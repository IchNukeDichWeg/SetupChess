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

**Every row above was measured with opponent and colour CONFOUNDED.**
`match.py` picked the opponent as `k % len(field)` and the colour as `k % 2`,
so whenever the field size is even -- which it is for every shipped campaign
-- the two cycles lock and each opponent is played from exactly one colour.
Enumerated: **0 of 106 opponents on the v3 field and 0 of 84 on v6** were ever
seen from both sides. The drafters carry no RNG, so `k` and `k + len(field)`
draft the identical handoff FEN, and the 3,000-game re-targeting run sampled
106 distinct positions replayed 28 times each rather than 212. The effective
sample was half the headline and the colour balance was not a balance at all.
Fixed by advancing the colour once per full pass. **The re-targeting figures
are not re-measured**, so treat their intervals as optimistic.

**All of these are on `match.py`'s PAIRED HALF-SCALE.** It maps the paired
difference through `(d + 1) / 2`, so the reported Elo is of a half-sized edge
rather than the gap between the arms; measured over three simulated arm pairs
at 400k games each, the ratio is 0.495 / 0.500 / 0.499. So re-targeting's
**+6.71 paired is about +13.4 between the arms**, and the `[0,4]` SPRT band is
about `[0,8]` on that axis. Both quantities are legitimate; the defect was
printing one unlabelled number next to field-Elo figures. `match.py` now
prints both axes and names them. The verdict signs and the zero point were
never affected.

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
v2         nothing                            0.9425 +/- 0.0121
v3         13 bishops                         0.9038 +/- 0.0159
v4         13 bishops + 2 real bot armies     0.9406 +/- 0.0139
```

Margins are PER PAIR. The gate used to flatten each colour-swapped pair into
two independent games, which violates `stats.report`'s iid assumption in the
anti-conservative direction: the LLRs were inflated 19-40% (v3 +41.4 against
+34.9, v4 +70.2 against +50.1, v6 +55.1 against +44.8). The means are
unaffected and every shipped verdict still ACCEPTs H1 far past +2.944, so
nothing here changes except the width -- but a marginal future gate would have
accepted early. v4's interval still does not overlap v3's.

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

### Confirmed at 400 pairs, and the solver's argmax was not the best army

Index 83 confirms: **0.4481 +/- 0.0209** against the bot's 2026-08-05 army,
below 0.5 at 95%. It is removed from the sampled support in
`campaigns/champion_v4b.json`.

The two screen leaders were confirmed on all three columns, and both beat the
shipped champion's worst case, which the registered prediction said they would
not:

```
      bot 08-05           bot 08-08           13 bishops          worst
54    0.9006 (shipped)    0.9525 (shipped)    0.6247 (shipped)    0.6247
40    0.8431 +/- 0.0137   0.9434 +/- 0.0082   0.6372 +/- 0.0102   0.6372
63    0.9684 +/- 0.0064   0.9850 +/- 0.0045   0.6369 +/- 0.0098   0.6369
```

**Index 63 dominates index 54 on every column**, outside the interval on the
two bot armies and inside it on the 13-bishop column. Index 40 ties 63 on the
worst column and gives up 0.125 on `bot 08-05`, so the worst-column rule alone
cannot separate them and the tie goes to dominance. `DEFAULT_TARGET` is now 63.

The solver crowned 54 with weight 0.273 against 63's 0.180, because the
equilibrium is solved over the pool. **The argmax of a pool equilibrium is not
the best army against real opponents**, which is the same lesson as
`--seed-bot` and the 13-bishop seed, arriving this time inside the mixture.

### The remaining two, and where this ranking runs out of resolution

`87` and `34` closed at 400 pairs as well, so four of the thirteen support
members are now confirmed on all three columns:

```
      bot 08-05           bot 08-08           13 bishops          worst
