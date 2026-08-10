# Every measured claim, and what it is worth

The full history behind the constants in `play.py`. It lived there as comments
until the preamble reached 145 lines before the first constant, at which point
it stopped being a comment and became a changelog. The code now carries the
current number and a pointer here.

Every figure is Elo with its interval. Read the caveat attached to each one:
several are on superseded instruments and two were wrong when first recorded.

---

## The referee, which invalidated more than anything else

Vanilla Stockfish segfaults above 32 pieces. Measured on this build: 36 pieces
survive, 38 crash; the 42-piece pawn-wall mirror answers `depth 4` and dies at
20,000 nodes; the 40-piece champion-versus-bot matchup exits `-11`.

So every campaign built with it silently dropped its **high-piece-count cells**,
and dense armies are what a pawn wall loses to. Two results were artifacts:

| claim | on the censored matrix | uncensored |
|---|---|---|
| which bishop army wins | 12 bishops + 3 pawns | 11 bishops + 6 pawns, **+112 Elo** better |
| re-targeting is worth | +24.63 Elo | **+6.71 Elo** |

`fairy-stockfish` 14.0.1 plays all of it, up to the 48-piece maximum, defaults
to `UCI_Variant chess`, and agreed with vanilla Stockfish on three
forced-tactic oracles. **Use `--engine fairy-stockfish --max-pieces 0`
everywhere.**

## Best-response re-targeting: CONFIRMED, small

| pool | referee | Elo | pairs |
|---|---|---|---|
| 19 setups | Stockfish | +17.13 [+12.36, +21.91] | 1,522 |
| 87 setups | Stockfish | +29.63 [+23.46, +35.83] | 1,187 |
| 87, post handoff fix | Stockfish | +24.63 [+18.06, +31.22] | 1,187 |
| 87, at 200k nodes | Stockfish | +29.93 [+23.66, +36.21] | 1,187 |
| **19 setups, uncensored** | **fairy-stockfish** | **+8.57 [+5.31, +11.83]** | **3,000** |
| **60 setups, uncensored** | **fairy-stockfish** | **+6.71 [+2.57, +10.85]** | **6,500** |

The two bold rows are the honest ones. The Stockfish rows are on the censored
matrix and are not a fair before-and-after with them.

### "Re-targeting grows with the pool" is withdrawn

On the censored matrix the effect looked like it scaled: +17.13 on 19 setups,
+29.63 on 87, described at the time as "nearly double, which is the effect you
would hope for". On the clean instrument the two pools give **+8.57** and
**+6.71**, intervals overlapping, and the smaller pool is if anything ahead.

The scaling story was part of the censoring artifact. A bigger pool has more
high-piece-count members, so a bigger pool lost more cells to Stockfish's
ceiling, so its best response was chosen from a more selectively-sampled set.

Recorded because a prediction was made before this run and it was wrong: the
smaller pool was expected to score lower, on exactly the reasoning above. It
did not.

The magnitude is quotable because the run went to a **fixed 6,500-pair budget**
rather than halting when the statistic crossed. The budget was raised twice
after looking at the trend and the estimate wandered `+7.53 -> +5.91 -> +6.71`
rather than climbing toward the bound.

Effective sample is far below the headline: **2,988 of 6,500 pairs came out
identical**, because the drafter only re-targets in some games. That is why this
needed a budget the other measurements did not.

## The handoff turn-order fix

`handoff_fen()` hardcoded White to move. A full setup phase relayed against
chess.com's own bot showed otherwise: a finished side **passes** (the server
writes `P`), so turns keep alternating and whoever follows the final placement
moves first. It changed the outcome of **494 of 1,187 pairs**.

`arena.py` never shared the problem; it goes through `setup_fen()`.

## Depth is not what carries the champion

Current champion against the twelve archetypes, `fairy-stockfish`, no ceiling:

```
20k    0.9349 +/- 0.0127   +462.86 [+429.53, +502.90]
200k   0.9500 +/- 0.0107   +511.50 [+475.97, +555.09]
```

