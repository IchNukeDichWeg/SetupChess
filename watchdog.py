"""Restart a resumable job when it stops making progress.

Built for expand.py, which stalls at a `with mp.Pool(...)` teardown: the parent
blocks joining a handler thread, sitting at 0% CPU with every worker and engine
already gone. Observed twice on a live campaign, once after the seeding and
once after a screen, each time an hour of wall clock lost to a process that had
already finished its work and only needed killing.

The stall is invisible to an exit code -- the process is alive and healthy by
every signal except that it has stopped printing. So that is what is watched:
silence on stdout for --silence seconds means stalled.

This does NOT fix the hang. It makes a hang cost one restart instead of a
night. The job must be resumable and must be re-runnable with the identical
command, which is exactly what expand.py's state file provides.

    python3 watchdog.py -- python3 expand.py --state campaigns/x.json ...

Ctrl-C stops the watchdog AND the job; it is not treated as a stall.
"""

import argparse
import os
import signal
import subprocess
import sys
import threading
import time

# How long stdout may be silent before the job counts as stalled. expand.py
# prints every 25 pairs, which at the slowest rate measured on a 40-piece pool
# (0.64 pairs/s) is about 40 seconds, so five minutes is roughly seven times
# the worst observed gap. Too low and a genuinely slow tranche gets killed.
DEFAULT_SILENCE = 300.0

# A restart that produces no output at all is not progress; after this many in
# a row the job is broken rather than stalled and the watchdog gives up rather
# than looping on it forever.
MAX_FRUITLESS = 3

# Time a killed job gets to checkpoint on SIGINT before SIGKILL. expand.py's
# handler saves and exits immediately, so this is generous.
GRACE = 15.0


def child_pids(parents):
    """Direct children of `parents`, via ps. Empty on any failure.

    Used to sweep the job's engines after it is killed: once the parent is
    gone the link is lost, so they are recorded while it is still alive.
    """
    want = set(parents)
    try:
        out = subprocess.run(["ps", "-eo", "pid=,ppid="], capture_output=True,
                             text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    kids = []
    for row in out.splitlines():
        parts = row.split()
        if len(parts) != 2:
            continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        if ppid in want:
            kids.append(pid)
    return kids


def descendants(root):
    """Every process under `root`, breadth first. Only ever our own job."""
    seen, frontier = [], [root]
    while frontier:
        kids = child_pids(frontier)
        kids = [k for k in kids if k not in seen and k != root]
        if not kids:
            break
        seen.extend(kids)
        frontier = kids
    return seen


def stop(proc, reason):
    """SIGINT so the job checkpoints, then SIGKILL, then sweep its children."""
    doomed = descendants(proc.pid)
    print("\n[watchdog] %s -- stopping pid %d (%d descendants)"
          % (reason, proc.pid, len(doomed)), flush=True)
    try:
        proc.send_signal(signal.SIGINT)
    except OSError:
        pass
    # A SECOND CTRL-C MUST NOT ESCAPE THIS. The wait was unguarded and stop()
    # is called from run_once's `except KeyboardInterrupt`, so a second
    # interrupt during the 15s grace window propagated straight out and skipped
    # everything below: no proc.kill(), no wait, no descendant sweep. Measured
    # against a job that ignores the first SIGINT, two Ctrl-Cs 1.0s apart left
    # the job parent AND its grandchild alive while the watchdog exited 130,
    # contradicting the docstring's "Ctrl-C stops the watchdog AND the job".
    # Pressing it again after 15 quiet seconds is the natural reaction.
    deadline = time.time() + GRACE
    while time.time() < deadline and proc.poll() is None:
        try:
            time.sleep(0.2)
        except KeyboardInterrupt:
            break
    if proc.poll() is None:
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
    swept = 0
    for pid in doomed:
        try:
            os.kill(pid, signal.SIGKILL)
            swept += 1
        except (ProcessLookupError, PermissionError, OSError):
            pass
    if swept:
        print("[watchdog] swept %d leftover process(es)" % swept, flush=True)


def run_once(command, silence):
    """Run `command` until it exits or goes quiet. Returns (code, saw_output).

    code is the job's exit status, or None if the watchdog stopped it.
    """
    # PYTHONUNBUFFERED, because silence on this pipe is the ONLY stall signal
    # and Popen(stdout=PIPE) makes a Python child block-buffer at 128 KB. Any
    # job whose progress prints are not explicitly flushed then looks stalled
    # while it is working perfectly: measured, a child printing an unflushed
    # line every 1.5s under --silence 4 was killed at 4s, restarted twice and
    # declared broken. The same child with PYTHONUNBUFFERED=1 exits 0.
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    proc = subprocess.Popen(command, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1,
                            env=env)
    last = [time.time()]
    saw = [False]

    def pump():
        for line in proc.stdout:
            last[0] = time.time()
            saw[0] = True
            sys.stdout.write(line)
            sys.stdout.flush()

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    try:
        while True:
            code = proc.poll()
            if code is not None:
                reader.join(timeout=2)
                return code, saw[0]
            if time.time() - last[0] > silence:
                # Read `saw` BEFORE stopping. Killing the job makes it print --
                # a KeyboardInterrupt traceback lands on the merged stream --
                # and counting that as progress means a job that never produces
                # anything of its own restarts forever. Measured: it ran to
                # --max-restarts 100 instead of giving up after 3.
                stalled_saw = saw[0]
                stop(proc, "no output for %.0fs" % silence)
                return None, stalled_saw
            time.sleep(0.5)
    except KeyboardInterrupt:
        stop(proc, "interrupted by you")
        raise


def main_for_test(command, silence=DEFAULT_SILENCE, max_restarts=100):
    """The restart loop, callable directly. Same code path as main()."""
    restarts = fruitless = 0
    started = time.time()
    while True:
        code, saw = run_once(command, silence)
        if code == 0:
            print("[watchdog] job finished cleanly after %d restart(s), %.1f min"
                  % (restarts, (time.time() - started) / 60.0), flush=True)
            return 0
        if code is not None:
            print("[watchdog] job exited with %d; not restarting" % code,
                  flush=True)
            return code
        fruitless = 0 if saw else fruitless + 1
        if fruitless >= MAX_FRUITLESS:
            print("[watchdog] %d consecutive runs produced no output at "
                  "all (%d of them restarts); this is broken, not stalled"
                  % (fruitless, max(0, fruitless - 1)), flush=True)
            return 1
        restarts += 1
        if restarts > max_restarts:
            print("[watchdog] hit --max-restarts %d" % max_restarts, flush=True)
            return 1
        print("[watchdog] restart %d, re-running the same command" % restarts,
              flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--silence", type=float, default=DEFAULT_SILENCE,
                    help="seconds of stdout silence that counts as a stall "
                         "(default: %(default)s)")
    ap.add_argument("--max-restarts", type=int, default=100,
                    help="give up after this many restarts (default: %(default)s)")
    ap.add_argument("command", nargs=argparse.REMAINDER,
                    help="-- followed by the command to run")
    args = ap.parse_args()

    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        sys.exit("nothing to run: put the command after --")

    return main_for_test(command, args.silence, args.max_restarts)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