63    0.9684 +/- 0.0064   0.9850 +/- 0.0045   0.6369 +/- 0.0098   0.6369  ships
87    0.9753 +/- 0.0054   0.9772 +/- 0.0060   0.6378 +/- 0.0116   0.6378
40    0.8431 +/- 0.0137   0.9434 +/- 0.0082   0.6372 +/- 0.0102   0.6372
34    0.9469 +/- 0.0082   0.9688 +/- 0.0068   0.6294 +/- 0.0110   0.6294
54    0.9006 +/- 0.0110   0.9525 +/- 0.0076   0.6247 +/- 0.0096   0.6247
```

**Nothing changes.** `87` has the highest worst column by **0.0009** against
margins of +/-0.011, which is a twelfth of the error bar, and it does not
dominate: it beats 63 on one bot army and loses on the other. Acting on that
gap is the extreme-over-noise mistake this file already records twice.

The top three worst columns are 0.6378, 0.6372 and 0.6369. **They are one
number.** The switch from 54 to 63 was justified on dominance -- 63 wins both
bot columns outside their intervals -- and NOT on the worst column, where
0.6369 against 0.6247 includes zero.

Screen-to-confirmation drift bounds what this budget can resolve. Measured over
four armies on the binding column: `-0.0003`, `+0.0044`, `+0.0116`, `+0.0019`.
So a 100-pair screen locates an army to about `+/-0.012` of its 400-pair value,
which is larger than every gap at the top of this table. Separating these would
need a different budget, not another confirmation round.

### Removing 83 is not free

Index 83 earned 9.1% honestly: the mixture is a Nash equilibrium **over the
pool**, so dropping a support member makes the mix exploitable by some pool
army. The trade taken is a measured loss against an opponent that exists for a
theoretical loss against one never seen. The principled fix is a campaign
re-solved with the real opponents pinned as columns, so the solver never gives
weight to an army that loses to them; that costs a campaign rather than an
edit. `expand.py --gate-pool` is half of it.

## A fourth real opponent: the wall a human actually played

Game #106991479, live and rated, opponent 1826. They drafted **15 pawns, 2
knights, a bishop, a rook and a queen** -- a full wall, 38 of 39 points.

That shape had been written off. `BOT_WALL` is the pawn-and-knight army
`docs/BOT_MODEL.md` predicted the BOT would build, and two live bot games
refuted it when the bot bought three queens instead. The conclusion drawn at
the time was that the wall was a modelling error. It was not: the model was
wrong about **who** plays a wall, not about whether anyone does.

The shipped champion against it, 400 pairs, same instrument:
**0.8912 +/- 0.0107, +365.43 Elo**, 1.8% truncation.

```
the champion's real-opponent grid, now four columns
bot 08-05     0.9684 +/- 0.0064
bot 08-08     0.9850 +/- 0.0045
13 bishops    0.6369 +/- 0.0098   <- still binding
human wall    0.8912 +/- 0.0107
worst         0.6369, unchanged
```

**The fourth column confirms rather than overturns.** The wall lands well
above the binding column, so every worst-case ranking on the three-column
grid stands and nothing needs re-running. A prediction was registered before
the run -- "above 0.90, because eleven bishops cut a static structure on the
diagonals" -- and it was 0.009 too high against a 0.011 margin. The direction
was right and the threshold was wrong.

### What the game itself is worth, which is less

The game was WON by checkmate, and that is close to meaningless as evidence.
The army actually played was 10 bishops + 9 pawns, not the champion's 11 + 6:
the drafter was driven by hand through a browser, one placement mis-clicked
because the piece bank RE-LAYS OUT as pieces become unaffordable and the
captured pixel coordinates went stale, and the rest was finished manually
under time pressure. One game, wrong army, no conclusion.

The transcript is the deliverable. `pool.HUMAN_OBSERVED` holds the wall and
`campaigns/pool_real_opponents.json` is now four armies rather than three.

## The v5 campaign measured nothing, and said it had converged

Run with the human armies seeded and the real-opponent field as the gate. It
stopped after two rounds having admitted **zero** challengers out of 47, then
reported "the pool has converged" and produced a gate number. All of that is
worthless, and the failure is worth more than the campaign would have been.

What happened: the support collapsed to **one army, the 13-bishop army, at
weight 1.0**. That is the most drawish army in this repo -- the shipped
champion draws 717 of 800 pairs against it. Admission needs a score strictly
above `--screen-margin 0.5`, so every challenger drew both its games, scored
**exactly 0.500**, and could never clear the bar. Zero admitted, twice, and
the loop counted that as two dry rounds and declared convergence.

```
round 0   24 challengers   0 admitted   best 0.500
round 1   23 challengers   0 admitted   best 0.500
gate      0.8275 +/- 0.0119 vs the 4 real opponents   <- this is just 13 bishops
```

The gate number is the 13-bishop army playing a field that contains itself. It
is not a champion and must not be quoted as one.

Two errors in the command, both avoidable by reading what the campaigns that
worked actually used:

| | v3 / v4 (worked) | v5 (failed) |
|---|---|---|
| `--screen-pairs` | 2 | 1 (the default) |
| seeded | `pool_13bishop.json`, which also holds a STRONG army | two human armies only |

The strong army is what kept 13 bishops from owning the support in v3 and v4.
Remove it and a fortress takes the whole equilibrium, because a draw-everything
army is a perfectly good strategy in a symmetric zero-sum game: value 0.5,
exploitability 0, nothing to improve.

**A converged double oracle and a blind screen look identical from the
outside.** `expand.screen_blind` now separates them and the loop prints a
warning naming both fixes; `selftest.py` pins it. Without that, the honest
reading of any "converged" verdict is UNMEASURED rather than settled.

## v6 ships, and the two decision rules disagree

The corrected campaign ran properly: 16 rounds, pool 72, support 9,
exploitability 0. Screening all nine support armies against the binding
column found two above the champion's 0.6369, and both confirmed at 400
pairs on all four real opponents:

```
             bot 08-05           bot 08-08           13 bishops          wall                worst
