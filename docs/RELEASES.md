# Releases

Version, freeze, publish, in that order. The checklist is at the bottom; it is
written down because anything living only in someone's head gets skipped.

---

## v2 -- judged on opponents that exist, not archetypes we invented

The army barely changed and almost everything about how it was chosen did. v1
picked its champion on twelve hand-written archetypes. Measured against the
only three armies anyone has ever actually played against us, the champion with
the best archetype score turned out to be the one that cannot beat a real
opponent. So the decision instrument changed, and with it the target, and one
army was thrown out of the mixture for losing.

```
Army     | 11 bishops + 6 pawns, king f1   (fingerprint a052fbf65141eb70)
         | index 63 of the v4 campaign, NOT the solver's argmax
vs bot 2026-08-05 | 0.9684 +/- 0.0064   +594.55 Elo
vs bot 2026-08-08 | 0.9850 +/- 0.0045   +726.94 Elo
vs 13 bishops     | 0.6369 +/- 0.0098    +97.62 Elo   <- binding column
Worst    | 0.6369, the rule the army was chosen by
Games    | 800 per cell, 3 cells, plus 45 cells of screening behind them
Mixture  | 12 armies sampled from the equilibrium support, one removed by hand
Pool     | 90 setups, exploitability 0
Stable   | index 63 in the support at shrink 0, 4, 8, 16, 32, weight 0.1799
Referee  | fairy-stockfish 14.0.1, no piece ceiling
TC       | 20,000 nodes fixed, 15% per-game jitter
Bench    | 404,905 nodes (C move generator, cross-check only)
Machine  | Mac14,9 arm64, macOS
```

### What changed

- **The archetype gate was demoted to a screen.** v1's champion scores 0.9425
  against the archetypes, the best of any champion measured, and 0.4869 against
  the bot army we met over the board -- drawing 764 of 800 pairs. A field of
  guesses cannot rank armies against a field that exists.
- **The target is no longer the solver's argmax.** Index 63 dominates the
  argmax on all three real opponents, outside the interval on both bot armies.
  The equilibrium is solved over the pool, and the pool is not the opponent
  distribution. Weights still come from the solver; only the argmax is
  overridden.
- **One army was removed from the mixture for losing.** Index 83 held 9.1% of
  the weight and scores 0.4481 +/- 0.0209 against a real opponent, below 0.5 at
  95%. All 13 support members were screened to find it. This is not free and
  the cost is stated at `play.py:MIX`: dropping a support member breaks the
  equilibrium property over the pool.
- **One piece is worth 0.477.** A king sweep refuted its own prediction and
  isolated a single bishop, g3 against d1, worth 0.9641 versus 0.4869 with all
  16 other pieces and the king identical. A search over compositions cannot
  find that, and a pool that deduplicates by material would discard it.
- **`--seed-bot` is rejected.** Breeding against the observed bot armies
  produces a champion that is worse against them. Demonstrated twice, the
  second time from a single-piece change.
- **`expand.py --gate-pool`** gates a campaign on a supplied field instead of
  the archetypes. Archetypes remain the default.
- **`pool.fingerprint`** is order-independent. v1's published fingerprint was
  sha256 of the army in its stored order, so the same army serialised two ways
  fingerprinted differently -- which actually happened mid-campaign. v1's value
  is left as published; v2 on uses the new one.

### Known limits

- **The whole decision rests on FOUR opponents.** Two bot armies and two human
  armies. Every ranking in this release is a worst-case over those columns.
  That field is the binding uncertainty now, and no amount of compute widens it
  -- only playing more real games does. The fourth, a 15-pawn wall from a live
  rated game, was added after the notes above were written and scores 0.8912
  +/- 0.0107, so it confirms the ranking rather than changing it. The other
  three cells in the stats block predate it.
- **The shipped army's archetype gate is unmeasured.** The 0.9406 figure
  belongs to index 54. Index 63 has never been played against the archetypes.
- **Only 6 of 13 support members are confirmed** against real opponents. The
  other seven screened at 0.6088 or below and were never re-run.
- **The top three armies are one number.** Worst columns 0.6378, 0.6372,
  0.6369. Screen-to-confirmation drift measures +/-0.012 on that column, wider
  than every gap. The switch to 63 rests on dominance, not on the worst column.
- **Removing index 83 was an edit, not a fix.** The fix is a campaign re-solved
  with the real opponents pinned as columns. Not done.
- **Re-runs are not bit-identical.** `arena.py` never sends `ucinewgame`, so
  the engine hash carries across games in a worker. Variance, not bias.
  Deliberately unchanged: fixing it would make every number here incomparable
  to anything measured after.
- Carried over unfixed from v1: `--optionality` is broken, `expand.py` stalls
  intermittently at a multiprocessing teardown, one engine survives a hard kill
  sometimes, and drafting variance is unmeasured.

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
- **Seeding an opponent into the pool is not a free hedge.** A v4 campaign bred
  against both observed bot armies produced a champion that is *worse* against
  one of them (`-0.0728 +/- 0.0123`, 800 pairs) than the v1 champion which
  never saw them. Same material, different king square. `docs/MEASUREMENTS.md`.
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
5. **Confirm the shipped army survives the noise correction.** Solve again at
   `adapt.shrink_cells(..., k)` for k in 0, 4, 8, 16, 32. Through v1 this meant
   "does the argmax change", because the argmax was what shipped. From v2 the
   shipped army is chosen on the real-opponent grid instead, so the test is
   **does it stay in the support with a stable weight** -- an army that drops
   out under shrinkage was a noise artifact whatever grid picked it. Pinned by
   `selftest.py`. v1 is stable as an argmax; v2 holds weight 0.1799 at every k.
6. **Judge the army on opponents that exist.** The twelve archetypes are a
   screen and cannot rank armies: the best archetype score in this repo belongs
   to the one champion that loses to a real opponent. Run the shipped army
   against everything in `campaigns/pool_real_opponents.json` and take the
   worst column. Where the worst column cannot separate two armies -- it is
   currently a +/-0.012 instrument -- prefer the one that dominates.
7. **Screen every army the drafter can actually play, not just the target.**
   With `MIX` on, the mixture is what ships. One support member was losing to a
   real opponent for an entire release before anyone checked.
8. Write the notes: headline, fixed-field stats block, what changed, known
   limits. Prose-only notes are not notes.
9. Name what is still owed. A known-limits section that admits the unmeasured
   case is worth more than one claiming completeness.
