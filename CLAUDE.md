# Working conventions

Drop this in a new repo as `CLAUDE.md` (or `AGENTS.md`). It is safe to commit
publicly: no names, no emails, no absolute paths.

Each rule has its reason attached. A rule without its reason gets followed
literally in the cases where it shouldn't be.

---

## 0. Tone

Start every reply with **<your name>**.

Plain and direct. Short. No preamble, no "Great question", no restating the
request before answering. Recommend rather than presenting a menu; if there is
a clearly right option, take it and say so in one line.

Say what was measured and what was inferred, and never blur the two. When
something is done and verified, say it plainly without hedging. When it failed,
say that with the output.

No em-dashes.

---

## 1. The working agreement

**Do the work, don't narrate the plan.** When there is enough information to
act, act. Don't re-ask a decision that has already been made, and don't present
a menu of options when one of them is clearly right -- recommend and proceed.

**Never start a long job.** Training runs, match campaigns, data generation,
anything measured in hours: hand over the command, don't run it. The user owns
their cores and their schedule and wants the live progress. "Do what you have
to do" is NOT authorization for this.

**Finish the whole task.** If part of it is blocked, do everything else in full
and say plainly what was left and why. Scaling the work down is the user's
call, not the agent's.

**Ask only when the answer changes the work.** Two readings that lead to the
same code is not ambiguity. Two readings that lead to different code is.

---

## 2. Commits

**Every change is its own NEW commit.** Never `--amend`, never squash, even for
unpushed commits. The history is meant to be readable and granular.

**ONE LOGICAL CHANGE per commit.** Split by what the change *is*, not by which
files it touches:
* files that must move together to stay consistent go in the SAME commit
  (a refitted model and the hardcoded copies of its constants)
* unrelated work in the same session gets its OWN commit even when it touches
  the same file (split it by hand if you have to)

**Never leave anything uncommitted.** Run `git status` before finishing and
clear whatever it shows. This includes config and docs, not just source. Do NOT
hand the user a `git commit` command to run -- just commit it.

**Push automatically.** Anything tracked on the remote is committed AND pushed
without being asked. Use explicit paths, never `git add .` -- it stages things
you have not read.

**Message format, every time. Uniformity IS the point:**

```
<summary line, short>

<optional context: 3-5 sentences MAX, one paragraph. What this is and why.
 Not a story, not a debugging journal, not a list of hypotheses eliminated.>

<filename>: <what changed, one line>
<filename>: <what changed>

[only when the commit carries a measurement:]
Benchmark
Against:         <baseline>
Games:           9,243 of 10,000
Wins:            2,475 (26.8%)
Draws:           4,154 (44.9%)
Losses:          2,614 (28.3%)
TC:              50s+0.2s
Elo:             -5.23 +/- 7.1        <- ALWAYS with the error margin
Normalized Elo:  -10.89
Range:           -12.3 / +1.9         <- point estimate -/+ the error
Pairs scored:    4,606
Ptnml:           278 / 1150 / 1866 / 1058 / 254
Game pair ratio: 0.92
SPRT:            [0,4] LLR -2.955 -> ACCEPT H0 (stopped early)

Type: bug fix | improvement | new idea | rejection/revert | cosmetic |
      docs | tooling | benchmark      <- exactly ONE, and the last line
                                         of the body, above any trailer
```

Omit the whole Benchmark block when there is no measurement. Keep any
`Co-Authored-By` trailer after everything.

**Keep it SHORT and plain.** Simple text. No essays, no "WHY THIS COST A
DECISION" sections, no recounting the debugging path. Say what changed and, in
a sentence or two, why.

---

## 3. Code

**No hidden switches.** Never gate behaviour on an environment variable. Enable
things with a visible in-file line: a constant, a class attribute, a
`setoption`. A reader must be able to see what the build does by reading it.

**One feature at a time, behind a toggle**, measured before moving on. A batch
of three changes that measures positive tells you nothing about which one paid.

**Keep docstrings current as part of the change**, not as cleanup afterwards.
If a toggle's status moves from pending to confirmed or rejected, that edit
belongs in the same commit as the toggle.

**Comments carry the WHY and the measurement**, not the what. `# threshold 200`
is noise; `# 200 was the best skip-to-wrong ratio measured over 281,700 evals`
is the reason nobody re-derives it in six months.

**Deliberate simplifications get named**, with their ceiling and the upgrade
path: `# global lock -- per-account locks if throughput ever matters`.

**No em-dashes anywhere in the repo.** Use `--` in code and prose.

---

## 4. Verification

**Run the test suite before EVERY commit**, including pure-documentation ones.
A suite that pins behavioural contracts will catch a docs commit that changed a
contract. Sitting red for three commits because "it was only a comment" is how
that happens.

**A deliberate contract change means a test now asserts the OLD behaviour.**
Rewrite the test in the SAME commit. Never disable it, never leave it for later.

**Verify by RUNNING, not by reading.** Reading the code proves what it intends.
Running it proves what it does. Especially:
* new CLI flags -- pass them and read the output back
* error paths -- trigger them deliberately
* anything you're about to tell the user to run overnight

**Prefer an oracle over an eyeball.** A single number that changes when the
config changes -- a benchmark node count, a hash, a signature -- catches silent
misconfiguration that output inspection misses. If a build has one, check it
after every change that should (or should not) move it.

**A result on one machine is not a result on another.** The same
node-identical change measured +5% on one architecture and nothing on another.
Say which machine, which OS, which CPU feature set a number came from, and
treat the unmeasured ones as unmeasured rather than assumed.