v4 champion  0.9684 +/- 0.0064   0.9850 +/- 0.0045   0.6369 +/- 0.0098   0.8912 +/- 0.0107   0.6369
v6 idx 57    0.9513 +/- 0.0078   0.9884 +/- 0.0037   0.6891 +/- 0.0106   0.8959 +/- 0.0105   0.6891  ships
v6 idx 33    0.9931 +/- 0.0030   0.9969 +/- 0.0019   0.6713 +/- 0.0110   0.9137 +/- 0.0098   0.6713
```

**The binding column moves 0.6369 -> 0.6891**, the first real progress on it
since v4 and the reason this campaign was worth running.

**Worst column and dominance point at different armies.** Index 33 DOMINATES
the old champion on all four and has the better mean (0.8938 against 0.8812);
index 57 has the higher floor but gives up 0.0171 on `bot 08-05`. The rule
registered before the run was worst column primary, dominance only as a
tiebreak, and 57 leads by 0.0178 against a combined margin of 0.0153 -- not a
tie, so the rule decides it and is not being re-chosen after the fact. What it
costs is stated rather than hidden: maximin buys the floor and pays in the
average.

**Index 33 is the first competitive non-bishop army in six campaigns**: 9
bishops, 6 pawns and 2 knights. Every champion before it was pure bishops
plus pawns.

Two predictions were registered and both were wrong. "Nothing will clear
0.6369 decisively, and 0.70 would be surprising" -- index 57 screened at
0.7075. Then "57 will likely drop on a bot column below 0.85" -- it dropped to
0.9513, a real regression but nowhere near the threshold.

### The mixture ships intact

All nine support armies were played against all four real opponents. Worst
columns: 0.5763, 0.6175, 0.6891, 0.5050, 0.5725, 0.6062, 0.6713, 0.6025,
0.5637. **None below 0.5**, so v6 needs no hand-removal and keeps its Nash
property over the pool -- unlike v4, which shipped with index 83 holding 9.1%
of the weight while scoring 0.4481 against a real opponent. Screening up front
costs nothing; removing afterwards cost the equilibrium.

## Three shipped bugs found by audit, and what they do and do not invalidate

### `adapt.py` never read its own required `--champion`

`--champion` is `required=True` and appeared nowhere after argparse: a
nonexistent path ran to completion. The pure curve took the highest
EQUILIBRIUM-WEIGHT row instead, which stopped being the shipped army the
moment armies started being chosen on the real-opponent grid rather than the
solver's argmax. On v6 the argmax is pool 46 and the shipped champion is 57.

Corrected number: **being predictable costs 14.7 Elo**, not the ~30 previously
reported, because the argmax row is more exposed than the army that ships.
`adapt.py` now resolves the champion's row, refuses a champion outside the
pool or dropped for holes, and prints both indices so the two can never be
confused again.

### Zero-variance samples reported `+inf` Elo and `CONTINUE`

`stats.report([1.0]*20)` printed `Elo: +inf [+3600.00, +3600.00]` and
`SPRT: LLR +0.000 -> CONTINUE`: the strongest possible evidence for H1 read as
no evidence at all, so a sequential test driving on that verdict never stops.
`elo_with_ci` clamped the bounds but not the mean, and `sprt_llr` returned 0.0
whenever the sample variance was zero. Both fixed; a 20-0 sweep now reports
`+3600.00` and `ACCEPT H1`, a 20-0 loss `ACCEPT H0`, and 20 draws still
`CONTINUE`, which is the one case where zero variance really does mean no
evidence.

## Two more shipped bugs, and what they do and do not invalidate

### `--mix` never mixed

`MIX` is documented in `play.py`, the README and all three release notes as
the anti-exploitability property this project is built around. It had never
fired. `_retarget` ran on the EMPTY board, where the opponent has revealed
nothing, `_opponent_weights` returns the uniform prior, and the best response
to a uniform prior is a **constant** -- so the sampled army was overwritten on
the very first placement. Measured on the shipped v6 pool over seeds 0-7:
**five distinct armies drawn, one built.**

Fixed: re-targeting waits until the opponent has revealed a placement.
Verified both ways -- 12 seeds now build 6 distinct armies, and re-targeting
still fires after six opponent pieces, so the +6.71 Elo feature is intact.

**What this invalidates.** Nothing measured. Every harness here scores against
a fixed field, where mixing is documented as costing about -32 Elo, so no
number in this file was produced with mixing live. What it invalidates is the
CLAIM, repeated in three releases, that the shipped drafter was unpredictable.
It was not; it played one army.

### Killed challengers' screen games were credited to admitted armies

`expand.py` carried screen cells into the pool matrix with only an
`i2 < len(armies)` guard. A killed challenger keeps its scratch index, the
pool has just grown by `len(admitted)`, so any killed index inside
`[first, first + len(admitted))` **aliases onto an admitted army's new row**.
The admitted challenger's own cell is then dropped by the already-occupied
test, and `cell_tasks` never re-plays an occupied cell. Nondeterministic on
top, because `scratch` is filled by `imap_unordered`.

Fixed in `carry_screen_cells`, which drops any scratch cell belonging to a
challenger that is not in `remap`; `selftest.py` reproduces the exact
aliasing scenario.

**The blast radius, stated honestly.** It cannot be repaired retroactively:
affected cells hold 2 corrupt game records out of 4 after the fill tops them
up, so the game counts look normal (every cell in every shipped campaign sits
at exactly the fill depth) and the state files never record which challenger
indices were killed. Upper bound for v6 is 57 aliasing challengers over 16
rounds. **So every campaign matrix in `campaigns/` is suspect, and so is any
equilibrium support solved from one.**

**What it does NOT touch: the numbers that decide what ships.** Those come
from `arena.py` runs on two-army pools at 400 pairs -- `conf_v6s57_*.json` and
the rest of the real-opponent grid -- which never go through this path. The
campaign matrix only decides which armies get PROPOSED; the four-column grid
decides which one wins, and that grid is clean.

## The king hunt's justification is withdrawn

`HUNT_WHEN`'s comment claimed the hunt "locks out" two of four king-last
styles. That rested on a selftest helper which returned `"lockout"` whenever
no KING placement was offered -- which is also true once the king is already
on the board. On the `rank3` style the black king was sitting on **a7 with 6
legal placements left** and the suite scored it a lockout.

With the artifact removed:

```
style        hunt off   HUNT_WHEN=6   HUNT_WHEN=8
dense        complete   complete      complete
queen_spam   1-0        1-0           1-0
rank3        complete   complete      complete
heavy        1-0        complete      complete
```

The hunt locks out **none** of them, and on `heavy` it **costs a win** the
baseline had. "Neither loses a win the baseline had" was false.

What survives: the lockout is a real rule -- ending setup without a king loses
outright -- and it is now actually SCORED. `rules.place()` had no terminal
state for it, so `done()` returned False for a kingless side, `complete` never
became True, and the designed win deadlocked: the turn was handed back forever
to a side with no legal placement, `Drafter.choose` raised, `handoff_fen`
raised, and `match.py` discarded a won game as an engine error.

**`HUNT_WHEN` is left at 6.** Changing it needs an A/B over the real-opponent
field, not a second guess off four hand-written styles. That is owed.

## The grid measures a game nobody plays

Every payoff in this repo up to here was built by `rules.setup_fen`, which
stamps two finished armies onto an empty board. That models simultaneous blind
commitment. Setup Chess is not that: placement ALTERNATES with full
information, so the second player can answer what the first has already put
down. A real opponent did exactly that -- stacked two attackers on a defended
bishop and took the exchange at handoff.

`draft.py` plays the phase instead. Same matchup, same instrument
(fairy-stockfish, 20,000 nodes), shipped champion `66fd725f` against the
13-bishop army `c52ed944` that is its worst column:

```
model                          score           pair-games
stamped, from the shipped grid 0.6891 +/- 0.0106   400
stamped, re-run here           0.7026 +/- 0.0277    95
DRAFTED, plan vs plan          0.3800 +/- 0.0235   200
DRAFTED, search vs search      0.5038 +/- 0.0266   200
```

**A comfortable win becomes a loss.** The re-run reproduces the grid, so the
gap is the model and not the wiring. 0.70 to 0.38 is roughly twelve sigma; no
sample size argument touches it.

Read the drafted rows carefully, because they are NOT the same matchup and
mislabelling them would be the easiest mistake here:

- Unopposed, the plan drafter builds 13 bishops exactly (`c52ed944`). Under
  interaction it does not: it goes off-plan twice and ends on `1P 11B 1R`.
  The cause is not check -- there are none -- it is `_safe_placements`
  declining squares that would let the opponent mate during setup.
- So a drafted cell means "champion PLAN against 13-bishop PLAN", not
  "champion against 13 bishops". The armies are outputs now, not inputs.
- Who moves first is derived from placement order rather than assigned to
  White by convention. Pair antisymmetry still holds exactly (1.0000) under
  matched modes, so this cancels within a pair and is not the explanation.

**A withdrawn claim, and why it was wrong.** This section first reported that
a searching drafter recovered half the gap, 0.3800 to 0.5038, with depth 2 at
0.5188. Both numbers were run with `--draft search:search`, and they do not
mean what they were written to mean.

A searching drafter builds from the POSITION, not from its nominal army. With
both sides searching, the pool never enters the calculation: champion vs 13
bishops, 13 bishops vs champion, and v2 vs v2 all drafted the identical pair of
armies (`87ddeab4` and `d7cfe0c8`). The matrix measured the search against its
own mirror, which sits near 0.5 by construction and differs from it only by
placement tempo, since White places first.

It surfaced because a `--pst-scale 1.0` run returned 0.5000 +/- 0.0000 with
every single cell identical -- the two near-mirrored armies reach a position
the referee draws every time. The blind-matrix guard fired. Without it the run
would have been recorded as "the piece-square term makes no difference", which
is a conclusion about nothing.

Compounding it, `--draft` attached modes to COLOUR rather than to army, so the
colour-swapped second game of a pair swapped strategies too. Both are fixed:
modes follow the army, and `search:search` now warns that it ignores the pool.

So the honest state is one comparison, not three:

```
model                          score           pair-games
stamped, from the shipped grid 0.6891 +/- 0.0106   400
stamped, re-run here           0.7026 +/- 0.0277    95
DRAFTED, plan vs plan          0.3800 +/- 0.0235   200
```

That finding is untouched -- plan mode does build the nominal armies, verified
piece by piece -- and it is the one that matters: playing the placement phase
turns a comfortable win into a loss.

Re-run properly with `search:plan`, which holds the opponent fixed while only
our side's policy changes, the searching drafter scores **0.0437**. Worse than
following the plan, not better, and by a wide margin.

### But read the error bars on all of these as fiction

Both drafters are DETERMINISTIC and carry no RNG. So a drafted cell produces
exactly ONE handoff position -- verified, eight drafts of the same matchup gave
one distinct FEN -- and every "pair-game" in it replays that single position
with only the 15% node jitter varying. The reported +/- is the referee's noise,
not the drafting's, and the effective sample for anything about DRAFTING is 1.

That means 0.0437 does not say "the search builds bad armies". It says this one
line is lost. It also caps the headline: 0.6891 stamped against 0.3800 drafted
compares two specific positions, which is a real difference between two
specific positions and NOT a law about drafting in general. The direction is
believable because the mechanism is understood; the magnitude and the sigma
are not transferable.

### The pool is mined out, and thirteen bishops is the whole problem

Every one of the 131 candidates in the v9 pool scored against all four real
opponents in a single pass -- 1,064 cells, 127,680 games, 0 errors, diagonal
0.4990 and pair symmetry 1.0004. Not a sample of the pool: the pool.

**Nothing beat the champion.** Three armies tie at the top, and the tie is real
rather than quantisation:

```
239c91ed   0.7583 +/- 0.0246
79fc9211   0.7583 +/- 0.0265   <- the shipped champion
07a28576   0.7583 +/- 0.0231
8446327c   0.7375 +/- 0.0173   (the champion before it)
```

That is the THIRD consecutive null on this pool, after confirming the top 5 and
then the top 19. The champion's worst column now reads 0.7800, 0.7784 and
0.7583 across three independent runs at falling precision, all overlapping. The
registered ~35% was wrong again, and three misses in the same direction is the
signal: **this pool has no better army in it.** More breeding or more
confirming is spending compute to re-derive a null.

**The structural finding is worth more.** For 104 of 131 armies -- 79% -- the
binding constraint is the same opponent:

```
worst column is    13 bishops   104  (79%)
                   wall          12   (9%)
                   bot 08-05      8   (6%)
                   bot 08-08      7   (5%)