Paired against the previous champion on identical cells: `+0.0255 +/- 0.0156`
at 20k, `+0.0203 +/- 0.0122` at 200k. Both exclude zero, so the head-to-head
advantage **does** transfer to the archetype field.

It does **not** transfer to the modelled opponent, where the two champions are
indistinguishable (0.9400/0.9425 against 0.9200/0.9400). Same two armies, two
opponents, two answers.

## The pool is a sample, not a cover

A real game produced **13 bishops, no pawns** — an army the loop never bred.
It beat the then-champion by **-20.87 Elo [-28.51, -13.25]**.

Seeded into a fresh campaign with `--seed-army`, the loop found answers:
**+7.60 [+3.76, +11.45]**, a 28 Elo swing. The two champions are
indistinguishable head to head (`-1.74 [-11.43, +7.96]`), but the hedge cost
archetype performance: the gate fell **0.9425 → 0.9038**.

Exploitability 0 means no army **in the pool** beats the mix. That is a much
weaker claim than it reads as.

## Breeding against the real opponent, not the modelled one

The v4 campaign is the first bred against the armies chess.com's bot has
actually been observed playing (`pool.BOT_OBSERVED`, both queen-heavy) rather
than `BOT_WALL`, the refuted 16-pawn model. It also carries the 13-bishop army
a human played, so its field contains **every real opponent ever observed**.

```
campaign   seeded with                        gate vs the 12 archetypes
v2         nothing                            0.9425 +/- 0.0116
v3         13 bishops                         0.9038 +/- 0.0146
v4         13 bishops + 2 real bot armies     0.9406 +/- 0.0117
```

All three rows are `fairy-stockfish`, `max_pieces 0`, 800 games, and each
reproduces from its own gate file with `stats.report`. **The campaign named v2
here is `campaigns/champion_fsf.json`, not `campaigns/champion_v2.json`** --
the latter is an older stockfish-era army (3P 9B 1Q, king e1) whose gate is on
the censored referee and cannot be compared to these. The filenames predate the
vN vocabulary and were never renamed.

v3 paid for its hedge; **v4 did not.** Its interval does not overlap v3's, so
adding the real opponents on top of the 13-bishop army recovered the archetype
performance that seeding 13 bishops alone had cost. Why is not established:
v4 also filled in 11 rounds against v3's 14, so pool composition differs in
more than one way.

**The bot's armies are not in the equilibrium support**, in either campaign.
The loop answers them rather than adopting them, which is what an opponent
model should do.

### `--seed-bot` is REJECTED, and the control is why

The gate above is against the archetypes. Against the bot armies themselves,
both champions played the same two matchups, 800 pairs each, same instrument:

| opponent | v3, never bred against it | v4, bred against it | difference |
|---|---|---|---|
| 2026-08-05, 4P 1B 1R 3Q | **0.9734 +/- 0.0055** | 0.9006 +/- 0.0110 | **-0.0728 +/- 0.0123** |
| 2026-08-08, 3P 2N 1B 3Q | **0.9641 +/- 0.0064** | 0.9569 +/- 0.0071 | -0.0072 +/- 0.0096 |

Seeding the opponent made us **worse against that opponent** on one army and
no better on the other. The first difference excludes zero; the second does
not. Truncation was 0.2%/0.4% for v3 and 1.9%/0.8% for v4, so the scores are
chess, not the ply limit.

The prediction on record before the run was parity, on the reasoning that the
bot's armies never entered the support so the loop had found them easy. Half
right: the loop did ignore them. What it did not do was leave the champion
alone.

Both champions are **11 bishops + 6 pawns**. The only difference is the king,
b1 for v3 and f1 for v4 -- and f1 is the same square the best response to v3
uses (see "Being predictable"). v4 crowned its own counter-army: better
against a field of bishops, worse against queens.