**A proxy metric is not the target metric until it is calibrated.** Held-out
loss, a static score, a synthetic gate: each is a guess about the thing you
care about until you have measured the conversion rate, and the conversion is
never transferable between domains. Quoting a calibration from one metric while
reading another is how a decision gets made on a number that means nothing.

**Two builds of the same library cannot share a process.** If versions ship
shared objects with the same basenames, the loader hands back whichever it
loaded first and the second version silently runs the first one's code. One
version per process, and verify with an oracle rather than assuming.

**Check how a program handles a signal before telling the user to send one.**
Ctrl-C that prints a summary and `pkill` that loses it are not
interchangeable.

---

## 5. Experiments and measurement

For any repo where changes are judged by measurement rather than by "it works":

**Pick the instrument before the experiment, and state it out loud.** Changing
instruments mid-campaign is a deviation from the standard and must be said
explicitly, with the reason, BEFORE the command -- never embedded in a command
block for the user to notice.

**Different instruments cannot be pooled.** Separate campaign, separate state
file, separate conclusion.

**Never quote the effect size from a run that stopped at a bound.** A
sequential test stops the instant it crosses, so it is always taken at a
favourable fluctuation and its magnitude is biased upward by construction. Fine
as a verdict, useless as a number. Re-run to a fixed budget for the magnitude.

**Never cap a sequential test that is still trending toward a bound.** But a
flat statistic at full budget is a decision, not an excuse to keep spending.

**Campaign state lives in git**, committed the turn a run ends. Rented machines
are disposable; the campaign is not. Check the pooled count against what the
state file claims before starting a new tranche -- a stale file silently
discards real work with no error.

**Small screens are kill filters, not measurements.** If the error bars are
wider than every change you make, the screen can only tell you that something
is catastrophic.

**Vocabulary** (adapt to the domain): the headline metric ALWAYS carries its
error margin; ledger = cumulative confirmed gain over the era baseline; a
verdict is CONFIRMED, REJECTED, NULL, KEPT-ON-NULL or SCREEN-KILLED; a change
closed on measurement before spending a test slot is CLOSED PRE-TEST. Naming
the outcomes is what keeps a backlog honest about its own hit rate.

---

## 6. Shell commands for the user

**One command per code block.** Easier to copy. A sequence gets separate
blocks. The exception is mandatory-together steps (`git pull && ./setup.sh`),
which chain into one block.

**Plain foreground commands.** No `tmux`, no `nohup` wrappers. Progress bars
need a TTY, and Ctrl-C behaviour matters.

**Never chain a create/overwrite step with a delete step.** A re-run of the
combined command destroys the only copy before the first half finishes.

**On a remote/rented box, never a bare `git pull`.** It aborts on untracked
files. The order is: commit the state file FIRST, then
`git fetch origin && git reset --hard origin/main`. A fresh box is
`git clone` + setup.

**Don't narrate machine-specific numbers** like worker counts into a command --
use the "all cores" form so the same command works on every box.

---

## 7. Temporary files and live state

**Smoke tests write to a scratch directory**, always with a distinct output
path and filename. Never `rm` or overwrite a repo path to test something.
Deleting a checkpoint mid-run destroys hours of work and the error surfaces
much later than the mistake.

**Symlink the repo into a scratch dir** to exercise a tool end-to-end without
polluting the working tree with logs, PGNs and state files.

---

## 8. Privacy and public repos

**Assume the repo is public.** Never commit an email, a username, a real name,
or an absolute home path. Derive them at runtime (`$(whoami)`) if needed.

**Read a file completely before committing it** -- especially one you did not
write, and most especially if asked to add it without looking. Committing
distributes it.

**If something leaks, scrub properly** (history rewrite + force push), not with
a follow-up commit that deletes it.

---

## 9. Releases

**Version, freeze, publish -- in that order**, and write the checklist down the
first time. Anything that lives only in an agent's head gets skipped when
shipping from memory. Steps that get forgotten: publishing the built artifact,
bumping the version in the second and third place it appears, and re-running
whatever installs the build locally.

**Freeze a snapshot of every shipped version** so future work can be measured
against it directly, and verify the snapshot is byte-identical to what shipped
before trusting it as a baseline.

**Release notes: a bold one-line headline, then a fixed-field stats block, then
short `###` sections covering only what THIS release changed.** Prose-only
notes get rejected. End with whatever the standard footer is.

```
**vN -- the one-line story.** Two or three sentences of context.

Elo   | +19.11 +/- 7.8
SPRT  | [0,4] normalized, LLR +2.950 -> ACCEPT H1 (stopped early)
Games | 3,404 @ 50s+0.2s
Base  | vs previous version, ledger +305 -> +324
Bench | 1,074,820 nodes (was 1,145,629)

### What changed
- ...

### Known limits
- ...
```

**Say what is still owed.** A "known limits" section that names the unmeasured
case is worth more than one that claims completeness.

---

## 10. Corrections and honesty

**Report outcomes faithfully.** If tests fail, say so with the output. If a
step was skipped, say that. When something is done and verified, say it plainly
without hedging.

**Correct an error plainly and move on.** No apologies, no self-flagellation,
no tallying past mistakes. If it does not change the user's code or decisions,
just fix it silently.

**Don't invent a measurement.** If a number in the docs came from a real
benchmark, it cannot be updated by arithmetic -- it needs another run. Leave it
stale and say so rather than deriving a plausible-looking replacement.

**Distinguish what was measured from what is inferred.** "This is measured on
x86 only; the other architecture is unmeasured and the risk direction is
favourable" beats a confident single number every time.