```

And the ceiling per column, over every army in the pool:

```
bot 08-05    1.0000
bot 08-08    1.0000
wall         0.9917
13 bishops   0.8104
```

Three of the four real opponents are SOLVED -- some army in the pool beats each
of them essentially every game. Thirteen bishops is the only one that resists,
and it caps the whole project: since shipping is maximin, the worst column IS
the score, so "improve the army" has meant "improve against thirteen bishops"
for some time without that being stated.

So the honest frontier is not a better army but a better answer to one specific
opponent, and the pool was never bred for that -- it was bred against a field of
four, three of which stopped mattering. A campaign seeded and gated on thirteen
bishops ALONE is the experiment that has never been run.

### Pinning one opponent OVERFITS to it: v10 is REJECTED

The experiment the section above called for has now been run. `expand --pin-pool`
pins an opponent into the matrix as something to be measured against but never
bred from, and v10 pinned thirteen bishops alone: 120 armies, 9 rounds, 20
admitted, and still admitting 1-4 a round when the 350-minute cap stopped it.
That alone is worth noting -- v9 died on three consecutive nulls, so the pin
does open ground the unpinned campaign had exhausted.

The maximin pick is a new PLACEMENT of the champion's own composition, 1Q 9B 3P
1K, screening 0.9062 against the pin where the champion screens 0.8125.
Confirmed at 150 pairs a cell, 4,800 pairs, 9,600 games, 0 errors, diagonal
0.4971 and pair symmetry 1.0096, with the champion as a control arm:

```
                       real 0     real 1     13 bishops  real 3   worst