So a seeded army is not a free hedge. It changes which cells the equilibrium
is solved over, and the champion can move to one that is worse against the
very army that was seeded. `DEFAULT_TARGET` stays on the v3 champion.

## The only three opponents that exist, and what ships

The gate above is twelve archetypes we wrote. Three armies have ever been
played against us for real: the bot's two, and thirteen bishops from a human.
Every champion against every one, 800 games a cell, `fairy-stockfish`, no
ceiling, 20k nodes with 15% jitter. Every cell's armies were fingerprint-checked
against the champion files before this table was built.

```
       king   bot 08-05   bot 08-08   13 bishops   worst    archetype gate
v2     e1     0.9172      0.4869      0.4700       0.4700   0.9425
v3     b1     0.9734      0.9641      0.5109       0.5109   0.9038
v4     f1     0.9006      0.9569      0.6247       0.6247   0.9406
```

Margins are +/- 0.0044 to +/- 0.0110, all 800 games. **`play.py` defaults to
v4**, on a rule fixed before the last three cells were run: take the best worst
column, not the best average and not the best gate. A gap between 0.90 and 0.97
against an army you beat either way is worth less than one between 0.51 and
0.62 against an army that is actually close.

Three things this says that the archetype gate could not:

**The archetype gate is a screen, not the decision.** v2 has the best gate of
the three and is the only champion that cannot beat a real opponent. Against
the bot army we actually met over the board it draws **764 of 800 pairs** at
0.4869. Truncation was 1.1%, so those are real draws -- a fortress, not an
unfinished game.

**Arrangement decides it, material does not.** All three champions are the same
11 bishops and 6 pawns. v2 and v4 differ by **two pieces and nothing else** --
the king moves e1 to f1, and one bishop moves d1 to g3:

```
                bot 08-05   bot 08-08   13 bishops
v2  Ke1, Bd1    0.9172      0.4869      0.4700
v4  Kf1, Bg3    0.9006      0.9569      0.6247
```

Two squares are worth 0.4869 -> 0.9569 against one fixed opponent. v3 is a
genuinely different arrangement, 8 of its 17 non-king pieces on other squares.

This is why a search over compositions is not enough, and why the pool has to
carry arrangements as distinct members rather than deduplicating on material.

`expand.py --gate-pool campaigns/pool_real_opponents.json` gates a campaign on
this field instead of the archetypes. The archetype gate stays the default,
because three armies are too few to breed against without overfitting -- which
is the same trap `--seed-bot` fell into, one section up.

**v4 ships despite `--seed-bot`, not because of it.** The rejection above
stands: v4 is worse against both bot armies than v3. What it buys is the
13-bishop column, +0.11. Why is not established -- v3 and v4 differ in seeds
*and* in round count (14 against 11), so this is not a clean attribution of the
gain to any one cause.

And the answer is still bishops. Four independent campaigns, two of them
seeded with armies specifically chosen to break bishops, all land on
**11 bishops + 6 pawns** at the top of the mix.

## One bishop is worth 0.477

A king-square sweep: all 17 non-king pieces of the v4 champion held fixed, the
king moved to every free square in its own zone, each against the bot's
2026-08-08 army, 800 games a square.

```
king  score vs bot2
e1    0.9641 +/- 0.0064   <- best
f1    0.9525 +/- 0.0076   (the shipped champion; oracle, see below)
d1    0.9400 +/- 0.0088
g1    0.9237 +/- 0.0090
h1    0.8997 +/- 0.0102
c2    0.8431 +/- 0.0130
h3    0.7844 +/- 0.0140   <- worst
```

**A prediction was registered before this ran and it was wrong.** The v2
champion collapses against this opponent (0.4869) and has its king on e1, so
e1 was predicted to score materially below the others. It scored best. The
king-square explanation of that collapse is REFUTED. The secondary prediction
held: both off-back-rank squares are the two worst.

Refuting it leaves exactly one variable, because the e1 variant and the v2
champion differ by **one piece**:

