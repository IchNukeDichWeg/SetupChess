# Releases

Version, freeze, publish, in that order. The checklist is at the bottom; it is
written down because anything living only in someone's head gets skipped.

---

## v1 -- eleven bishops and six pawns, and an honest account of how far to
trust that

The first coherent state: every claim measured on one instrument, with the army
that actually ships. Also the first version to say plainly what it does not
know, which is more than the error bars suggest.

```
Army     | 11 bishops + 6 pawns, king b1   (fingerprint ec385f649cf8af3e)
Gate     | 0.9038 +/- 0.0146, +389.06 [+361.65, +420.48] vs 12 archetypes
SPRT     | [0,4] LLR +41.444 -> ACCEPT H1, 800 games, 0 unplayable
Pool     | 94 setups, exploitability 0, seeded with a real opponent's army
Stable   | champion unchanged at shrink 0, 4, 8, 16, 32; support 7 armies
Referee  | fairy-stockfish 14.0.1, no piece ceiling
TC       | 20,000 nodes fixed, 15% per-game jitter
Bench    | 404,905 nodes (C move generator, cross-check only)
Machine  | Mac14,9 arm64, macOS
```

### What changed

- **The referee.** Vanilla Stockfish segfaults above 32 pieces, so every
  earlier campaign silently dropped its high-piece-count cells. Two results
  were artifacts of that hole: the wrong bishop army was crowned, and
  re-targeting looked worth four times what it is. `fairy-stockfish` plays up
  to the 48-piece maximum.
- **The chess phase does not start with White.** A finished side passes, so
  turns keep alternating and whoever follows the last placement moves first.
  Verified against a live game, byte for byte. It had changed the outcome of
  494 of 1,187 pairs in one measurement.
- **The pool is seeded with an army a human actually played** -- thirteen
  bishops, which the search never produced on its own and which beat the
  previous champion by 21 Elo.
- **`adapt.py`**, which measures what the rest of the repo structurally cannot:
  what happens when the opponent learns you.
- **Two numbers corrected after publication**, both from taking an extreme of a
  noisy sample. See `docs/MEASUREMENTS.md`.

### Known limits

- **Every opponent is one we built, and the bot model is half wrong.** Two live
  games show the bot buying THREE QUEENS each time, where the model predicts 16
  pawns and 7 knights. Its king clause is confirmed; its piece preference is
  refuted. Every `--opponent bot` number in this repo describes the model, not
  the bot. The observed armies are now in `pool.BOT_OBSERVED`.
- **The pool is a sample, not a cover.** Exploitability 0 means no army *in the
  pool* beats the mix. The first outside army anyone tried beat the champion.
- **Mixing is on by a judgement, not a measurement.** A pure strategy is solved
  after one game and pinned forever (`adapt.py`), so the drafter now samples the
  equilibrium support. No harness here can score that choice: they all assume a
  fixed field, where mixing costs about 32 Elo. `--no-mix` reverts it.
- **`--optionality` is broken**, not rejected. Its only measurement, -79 Elo,
  was of a bug in four unconstrained placement paths.
- **`expand.py` stalls intermittently** at a multiprocessing teardown. Cause
  unknown after three wrong diagnoses. `watchdog.py` makes it cost a restart.
- **One engine survives a hard kill, sometimes.** Also unexplained.
- **Drafting variance is unmeasured** everywhere the drafters are
  deterministic: those intervals carry chess-phase noise only.
- **The 19-setup pool's +17.13 re-targeting figure predates the handoff fix**
  and has never been re-run.

---

## Checklist

1. Run `python3 selftest.py` and confirm the bench oracle is unchanged, or that
   the change is intended.
2. Confirm `git status` is clean and everything is pushed.
3. Freeze the champion: its JSON is committed under `campaigns/`, and its
   fingerprint goes in the notes above so a later version can be compared
   against exactly what shipped.
4. Confirm `DEFAULT_POOL` and `DEFAULT_TARGET` point at the frozen campaign and
   that the target is a member of the pool -- re-targeting abandons a target
   that is not.
5. **Confirm the champion survives the noise correction.** It is the argmax of
   equilibrium weights over cells backed by a handful of pairs, which is the
   same extreme-over-noise that produced two wrong numbers in this repo. Solve
   again at `adapt.shrink_cells(..., k)` for k in 0, 8, 32; if the winner
   changes, the choice was luck and the campaign needs more pairs per cell.
   Pinned by `selftest.py`, and v1 is stable across all three.
6. Write the notes: headline, fixed-field stats block, what changed, known
   limits. Prose-only notes are not notes.
7. Name what is still owed. A known-limits section that admits the unmeasured
   case is worth more than one claiming completeness.