v10 s115  3P 9B 1Q     0.5933     0.6750     0.9408      0.9558   0.5933
79fc9211  (control)    0.9358     0.9817     0.7658      0.9483   0.7658
```

**It bought +0.1750 +/- 0.0209 on the pinned column and paid -0.3425 +/- 0.0326
and -0.3067 +/- 0.0282 for it.** The fourth column is a null, +0.0075 +/-
0.0190. Its worst column is 0.5933 against the champion's 0.7658, so it is
REJECTED and nothing ships.

This is a finding about the METHOD, not about one army. Breeding against a
single pinned opponent optimises the pinned column and nothing else, and since
shipping is maximin, a gain on one column bought with losses on two others is
strictly negative. The mined-out section was right that thirteen bishops caps
the project and wrong that a campaign against thirteen bishops alone is the way
out -- a pin has to be bred against the pinned opponent AND the rest of the
field at once, or the maximin rule simply throws the result away.

The control re-measured at **0.7658 +/- 0.0160** against 0.7800 +/- 0.0128 from
`confirm_v9c`. The gap of 0.0142 sits inside the combined margin of 0.0205, so
the instrument agrees with itself across a rebuilt field.

**The head to head was useless again, exactly as predicted.** Challenger and
champion drew all 8 screen games -- same composition, same fortress recorded
below. The columns were the entire decision, and this is the second time that
has been true.

### SEE-aware re-targeting: REJECTED, the projection sees phantom threats

The placement search's post-mortem assigned it a narrower job -- a tactical
filter inside a plan -- and this was that filter, at the one point where the
drafter chooses between futures. Placement order cancels at handoff, so the
real response lever is which pool army to complete into, and re-targeting
scored candidates only by matrix payoff against a similarity prior: both blind
to what THIS opponent has actually placed. The off-pool double-stack that
started the drafted rebuild was invisible to it. The term added the SEE
balance of each candidate's projected handoff against the revealed pieces.

Measured with match.py --test retarget-see, both arms re-targeting, only the
SEE term varying, 4,000 paired full games at 20k nodes:

```
OFF:     0.7691
ON:      0.7009
paired   -0.0683 +/- 0.0101   (better 404, same 2748, worse 848)
Elo      -47.50 [-54.53, -40.48] arm scale
SPRT     [0,8] arm-vs-arm, LLR -32.305 -> ACCEPT H0
```

REJECTED, decisively. The mechanism failed exactly where its docstring said
the risk was: the projection is one-sided, our FULL army against their PARTIAL
reveal, so it scores exchanges the finished opponent will never actually
offer. Mid-draft, most revealed pieces look attackable precisely because their
defenders have not been placed yet -- the phantom threat is the COMMON case,
not the corner case. The term fired in 1,252 of 4,000 pairs, far too often for
a filter meant to catch a rare stack, and re-targeted away from good armies to
dodge nothing.

What survives: the selftest oracle still passes -- against a genuinely
revealed stack the term picks the defending army -- so the mechanism is right
and the trigger is wrong. A retry would have to project the opponent's
completion too, or fire only above a threshold phantom threats cannot reach.
The toggle ships OFF.

Registered caveat from before the run, still true in reverse: this A/B
measures pool-vs-pool drafts, where genuine stacks are rare. It proves the
term hurts against normal opponents; it says nothing about what it would earn
against a real stacker. But -47 Elo of standing cost for an unmeasured benefit
in a rare case is not a trade, it is a donation.

### The champion holds, and the screen's ranking power is measured

The 19 highest-screening candidates not yet confirmed, plus the champion as a
control, at 110 pairs a cell -- 220 pair-games a column, 126,720 games, 0
errors, diagonal 0.5009 and pair symmetry 1.0001.

```
army                       worst column
79fc9211 (champion)        0.7784   <- still the best
239c91ed                   0.7443
0f5c0406                   0.6966
...
aa15d503                   0.5068
```

**Nothing beat it.** The control re-measured 0.7784 against 0.7800 from its own
confirmation, 0.0016 apart, so the instrument is steady and this is a real
null. The registered prediction -- ~50% that something would beat 0.7800 --
was wrong, and ~15% for anything clearing 0.85 was too.

The by-product is worth more than the verdict: **how much ranking power does a
campaign screen actually have?** Comparing each army's screen worst column
against its confirmed one, over 20 armies:

```
mean regression   -0.0090
range             -0.1494 to +0.0881
improved on screen 9 of 20
Pearson r         +0.546
Spearman rho      +0.263
```

So it is NOT a systematic winner's curse -- nearly half improved. It is noise
with a weak signal underneath. Spearman +0.263 is the honest figure; the higher
Pearson is carried mostly by the champion sitting at the top of both lists. A
screen cell is 4 pair-games at CI +/- 0.49, so this is exactly what the error
bars predict.

Two consequences. Selecting candidates by screen rank is only slightly better
than selecting at random from the pool, which is why nineteen of them produced
nothing. And a campaign cannot be trusted to have found its own best member --
the good armies have come from confirming a wide net, never from the loop's
own ordering.

The one composition question this could have reopened stays closed: the single
`9P 10B` pawn-heavy army in the field confirmed at 0.5341, rank 18 of 20.

### Confirmation is not optional: the screen flattered its winners

The v9 campaign was extended to 29 rounds and 135 armies -- it had never
converged, it ran out of pool space while still admitting 4-8 armies a round.
Ten candidates screening at or above the shipped champion were confirmed at 150
pairs a cell, 300 pair-games a column, 67,500 games, with the champion as a
control arm:

```
                       bot 08-05  bot 08-08  13 bishops  wall     worst