```
16 pieces identical, king e1 identical
Bg3   0.9641 +/- 0.0064
Bd1   0.4869 +/- 0.0044
```

Moving one bishop from g3 to d1 costs **0.477 of score**, roughly 470 Elo, on
the same instrument against the same opponent. That is the largest single
effect measured anywhere in this repo, and it is one piece on one square.

Consequences, in order of how much they should change what you do:

- A search over compositions cannot find this. Neither can a search that
  deduplicates armies by material. The pool must carry arrangements.
- The archetype gate cannot see it either: v2 has the best gate of the three
  champions and holds the losing bishop.
- Nothing here says ship the e1 variant. It is better than the shipped
  champion **against one opponent**, which is exactly the reasoning that made
  `--seed-bot` fail. It needs the full grid before it can displace anything.

### The e1 variant does not displace the champion

Judged on one column it looked like an upgrade. Judged on all three it is not:

```
column        v4 (shipped)        e1 variant          difference
bot 08-05     0.9006 +/- 0.0110   0.9116 +/- 0.0102   +0.0109 +/- 0.0150  includes zero
bot 08-08     0.9525 +/- 0.0076   0.9641 +/- 0.0064   +0.0116 +/- 0.0100  excludes zero
13 bishops    0.6247 +/- 0.0096   0.6028 +/- 0.0094   -0.0219 +/- 0.0134  excludes zero

worst         0.6247              0.6028              -> v4 KEEPS the default
```

It wins the column it was found on and loses the binding one, both outside
their intervals. **This is the second demonstration tonight that optimising on
an observed opponent picks a worse army**, the first being `--seed-bot`, and
this time the change was a single piece. The worst-case rule caught it.

The prediction registered before the run was half right: the variant did not
displace the champion, but the 13-bishop cell was predicted "near 0.5" and
came in at 0.6028, much nearer the champion than expected.

### Two instrument notes from this run

`h3` skipped 400 pairs as `opposite_check`, all of them the `(0,0)`
self-play diagonal, which is the P[i][i] sanity cell rather than the
measurement. Its two measurement cells are complete at 400 pairs each, so
0.7844 is a full 800-game result with one sanity check missing.

**Re-runs are not bit-identical.** The f1 variant is the shipped champion
against an opponent it had already played 800 games against, included
deliberately as an oracle. It returned 0.9525 where the earlier run returned
0.9569 -- inside both intervals, so the sweep is sound, but not the exact
repeat that identical pools and an identical seed would imply. Cause:
`arena.py` never sends `ucinewgame` and never clears the hash, so the engine's
transposition table carries across games inside a worker and the result
depends on which worker took which game. This adds variance, not bias, since
colours are swapped within every pair. Left alone deliberately: adding
`ucinewgame` would change the instrument and make every number already in this
file incomparable to anything measured after it.

## The mixture contains an army that loses

`MIX` is on, so `play.py` draws from the 13-army equilibrium support each game
rather than always playing the champion. Only the champion had ever been
measured against a real opponent. Screening all 13 against all three, 100 pairs
a cell (a KILL FILTER at about +/-0.02 on these, sized to separate a losing
army from a winning one, not to rank them):

```
idx  w      army             bot1    bot2    13b     worst
83   0.091  9P 10B 1K  Kf1   0.4412  0.8275  0.5750  0.4412  <- loses
72   0.020  9P 10B 1K  Kh2   0.9762  0.9888  0.5600  0.5600
69   0.009  6P 11B 1K  Kh1   0.9563  0.9425  0.5737  0.5737
51   0.007  9P 10B 1K  Kf1   0.9875  0.9825  0.5750  0.5750
43   0.162  6P 11B 1K  Kg1   0.9750  0.9750  0.6012  0.6012
76   0.046  6P 11B 1K  Ke1   0.9475  0.9825  0.6012  0.6012
71   0.008  9P 10B 1K  Kf1   0.8337  0.9450  0.6050  0.6050
60   0.015  6P 11B 1K  Kh1   0.9712  0.9650  0.6088  0.6088
54   0.273  6P 11B 1K  Kf1   0.9050  0.9663  0.6125  0.6125  <- champion
87   0.090  6P 11B 1K  Kf1   0.9663  0.9788  0.6262  0.6262
34   0.009  9P 10B 1K  Ke1   0.9637  0.9712  0.6275  0.6275
63   0.180  6P 11B 1K  Kf1   0.9762  0.9850  0.6325  0.6325
40   0.084  6P 11B 1K  Kf1   0.8425  0.9475  0.6375  0.6375
```