v9 s105  3P 9B 1Q      0.9375     0.9708     0.7800      0.9617   0.7800
v9 s95   3P 9B 1Q      0.8342     0.9608     0.7592      0.9108   0.7592
v9 s59   (control)     0.8283     0.9525     0.7367      0.8967   0.7367
v9 s134  3P 9B 1Q      0.6392     0.9650     0.8258      0.9150   0.6392
```

**The two armies the screen ranked highest, both at 0.8125 on eight pair-games,
came back at 0.7800 and 0.6392.** s134 lost a quarter of a point on the bot
08-05 column alone. That is the winner's curse working exactly as advertised,
and it is the answer to "why not just ship the screen leader": last time the
leader happened to hold, and treating that as the rule would have shipped an
army that is worse than the one it replaced. The control re-measured at 0.7367
against 0.7462 from its own confirmation, so the instrument is steady.

s105 ships. It DOMINATES the previous champion on all four columns and has the
better worst column, so maximin and dominance agree -- the first time in this
project they have not had to be adjudicated. The worst columns are separated:
gap 0.0433 against a combined margin of 0.0248.

**Their head to head is a FORTRESS, not a measurement.** s105 against s59 is
0.5000 +/- 0.0000 over 300 pair-games, every single pair drawn. That is real:
the drafted handoff evaluates +235 for White and still draws by the fifty-move
rule at both 20k and 60k nodes. Two massed same-colour bishop armies with
blocked pawns cannot make progress against each other. So a head to head
between queen-bishop armies carries no information at all, and the four columns
are the entire decision. Worth knowing before anyone reaches for a head to head
to break a future tie.

### Bred under drafted play, the answer grows a QUEEN

v9 is the first campaign bred and screened with the placement phase played
(`expand --draft`). 14 rounds, pool 90, support 15, exploitability 0, gate
0.7937 +/- 0.0154. Its top five candidates by worst column ALL carry a queen.

Confirmed at 200 pairs a cell, 400 pair-games a column, with the shipped
champion included as a CONTROL ARM so the comparison needs no cross-run
assumption:

```
                        bot 08-05  bot 08-08  13 bishops  wall     worst
v9 s59  3P 9B 1Q        0.8113     0.9437     0.7462      0.9119   0.7462
v9 s78  3P 1N 8B 1Q     0.9087     0.9756     0.7019      0.9656   0.7019
v9 s82  3P 1N 8B 1Q     0.9831     0.9825     0.6475      0.9688   0.6475
v9 s60  3P 1N 8B 1Q     0.8925     0.6819     0.6325      0.9169   0.6325
6P 11B  (control)       0.9969     0.9812     0.5713      0.9231   0.5713
v9 s35  3P 1N 8B 1Q     0.9975     0.9881     0.4994      0.9844   0.4994
```

The control re-measured at **0.5713 +/- 0.0137** against the 0.5767 from its
own confirmation, so the instrument agrees with itself. Head to head the new
champion beats it **0.9838 +/- 0.0060** over 400 pair-games.

**The screen did not flatter its winner.** The campaign cell that surfaced
s59 was 0.7500 on eight pair-games; confirmed at fifty times the sample it is
0.7462 +/- 0.0107. The registered prediction that it would "regress
substantially" was wrong -- it barely moved. The two predictions that held:
~60% that something confirmed above the champion (four did) and ~35% that
anything held above 0.70 (two did).

**A queen, after six campaigns of pure bishops.** Three independent drafted
measurements agree: the drafted equilibrium over 33 armies put 76.4% on a
3P 9B 1Q army, the first drafted campaign bred queen armies into all five top
slots, and this confirmation put four of them above the old champion. The
stamped campaigns were not wrong about the pool they searched -- they were
measuring what survives when neither side can answer the other, and a queen's
value is precisely that it punishes a reactive opponent. Bishops win a
commitment game; a queen wins a conversation.

Worst column over the whole project, on the only field that matters:

```
v2 champ    0.4700 stamped
v4 champ    0.6369 stamped
v6 s57      0.6891 stamped   -> 0.3917 drafted
1a6aa815    0.5767 drafted
8446327c    0.7462 drafted   <- ships
```

### The answer was in the pool; the stamped grid picked the wrong member

33 armies, 1,089 cells, 75 pairs each -- 163,350 games, 0 errors, diagonal
0.4998 and pair symmetry 1.0002. 29 candidates (every v6 and v7 support army,
all six past champions, the hand-written archetypes) ranked by worst column
against the four real opponents, with the placement phase played out.

```
idx  vs bot1  vs bot2  vs 13B   vs wall  WORST   what it is
 4   0.9983   0.9883   0.5767   0.9400   0.5767  v6 support, 6P 11B, Ke1
 7   0.9950   0.9917   0.5533   0.9117   0.5533  v6 support, 6P 11B, Kf1
 8   0.9817   0.9650   0.5150   0.9200   0.5150  v6 support, 3P 12B
11   0.8483   0.9083   0.5067   0.9383   0.5067  champion v2, 3P 9B 1Q
 5   0.9650   0.9900   0.3917   0.9350   0.3917  SHIPPED v6 -- 13th
 9   0.9967   0.9967   0.0517   0.9483   0.0517  v7 queen army
```

**Four armies beat all four real opponents. The shipped champion is not one of
them**, and it ranks 13th of 29. The registered prediction that ~15% of the
time anything would clear 0.5 on the 13-bishop column was too pessimistic:
four did.

The new pick, `1a6aa81575387e54`, was in the v6 pool the whole time. It has the
SAME composition as the shipped champion (6P 11B) and the same king square, and
differs in the placement of three pieces out of eighteen. That is the whole gap
between losing to thirteen bishops at 0.3917 and beating them at 0.5767, and it
beats the shipped champion head to head at **0.7167 +/- 0.0165**.

So the stamped grid was not wrong about bishops, or about the pool. It was
wrong about WHICH member of its own pool to ship, because the quantity it
ranked on -- performance from a position where neither side could answer the
other -- is not the quantity that decides a real game. The v7 queen army is the
sharpest illustration: 0.9967 and 0.9967 against both bot armies, and 0.0517
against thirteen bishops.

**The equilibrium disagrees with maximin, and the disagreement is informative.**
Solved over the full 33-army matrix, exploitability 0.0000:

```
76.41%  setup 11   champion v2, 3P 9B 1Q
14.36%  setup  7   v6 support, 6P 11B
 9.23%  setup  4   v6 support, 6P 11B  <- the maximin pick