Oracle: idx 54 is the shipped champion, and the screen returns 0.9050 / 0.9663
/ 0.6125 against its 400-pair 0.9006 / 0.9525 / 0.6247. The screen is sound.

**Index 83 scores below 0.5 against a real opponent and the drafter plays it
9.1% of the time.** It earns that weight honestly: the equilibrium is solved
over the pool, and 83 is good against pool members. It is not good against
reality. This is "the pool is a sample, not a cover" arriving inside the
shipped mixture rather than outside it.

The prediction on record was half right. It named the `9P 10B` armies as the
suspects and the worst one is `9P 10B`, but `34` is also `9P 10B` and ranks
third best. It also predicted the casualty would appear against `bot2`; it
appeared against `bot1`, where nothing else fell below 0.82.

**Do not read the ranking off this table.** It is the max and min over 13
armies times 3 columns at +/-0.02, which is exactly the extreme-over-noise that
produced two wrong numbers elsewhere in this file. The screen licenses a
confirmation run and nothing else. In particular `40` at 0.6375 versus the
champion's 0.6125 is inside the noise and is a candidate, not a result.

## Being predictable

The champion against the pool's best response to it, 400 pairs:
**0.4566 +/- 0.0143, -30.26 Elo [-40.27, -20.30]**.

The counter is the **same composition on different squares**, king f1 instead
of b1. Arrangement matters as much as material.

`adapt.py` simulates repeated play against a learner: a pure strategy is solved
after **one game** and pinned forever; a mixture holds the equilibrium value.

## Two numbers that were wrong when first recorded

Both from the same mistake — taking a max or min over cells backed by four
pairs, where the extreme of a noisy sample is biased in its own favour.

| quoted | actual | how it was found |
|---|---|---|
| being predictable costs **-112.3 Elo** | **-30.26** | argmin over 94 columns |
| the reactive ceiling is **+143.4 Elo** | **+46.0** | max over rows, per column |

`adapt.py --shrink` corrects for it, calibrated against the one cell measured
at 400 pairs: `k=8` predicts it to within 0.009 where the raw matrix is off by
0.113.

**Averages over the matrix are unaffected** — the noise cancels rather than
being selected for. Only extremes are poisoned.

## The draws are real

A game stopped at `PLY_LIMIT` (300) scores 0.5, which is indistinguishable from
a genuine draw once it reaches the matrix. Several results here are very
draw-heavy -- the champion against 13 bishops drew **717 of 800 pairs** -- so
the obvious worry is that the draws are the limit rather than chess.

Measured on that exact matchup, which is the most drawish in the repo:
**5 of 160 games hit the limit, 3.1%**. The draws are real: threefold, the
fifty-move rule, and blocked positions two bishop armies cannot break.

`arena.py` now reports the truncation rate at the end of every run. It was
computing the ply counts all along and discarding them, so no run before this
one ever said.

## Rules verified live, not assumed

Played against chess.com's own board and client:

- a king may **not** be placed on an attacked square; every other piece may
- checkmate during setup ends the game (`@Qe1+#`)
- ending setup without a king loses outright, so covering every empty square in
  the opponent's zone wins without a checkmate
- a finished side **passes**, and the chess phase starts with whoever follows
  the last placement

Full sourcing in `docs/RULES.md`, opponent model in `docs/BOT_MODEL.md`.