```

These answer different questions and both are honest. Maximin asks what
survives the worst REAL opponent; the equilibrium asks what is unexploitable
against the whole 33-army field, most of which nobody plays. The old v2
champion carrying 76% of an unexploitable mix while ranking fourth on maximin
says it is broadly solid and specifically vulnerable. Shipping follows maximin,
because the field that matters is the one people actually play.

Effective drafting sample is 116 matchups for the ranking (29 candidates x 4
opponents), against the 4 behind every drafted number measured before it.

### The placement search: REJECTED as an army builder

Same field, same opponents, same game indices; the only change is whether the
champion's side drafts by following its plan or by searching. Paired:

```
column          plan     search   delta
bot 08-05       0.9667   0.9771   +0.0104
bot 08-08       0.9833   0.8438   -0.1396
13 bishops      0.3750   0.0792   -0.2958
wall            0.9250   0.9000   -0.0250
ROW             0.8125   0.7000
paired          -0.1125 +/- 0.0200 over 480 shared cells, EXCLUDES zero
```

**Worse overall, and worst exactly where it had to help.** The 13-bishop column
was already the only losing one at 0.3750; searching takes it to 0.0792, close
to total. The verdict is REJECTED, and the prediction registered before the run
-- worse, between the single matchup's -0.3362 and zero -- held.

The mechanism is not tactical. The search does the tactical job it was built
for: material hung at handoff went from 300cp to 0, and it walks into no setup
mates. What it cannot do is choose an ARMY. Given the champion's plan of
6P 11B it builds 4P 10B 1R, agreement 0.870, nothing hanging -- and loses.
Expected overlap with the equilibrium mixture is too crude a proxy for army
quality, and depth does not rescue it (depth 2 bought nothing over depth 1).

So the architecture is wrong, not the tuning. Composition should come from the
measured pool, which is what seven campaigns produced, and the search should
only be allowed to choose among placements that keep the planned army intact.
`play.Drafter._safe_placements` is already a one-ply version of exactly that,
which makes the existing drafter closer to right than the replacement.
`psearch` earns its place as a tactical filter inside a plan, not as a
substitute for one.

### The four-column grid, re-measured with the phase played

Five armies, 1,500 pairs, `plan:plan`, so four distinct drafted matchups
instead of one. The champion's row against the same field as the shipped grid:

```
opponent            stamped      drafted (plan)
bot 08-05           0.9513       0.9667 +/- 0.0163
bot 08-08           0.9884       0.9833 +/- 0.0126
13 bishops          0.6891       0.3750 +/- 0.0290
wall                0.8959       0.9250 +/- 0.0243
worst column        0.6891       0.3750
```

**Three columns barely move and one collapses.** That is a far more specific
claim than "drafting changes everything", and a more damaging one. The
champion's advantage over the two bot armies and the pawn wall survives the
placement phase intact -- it even improves slightly. Against the 13-bishop
army, the one a human actually played and beat us with, it falls from a
comfortable 0.6891 to a LOSS at 0.3750, reproducing the 0.3800 measured on the
single matchup.

This matters for the shipping decision specifically. v6 was selected by
MAXIMIN over that row: 0.6891 was the best worst-column any candidate had. The
worst column is not 0.6891 once the phase is played, it is 0.3750, and it is
below 0.5, so the champion does not beat its worst opponent at all. Whether
maximin still selects the same army is unknown -- the other candidates have
not been re-measured this way.

The effective drafting sample is 4, not 480: both drafters are deterministic,
so each matchup contributes one position replayed under node jitter, and the
intervals above are the referee's noise. Four is enough to show the effect is
column-specific rather than global, which is the claim being made here.

What this invalidates: the four-column grid, every campaign matrix, and the
maximin argument that shipped v6 -- all of them rank armies under the stamped
model. What it does NOT invalidate: that the armies are legal, that the
engines played them, or the basin result below, which is a statement about
where the search converges rather than about any single cell. Nothing has been
re-derived yet, and until it is, treat every number above this section as
describing simultaneous blind play.

## The basin experiment: bishops are the answer BECAUSE massed bishops exist

v7 asked whether six campaigns agreeing on eleven bishops meant "best army"
or just "same starting pool". It started from 14 armies with at most 5
bishops each, on the fixed machinery, gated on the four real opponents.

**The pool did not come back to bishops.** 2 of 61 armies hold 8 or more
(earlier pools: ~90%), 60 of 61 hold a queen or a knight, and the final
support is `6P 2N 6B 1Q` at two-thirds weight. So the agreement WAS
start-dependent: there is a second, self-consistent equilibrium region built
around queens, and the double oracle is happy to live in it.

Then the four-column confirmation, 400 pairs a cell:

```
                    bot 08-05   bot 08-08   13 bishops   wall     worst
v6 s57 (ships)      0.9513      0.9884      0.6891       0.8959   0.6891
v7 s57 (1Q 2N 6B)   0.9975      0.9966      0.2384       0.9569   0.2384
v7 s59 (9B 9P 1N)   0.9981      0.9988      0.5428       0.9166   0.5428
```

**The queen army dominates the shipped champion on three of four columns**
-- including 0.9975 and 0.9966 against the bot's queen armies, the best bot
cells ever measured -- **and then loses 0.2384 to thirteen bishops**, the
worst cell on the grid by a wide margin. v7's start pool contained no massed
bishops, so its equilibrium never had to answer them, and it cannot.

This resolves the basin question with a mechanism rather than a vote count:

- **Massed bishops beat queen armies, hugely and consistently.** The bot's
  three-queen armies lose 0.95+ to bishop armies; v7's one-queen support
  loses 0.76 to thirteen bishops. It is the most lopsided relationship in
  the game.
- **So whichever pool CONTAINS massed bishops ends up ruled by them**, which
  is why six bishop-containing campaigns agreed, and why a bishop-free
  campaign settles somewhere else.
- **The answer is field-dependent, and the field contains bishops.** A human
  actually played thirteen bishops at us. Against a world with no massed
  bishops, v7 s57 would be the better army -- it dominates everywhere else.
  Against the world as observed, the worst-column rule keeps v6 s57, and by
  a mile.

The registered prediction (neither clears 0.6891, ~15% on a displacement)
held. The fixed-code/new-start confound registered before the run never
needed resolving: the outcome is not "different numbers for the same armies"
but a different region entirely, and its failure mode is measured.

"The answer is still bishops" now carries this asterisk permanently: bishops
win because the opponent field includes massed bishops, not because nothing
else can play chess.

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
