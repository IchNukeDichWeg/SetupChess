# Setup Chess

**Finding the strongest opening army for the chess.com variant
[Setup Chess](https://www.chess.com/variants/setup-chess).** Before play, each
side spends **39 points** placing an army on its own first three ranks (P=1,
N=3, B=3, R=5, Q=9, king free and mandatory, duplicates unlimited). Once both
armies are down, it is ordinary chess.

This is **not a chess engine**, and deliberately so. Once the armies are placed
the position is ordinary chess, and `fairy-stockfish` plays it far better than
anything here would. The whole question is the half that has no theory yet:
**which 39 points, and on which squares.**

So the repo is a measurement apparatus for one question. It breeds a pool of
candidate armies, plays them against each other to fill a payoff matrix, solves
that matrix for the equilibrium mix, and repeats -- a double oracle over the
space of armies. A drafting policy then realises the chosen army against a live
opponent, handling the tactics the placement phase actually has, including two
ways to win before a move is played.

**The answer it arrived at is eleven bishops and six pawns** -- and a real
opponent then beat it with **thirteen bishops and no pawns**, an army this
project never once sampled. That result is measured and stands
([below](#the-pool-is-a-sample-not-a-cover)). Everything here is how the answer
was established and how far it should be trusted, which turns out to be less
far than the error bars suggest.

**The shipping army changed on 2026-08-15**, to `1a6aa81575387e54`, after the
placement phase was measured properly for the first time. It is still eleven
bishops and six pawns, still king e1, and differs from the previous champion in
the placement of three pieces out of eighteen -- which is the whole difference
between losing to thirteen bishops at 0.3917 and beating them at 0.5767. It was
in the pool the entire time; the old measurement simply ranked the wrong member
of it.

> ### Read this before trusting any number below
>
> Every payoff in this repo was built by stamping two **finished** armies onto
> a board. That models simultaneous blind commitment. The real game alternates
> placements with full information, so the second player can answer what the
> first has already committed -- which is how the human who beat us won: he
> stacked two attackers on a bishop he could already see, and took the
> exchange at handoff.
>
> When the placement phase is actually played out (`draft.py`), the shipped
> champion's score against its worst column goes from **0.6891 +/- 0.0106 to
> 0.3800 +/- 0.0235**. A comfortable win becomes a loss, about twelve sigma.
>
> Measured across a four-opponent field, the collapse is **one column**, not
> the whole grid: the champion holds against both bot armies and the pawn wall
> and loses only to the 13-bishop army a human actually beat us with.
>
> Re-ranking all 29 candidates that way (33 armies, 163,350 games) changed the
> shipping decision. **Four armies beat all four real opponents; the previously
> shipped champion was 13th of 29** at 0.3917. The new pick clears its worst
> column at **0.5767** and beats the old champion head to head at 0.7167 +/-
> 0.0165.
>
> **Searching the placement game is REJECTED**: -0.1125 +/- 0.0200 paired
> against simply following the plan, and worst on the column that mattered
> (0.3750 -> 0.0792). It does its tactical job -- hung material goes 300cp to
> 0 -- but it cannot choose an army. Composition belongs to the measured pool;
> the search's place is as a filter inside a plan, not a substitute for one.
>
> So the four-column grid, every campaign matrix, and the maximin argument
> that selected the current champion all rank armies **under a model of the
> game nobody plays**. They are not being deleted, because they are honest
> measurements of what they measured and the machinery is reused, but nothing
> below has been re-derived yet. Treat every number outside this box as
> describing simultaneous blind play, and see
> [docs/MEASUREMENTS.md](docs/MEASUREMENTS.md#the-grid-measures-a-game-nobody-plays).
>
> The project's centre of gravity has moved with it: from "which 39 points" to
> **"what is the best reply to what the opponent just placed"**, which is a
> search problem over the placement tree rather than a search over armies.

Python is the harness. There is a C move generator, used as a cross-check and
perft oracle rather than to play anything.

Current version **v4**; what shipped and what it does not know are in
[docs/RELEASES.md](docs/RELEASES.md).

## What it found

**Bishops dominate, and it is not an artifact.** The shipping army is eleven
bishops and six pawns:

```
  3 | P B B B B . B P
  2 | B P B P B P . P
  1 | . K . B . B B .
      a b c d e f g h
```

Every earlier campaign was refereed by Stockfish, which **segfaults above 32
pieces**, so every high-piece-count matchup was an unmeasured hole and dense
armies were harder to keep. Dense is exactly what beats a pawn wall, so the
bishop result could have been survivorship. It is not. Rebuilt from scratch
with `fairy-stockfish`, which has no piece ceiling and left **zero** cells
unmeasured, the equilibrium is bishop-heavy in all thirteen of its members:

```
  31.8%  11 bishops + 6 pawns      8.4%  10 bishops + 9 pawns
  18.9%  11 bishops + 6 pawns      3.8%  10 bishops + 4 pawns + 1 rook
  12.8%  11 bishops + 6 pawns      1.9%  12 bishops + 3 pawns
  ...seven more, all 11 bishops + 6 pawns
```

The censoring did change **which** bishop army wins. The previous champion's
twelve-bishops-and-three-pawns survives at 1.9% of the mix, and head to head
under one referee the new army beats it:

```
Games   | 800 pairs, 0 lost outright
Score   | 0.6559 +/- 0.0111
Split   | 30 swept / 478 at 0.75 / 253 drawn / 39 at 0.25
Elo     | +112.09  [+103.64, +120.67]
SPRT    | [0,4] LLR +27.661 -> ACCEPT H1
TC      | 20,000 nodes, 15% jitter, fairy-stockfish, no piece ceiling
```

That result is **harder** than it looks: the new pool was capped at 60 setups
while the old champion came from 87, so the handicap ran against the winner.

Against the twelve hand-written archetypes, sampled uniformly:

```
Games   | 800 (400 pairs), 0 unplayable
Score   | 0.9425 +/- 0.0121   (per PAIR; see below)
W/D/L   | 712 / 84 / 4
Elo     | +485.85  [+451.79, +527.08]
SPRT    | [0,4] LLR +66.729 -> ACCEPT H1
Pool    | 60 setups, exploitability 0 in every round
TC      | 20,000 nodes fixed, 15% per-game jitter
Machine | Mac14,9 arm64, macOS, fairy-stockfish 14.0.1
```

The margin and the LLR are per PAIR. The gate used to flatten each
colour-swapped pair into two independent games, which inflated the LLR by
19-40% across the shipped campaigns; the mean is unaffected and the verdict
is unchanged. See `docs/MEASUREMENTS.md`.

Four losses in 800 games. Read that as "much better than hand-written
guesses", not "strong": the archetype field includes deliberately bad armies
and one of them scores 0.016. See [Known limits](#known-limits).

### The superseded Stockfish numbers

Kept because they are what the earlier commits measured, and because the two
sets are **different instruments and cannot be differenced**. The old champion
scored 0.9346, +462.06 [+440.79, +485.88] over 1,820 of 2,000 games, with 90
pairs unplayable, on an 87-setup pool after 21 rounds.

**It holds at ten times the depth, and the head-to-head gain transfers.**
The current champion against the twelve archetypes, `fairy-stockfish`, no
piece ceiling, so nothing is dropped:

```
20k    | 0.9349 +/- 0.0127   +462.86 [+429.53, +502.90]   385 swept / 28 drawn
200k   | 0.9500 +/- 0.0107   +511.50 [+475.97, +555.09]   400 swept / 16 drawn
Pairs  | 480 of 480 both times, 0 lost outright
```

At 200,000 nodes **no pair scores below 0.5 at all**. Both node counts solve to
an equilibrium that is pure on the champion with exploitability 0.0000, and
neither drops an archetype.

The previous champion ran the identical gate, so the cells pair up exactly --
same opponent, same game index, same node jitter, only our army differs:

```
                              paired difference (new minus previous)
20k    480 pairs   +0.0255 +/- 0.0156   144 cells changed
200k   480 pairs   +0.0203 +/- 0.0122   118 cells changed
```

Both exclude zero, so the +112 Elo head-to-head advantage **does** show up
against the archetype field. It does **not** show up against the modelled
opponent, where the two champions are indistinguishable. Same two armies, two
opponents, two different answers -- which is why a single headline number is
not enough.

Depth is worth a little to both armies and slightly less to the new one:
`+0.0151 +/- 0.0126` for the current champion against `+0.0203 +/- 0.0136` for
the previous, over the same 480 paired cells.

## Against a model of the real opponent

The archetypes are hand-written guesses. `play.py --opponent bot` is
chess.com's setup policy rebuilt from its shipped client
(`docs/BOT_MODEL.md`): king to a corner on move one, then 16 pawns and 7
knights, because material is absent from its setup eval entirely.

> **The second half of that model is REFUTED.** Two full setup phases played
> against the live bot, both as Black, both spending all 39 points:
>
> ```
> 2026-08-05   Ka8  Qc8 Qa7 Qb7  Rb8  Bc6  Pa6 Pb6 Pg7 Ph7
> 2026-08-08   Ka8  Qb7 Qb8 Qa7  Nb6 Nc6  Bg6  Pa6 Pe6 Ph7
> ```
>
> **Three queens in both** -- 27 of 39 points on the piece the model says it
> avoids. The king clause is confirmed twice more, a corner on the very first
> placement. Reading the minified eval gave us the tie-breaks and not the terms
> that decide.
>
> The observed armies are `pool.BOT_OBSERVED` and are what `--seed-bot` now
> pins. Every number below against `--opponent bot` describes the modelled
> opponent, not the real one.

The **current** champion (11 bishops + 6 pawns):

```
we are white | 0.9400 +/- 0.0246   +477.99 [+413.65, +574.24]  W/D/L 178/20/2
we are black | 0.9425 +/- 0.0242   +485.85 [+420.21, +585.37]  W/D/L 179/19/2
Games        | 200 per colour, 20,000 nodes, 15% jitter
Referee      | fairy-stockfish, no piece ceiling
```

357 wins, 39 draws, 4 losses in 400 games. The **previous** champion scored
0.9200 and 0.9400 on the identical instrument, so the intervals overlap almost
entirely and **there is no measured difference between the two against this
opponent** -- even though the new army beats the old one by +112 Elo head to
head. Two armies can be far apart against each other and indistinguishable
against a third.

```
we are white | 0.9200 +/- 0.0255   +424.28 [+371.39, +495.60]  W/D/L 168/32/0
we are black | 0.9400 +/- 0.0236   +477.99 [+415.91, +569.22]  W/D/L 177/22/1
Games        | 200 per colour, 20,000 nodes, 15% jitter
Referee      | fairy-stockfish, no piece ceiling
```

345 wins, 54 draws, **one loss** in 400 games.

### What the referee was hiding

The first version of this gate was refereed by our C core, because 40 pieces is
over vanilla Stockfish's ceiling. It reported 0.6500 as White and 0.8475 as
Black with **201 of 400 games drawn**, and two conclusions were drawn from it.
Both were wrong, and both are withdrawn:

* *"The pawn-and-knight wall is genuinely hard to break."* It is not. The draws
  were our core failing to convert a won position; 0.65 becomes 0.92 with a
  referee that can finish.
* *"The colour asymmetry is the interesting part."* The 0.65/0.85 gap was also
  the referee. It is 0.92/0.94 here, with overlapping intervals, so the story
  about re-targeting swapping in a rook as Black has no measurement behind it.

The archetype gate scores 0.9346. This one scores 0.92 to 0.94. Those are
**different referees and cannot be differenced**, but there is no sign the
modelled opponent is a harder field than the twelve hand-written armies.

### What still limits it

* **The intervals carry chess-phase variance only.** Both drafters are
  deterministic, so this is ONE setup per colour and 200 games of node jitter
  on top. Drafting variance is unmeasured.
* **We move first in both colours** (checked: both handoff FENs give us the
  move), since the bot spends 24 placements to our 16 and always places last.
* **Different instrument from every Stockfish number above.** Separate
  campaign, separate file, never pooled.

### Breeding against it

`expand.py --seed-bot` breeds the pool against this army instead of only
against itself. Three parts, all load-bearing: the wall joins the starting
pool, it is **pinned** so the prune cannot drop it, and a challenger must clear
`--screen-margin` against it **as well as** against the equilibrium support.
Without the third the whole thing is a no-op, because the wall draws rather
than wins, so the solver gives it no weight and the screen only ever plays
challengers against the support.

The two requirements are deliberately separate rather than averaged. Blending
them at 50/50 was measured to destroy the filter: beating the wall at ~0.95
contributes 0.475 on its own, so a challenger needed only 0.10 against the
support to clear a 0.5 margin. Admissions ran 12 of 32, then 16 of 25, then 28
of 31; the pool went 13 to 69 in three rounds; and the fill cost per round went
1,728 to 4,032 to 11,984 pairs, because it grows with pool times admitted.

```bash
python3 expand.py --state campaigns/expand_bot_new.json --seed-bot \
  --engine fairy-stockfish --max-pieces 0 --rounds 30 --workers 0 \
  --final-games 400
```

This starts a FRESH pool (the twelve archetypes plus the pinned wall), so it
inherits nothing from `expand_own.json`. Different referee from every campaign
above, so it is a different instrument, a separate state file, and its gate
number is not comparable with the Stockfish-refereed ones. Comparing the old
champion with whatever this produces needs a head-to-head under a single
referee, not a subtraction.

## A real game, start to finish

Played on chess.com's own analysis board against its built-in bot,
2026-08-08. We drafted with `--mix` on, so the army came from the equilibrium
support rather than the argmax: **12 bishops and 3 pawns, king b1**.

```
setup   1. @Bg1 @Ka8   2. @Bb3 @Qb7   3. @Bc3 @Pa6   4. @Bf1 @Nb6
        5. @Ba2 @Qb8   6. @Be2 @Nc6   7. @Bc2 @Qa7   8. @Bd2 @Ph7
        9. @Bf2 @Pe6  10. @Kb1 @Bg6  11-16. @Ba3 @Bd3 @Be3 @Pb2 @Ph2 @Pg3
                                            (Black passes throughout)

chess   16... e5  17. Bxb6 Qaxb6  18. Bxb6 Bxd3  19. Bexd3 h5
        20. Bxa6 Qxb6  21. Bxb6 Qxb6  22. Be4 Kb8  23. Bxc6 Qxc6
        24. Bxe5+ Ka8  25. Bd5 Qxd5  26. Bxd5+ Ka7  27. Be3#
```

**1-0, checkmate on move 27.** The chess phase was played by `fairy-stockfish`
at 2M nodes per move; the drafting was ours.

Three things the game settled, none of which a self-play campaign could:

* the bot placed its king in a **corner on its first placement**, as
  `docs/BOT_MODEL.md` predicts from the shipped client
* it then bought **three queens** -- 27 of 39 points on the piece that model
  says it avoids. The piece-preference half of our model is refuted
* White placed 16th and last, so **Black moved first** in the chess phase, the
  turn-parity rule confirmed live once more

The bishop mass ate the queens: every queen that captured on b6 was recaptured
by another bishop, because eleven bishops defend each other on both colours.
The evaluation was +21 within one move of the handoff.

## The pool is a sample, not a cover

A real game surfaced an army the expansion loop never produced: **thirteen
bishops, no pawns**, all 39 points on bishops. Head to head against the
champion under one referee:

```
Games   | 800 pairs
Score   | 0.4700 +/- 0.0109   (from the champion's side)
Split   | 2 swept / 107 at 0.75 / 488 drawn / 199 at 0.25 / 4 lost
Elo     | -20.87  [-28.51, -13.25]
SPRT    | [0,4] LLR -6.077 -> ACCEPT H0
```

**The champion loses.** Not by much, and 488 of 800 pairs are drawn, but the
decisive pairs break 199 to 107 against it and the interval is clear of zero.

Not one of the 60 setups in the pool is a pure bishop army. Every survivor
keeps at least three pawns:

```
21x   11 bishops + 6 pawns     <- the champion
16x   12 bishops + 3 pawns
 8x   10 bishops + 1 knight + 6 pawns
 5x   10 bishops + 1 rook + 4 pawns
 2x   10 bishops + 9 pawns
```

This is worth being precise about, because it is easy to read as the
measurements being wrong. They are not. Exploitability 0 means **no army in
the pool** beats the equilibrium mix, and that was true. It says nothing about
armies outside the pool, and the mutation operators never walked all the way to
the corner of the space where 13 bishops lives. A double oracle is only as good
as what its challengers reach.

So the honest status of "eleven bishops and six pawns" is: the best army this
search **found**, beaten by the first outside army anyone tried it against.

### Handing the gap to the loop fixes it

Re-run with the 13-bishop army seeded into the starting pool (`--seed-army`),
94 setups, exploitability 0. The new champion is again 11 bishops and 6 pawns,
a different arrangement, king on b1:

```
                            score     Elo                    verdict
v3 vs 13 bishops           0.5109   +7.60 [+3.76, +11.45]   ACCEPT H1
v2 vs 13 bishops           0.4700  -20.87 [-28.51, -13.25]  ACCEPT H0
v3 vs v2, head to head     0.4975   -1.74 [-11.43,  +7.96]  CONTINUE
```

A **28 Elo swing** on the matchup that was seeded, and **no measured
difference** between the two champions head to head. The double oracle could
not invent 13 bishops, but handed it, it found answers.

It was not free. Against the twelve archetypes the v3 mix scores **0.9038**
where v2 scored **0.9425** on the same instrument -- nine losses in 800 games
against four. Hedging against an army the archetypes do not contain costs
something against everything else.

Note also how drawish this matchup is: **717 of 800 pairs drew**. The two
armies are close enough that the chess phase usually cannot separate them.

### Judged on real opponents instead, the answer changes again

The archetypes are twelve armies we invented. Three armies have ever actually
been played against us: two the bot drafted and the thirteen bishops a human
drafted. Every champion against every one of them, 800 games a cell,
`fairy-stockfish`, no piece ceiling:

```
       king   bot 08-05   bot 08-08   13 bishops   worst    archetype gate
v2     e1     0.9172      0.4869      0.4700       0.4700   0.9425
v3     b1     0.9734      0.9641      0.5109       0.5109   0.9038
v4     f1     0.9006      0.9569      0.6247       0.6247   0.9406
```

All three champions are the **same 11 bishops and 6 pawns**. v2 and v4 differ
by **two pieces**: the king moves e1 to f1 and one bishop moves d1 to g3,
nothing else. Those two squares are the difference between 0.4869 and 0.9569
against the bot army we actually met. v3 is a genuinely different arrangement,
8 of its 17 non-king pieces elsewhere.

That is the result: against real opponents, arrangement decides these matchups
and material does not.

Read the v2 row. It has the **best archetype gate of the three** and it is the
only army here that cannot beat a real opponent -- against the bot army we
actually met over the board it draws **764 of 800 pairs** and scores 0.4869,
with truncation at 1.1%, so those are real draws and not the ply limit. An
army can look best against a field of guesses and be a fortress against the
thing you will actually face.

Armies are chosen on the worst column rather than the average or the gate.
Winning 0.90 instead of 0.97 against something you beat either way costs less
than 0.51 instead of 0.62 against something you do not.

### What ships now, and the column that finally moved

A fourth real opponent has since been added -- a fifteen-pawn wall a human
played in a live rated game -- and the v6 campaign moved the binding column
for the first time since v4:

```
            bot 08-05  bot 08-08  13 bishops  wall     worst
v4 champion 0.9684     0.9850     0.6369      0.8912   0.6369
v6 idx 57   0.9513     0.9884     0.6891      0.8959   0.6891   <- ships
v6 idx 33   0.9931     0.9969     0.6713      0.9137   0.6713
```

`play.py` defaults to **v6 index 57**, 11 bishops and 6 pawns with the king on
e1. Index 33 -- the first competitive non-bishop army in six campaigns, with
two knights -- **dominates the old champion on all four columns** and has the
better mean, but a lower floor. The rule registered before the run was worst
column primary with dominance only as a tiebreak, so 57 ships; the trade is
that maximin buys the floor and pays in the average. See `docs/RELEASES.md`.

### Being predictable is worse than any of this

Every number above is for a FIXED army, and a fixed army is exploitable by
construction. From the same matrix, against an opponent who knows what we play:

The hard counter is already in our own pool -- an 11-bishop 6-pawn army on
different squares, with the king on f1 instead of b1. Measured over 400 pairs:

```
champion vs its own best response   0.4566 +/- 0.0143
                                   -30.26 Elo [-40.27, -20.30], ACCEPT H0
```

Online, placements are visible and opponents play you repeatedly, so being
predictable is not a hypothetical cost. Note also what the counter *is*: the
same composition on different squares. **Arrangement beats composition here**,
and this project has spent its whole budget searching compositions.

> **A methodological correction worth keeping.** That counter was found as the
> argmin over 94 columns of a matrix whose cells hold 8 pairs each, and the
> screen said 0.3438, or -112.3 Elo -- nearly four times the real effect. The
> minimum of 94 noisy samples is biased low by construction. Every number in
> this repo read off the matrix by taking a max or min over many cells carries
> the same inflation. The reactive ceiling that motivated `--optionality` was
> recomputed on the shrunk matrix and fell from **+179.8 to +46.0 Elo** --
> against which measured re-targeting already captures +6.71.
>
> **Averages are fine, and measurably so.** Split-half reliability of the army
> ranking is **r = 0.995** over 93 armies, because an army's mean score
> averages ~370 pairs even though each cell holds four. The rule is: average
> over the matrix freely, never read an extreme off it. Every mistake here was
> an extreme; every result that survived scrutiny was an average.

**Mixing is ON by default.** `play.py` draws from the stored equilibrium
support each game rather than always playing the argmax -- 9 distinct armies
on the v6 campaign. `--no-mix` restores the single-army behaviour.

Every one of those nine is screened against all four real opponents before the
campaign ships, and none scores below 0.5. That check exists because v2 shipped
with a support member quietly **losing** at 0.4481 while holding 9.1% of the
weight, so the drafter chose an already-beaten army about one game in eleven.

That is a judgement about the opponent, not a measured improvement, and it
cannot be measured here: every harness in this repo scores against a fixed
field, which is exactly where mixing loses. An A/B would return about -32 Elo,
and that answer would be true and useless.

`adapt.py` simulates what happens when the opponent learns you, replaying the
measured matrix against fictitious play. No engine, seconds to run:

```
round   pure      mix
1       0.5175    0.5398     <- opponent is still guessing
2       0.4479    0.5127     <- solved
10      0.4479    0.5057
200     0.4479    0.4974

second half   pure 0.4479 (-36.3 Elo)   mix 0.5002 (+0.2 Elo)
```

**A fixed army is solved after one game and stays solved.** From round 2 the
pure strategy is pinned at exactly one value forever, because the opponent
found its counter and never has to look again. Searching for a better fixed
army does not fix that; it only changes which army gets countered.

**The levels are corrected, and the correction is calibrated against a real
match.** The opponent's best response is an argmax over cells backed by four
pairs, which lands on whichever cell got lucky -- raw, it valued the counter at
0.3438 where a 400-pair match measured **0.4566**. Shrinking each cell toward
0.5 by 8 pseudo-observations predicts that cell to within 0.009. It is a
single-point calibration, so treat the ~36 Elo as an order of magnitude.

Still not a real-game number: placement alternates, so a live opponent cannot
see your finished army before committing to theirs. This is the pessimistic
bound, and the truth lies between it and the fixed-field numbers above.

The shape is safe regardless, being a property of equilibria rather than of
this data. `selftest.py` pins it against rock-paper-scissors, where the answer
is known exactly, and pins the shrinkage against the 400-pair cell.

## The setup phase has real tactics

Two ways the game ends before a single move is played, both verified against
the shipped chess.com client:

* **Checkmate during setup.** A placement can give check, and the checked
  side can only answer by placing a blocker inside its own three ranks. If
  nothing reaches, it is mate. Triggered live on chess.com's own analysis
  board with `@Qe1#` against a king on the third rank.
* **King lockout.** A player who ends setup without a king loses outright
  (the client says `"failed to set up his king"`). A king may not be placed
  on an attacked square, so covering every empty square in the opponent's
  zone wins without any checkmate at all.

The second one punishes the common habit of placing the king last. Measured
against an army covering 23 of the opponent's 24 zone squares:

| opponent style | outcome |
|---|---|
| dense, king last | survives -- its own 16 pieces block every ray |
| queen spam, king last | loses, setup checkmate |
| rank-3 rush, king last | loses, locked out |
| heavy, king last | loses, locked out |

Sparse armies get punished; dense ones shield themselves.

## Choosing the referee, which mattered more than anything else

Every army here is judged by playing games, so the engine doing the judging
**is** the measuring instrument. Getting it wrong does not add noise, it
manufactures results.

Setup Chess positions are legal in the variant and illegal in *standard*
chess: sixteen pawns, nine bishops, up to 48 pieces on the board. python-chess
rejects them by ordinary-chess history rules, and Stockfish's data structures
assume 32 pieces -- the 42-piece pawn-wall mirror answers `depth 4` fine and
then **segfaults at 20,000 nodes** (measured threshold on this build: 36
pieces survive, 38 crash). The 40-piece champion-versus-bot matchup dies the
same way, exit code -11.

Every campaign built that way silently dropped its **high-piece-count** cells,
and dense armies are exactly what a pawn wall loses to. Two results turned out
to be artifacts of that hole:

* the wrong bishop army was crowned -- the uncensored rebuild's champion beats
  it by **+112 Elo**
* re-targeting looked worth **+24.63 Elo** and is actually worth **+6.71**

`fairy-stockfish` 14.0.1 plays all of it: the 40-piece matchup, the 42-piece
wall, and the 48-piece mirror of the bot's army, which is this variant's
theoretical maximum. It defaults to `UCI_Variant chess`, agreed with vanilla
Stockfish on three forced-tactic oracles, and costs 34.9 ms/move against our C
core's 15.6 ms at 20,000 nodes on the 40-piece position. Being a Stockfish 14
derivative it is vastly stronger than our core's -327 Elo.

**Pass `--engine fairy-stockfish --max-pieces 0` to every harness here.** It
also removes the case for training an NNUE, which existed only because nothing
strong could play these positions.

The C core remains as an independent cross-check and the perft oracle, and
should not referee a measurement again. It is bitboard-based, so unlike
Stockfish it has no piece-count ceiling:

```
Perft        | startpos(4) 197,281 exact; 8 setup positions to 42 pieces
Mismatches   | 0 against python-chess, node for node
Speed        | 41.5 Mnps vs python-chess's 1.66 (25x)
```

Published perft numbers assume castling, which this variant does not have,
so python-chess with the rights stripped is the reference.

## Layout

| file | what it is |
|---|---|
| `docs/RULES.md` | the rules, every line sourced, assumptions explicit |
| `docs/RELEASES.md` | what shipped in each version, its known limits, and the release checklist |
| `docs/MEASUREMENTS.md` | every measured claim with its interval and caveats, including the two that were wrong when first recorded |
| `docs/BOT_MODEL.md` | chess.com's own bot, decoded from its shipped client: king to a corner on move one, material ignored while drafting |
| `rules.py` | placement legality, points, FEN emission, validation |
| `pool.py` | archetype seeds, mutation and crossover operators |
| `arena.py` | fills the payoff matrix by playing army against army, resumable. `--draft WHITE:BLACK` plays the placement phase instead of stamping two finished armies |
| `solve.py` | equilibrium mix, best response, exploitability |
| `expand.py` | the double-oracle pool expansion loop; `--seed-bot` breeds against the modelled opponent |
| `stats.py` | Elo, confidence intervals, SPRT |
| `play.py` | the drafting policy: realises an army against an opponent, then hands the position to the engine. `--opponent bot` is chess.com's own setup policy |
| `psearch.py` | **searches the placement game**: iterative-deepening alpha-beta, leaf = static exchange + agreement with the solved equilibrium. This is the reply-to-what-they-placed engine |
| `draft.py` | plays the placement phase out between two strategies (`plan` or `search`) and returns the handoff |
| `match.py` | paired full-game A/B for a drafting change |
| `duel.py` | engine versus engine over setup positions, for validating a referee |
| `watchdog.py` | restarts a stalled campaign; expand.py hangs intermittently |
| `adapt.py` | what happens when the opponent learns you, simulated on the matrix |
| `Constants.h`, `movegen.c`, `eval.c`, `search.c` | the C move generator and a minimal search, used as a cross-check rather than to play |
| `cengine.py`, `cuci.py` | ctypes binding and the UCI front end |
| `selftest.py` | run before every commit |
| `campaigns/` | campaign state and gate results, in git on purpose |

## Getting started

```bash
./setup.sh
```

Installs dependencies, builds the C move generator and checks it with a perft
oracle, and verifies a UCI engine answers `uci`. You also need
`fairy-stockfish` on your PATH -- it is the referee for everything below, and
vanilla Stockfish is not a substitute (see [Choosing the
referee](#choosing-the-referee-which-mattered-more-than-anything-else)).

```bash
python3 selftest.py
```

**Play the answer against something.** One game each colour, setup through
result, using the shipping army:

```bash
python3 play.py --opponent bot --engine fairy-stockfish --max-pieces 0
```

`--opponent bot` is chess.com's own setup policy, rebuilt from its shipped
client. `--opponent stdin` reads placements as `@Qd1` tokens instead, which is
how a real game elsewhere gets driven; `--live` relays a game move by move.

**Re-derive the answer from scratch.** Hours, resumable, Ctrl-C checkpoints:

```bash
python3 expand.py --state ~/expand.json --engine fairy-stockfish --max-pieces 0 --rounds 30 --challengers 32 --pairs 4 --screen-pairs 2 --workers 0 --final-games 400
```

That is the double oracle: breed challengers, screen them against the current
equilibrium, admit the survivors, re-solve, repeat. It ends on a 400-pair gate
against the twelve hand-written archetypes.

**Fill a payoff matrix directly**, if you have a pool of armies and just want
them scored against each other:

```bash
python3 arena.py --out ~/matrix.json --pool ~/armies.json --engine fairy-stockfish --max-pieces 0 --nodes 20000 --pairs 4 --workers 0
```

**Measure setups that were actually drafted** rather than stamped together --
the two sides react to each other, and a cell means "these two plans played",
not "these two armies were glued to a board". Use the SAME mode on both sides
or a colour-swapped pair also swaps strategies and the matrix cannot be
antisymmetrised (arena warns).

```bash
python3 arena.py --out ~/drafted.json --pool ~/armies.json --engine fairy-stockfish --max-pieces 0 --nodes 20000 --pairs 100 --draft search:search --draft-depth 1 --workers 0
```

**Ask what to place next in a live game**, searching the placement tree under a
wall clock. Each depth prints as it lands, so there is always a move even if
the budget runs out -- chess.com forfeits at roughly 20 seconds.

```bash
python3 relay.py --color white --search --budget 3 @Bb2 @Qd8
```

**A/B a drafting change** over full games, paired so the opponent and colour
cancel:

```bash
python3 match.py --target campaigns/champion_fsf.json --pool campaigns/expand_fsf.json --out ~/ab.json --engine fairy-stockfish --max-pieces 0 --games 1200 --workers 0
```

`expand.py` stalls intermittently at a multiprocessing teardown; wrap it in
`python3 watchdog.py -- ...` to have that cost a restart rather than a night.

## Known limits

* **The baseline is weak.** +462 Elo is against hand-written archetypes, one
  of which scores 0.016 against the field. It is not a measurement against
  strong opposition.
* ~~9% of gate pairs are unmeasured~~ **CLOSED for the champion gate**: rerun on
  fairy-stockfish at no ceiling it is 480 of 480 pairs, zero piece-count skips,
  and archetype 3 measured for the first time. Every OTHER campaign in
  `campaigns/` is still Stockfish-refereed and still has the holes, including
  the 87-setup pool the champion was bred from, which is the one that matters
  and has not been re-run.
* ~~The champion gate is 20,000 nodes only~~ **now also measured at 200,000**:
  the champion scores 0.9324, +455.82 [+423.88, +493.83], and the 13-army
  equilibrium is pure on it. Twelve bishops are not a shallow-search artifact.
  The depth-only comparison is now done on a matched pool and comes out flat:
  +0.0023 +/- 0.0129 over 440 identically-indexed pairs. One caveat stands,
  and it is the important one: the field is still the same twelve hand-written
  archetypes, so a deeper search has only confirmed dominance over weak
  opposition. Depth was never the weak link in that claim -- the field is.
* **One whole archetype is missing from the depth gate.** Archetype 3 lost all
  40 of its pairs to Stockfish's piece ceiling, so 11 of 12 opponents are
  measured rather than a scattered 9%. Only one archetype offers real
  resistance at depth (0.6312); the rest sit above 0.87.
* **Re-targeting is NOT confirmed on the pool that ships.** On the clean
  fairy-stockfish pool it measures **+5.91 Elo [-0.18, +12.00]** over 3,000
  pairs with nothing unplayable, SPRT LLR +1.619 -> CONTINUE. The interval
  still includes zero; extending from 1,200 pairs moved the estimate down from
  +7.53 rather than up. It was +24.63 on the old pool, but that pool was itself built
  under the censored matrix, so the two are not a fair before-and-after. The
  toggle stays on only because the test is still trending rather than flat;
  if it settles NULL the default should become `--no-pool`.
* ~~The bot gate is withdrawn~~ **replaced**: `campaigns/gate_bot_fsf_200.json`
  measures 0.92 as White and 0.94 as Black on fairy-stockfish. The superseded
  `campaigns/gate_bot_200.json` is kept only as the record of what a weak
  referee does to a number. What the replacement does NOT fix: one setup per
  colour, so drafting variance is still unmeasured.
* **Best-response re-targeting still gives up a forced setup mate**, because
  the payoff matrix is measured by playing the *chess* phase from finished
  armies and cannot see setup tactics. It is on anyway, and CONFIRMED on the
  87-setup pool the defaults use: **+24.63 Elo [+18.06, +31.22]** over 1,187
  pairs, SPRT [0,4] LLR +8.101 -> ACCEPT H1 at full budget. `--no-pool`
  disables it. Teaching the matrix about the placement phase is the open work
  here, and would probably recover that forced mate on top.
* **That number is the re-run after the handoff turn-order fix**, and it was a
  real re-run: `match.py` plays from `handoff_fen()`, and the fix changed the
  outcome of **494 of the 1,187 pairs**. The pre-fix reading on the same pool
  was +29.63 [+23.46, +35.83]; the ranges overlap heavily, so the fix did not
  measurably change the effect, but the point estimate is about 5 Elo lower
  and only the post-fix one describes the shipping code. **The 19-setup pool's
  +17.13 [+12.36, +21.91] has NOT been re-run** and is still a pre-fix number.
  The champion gate never shared the problem: `arena.py` goes through
  `setup_fen()`, which did not change.
* **Re-targeting survives a 10x deeper search**, which is the only longer-TC
  result in the repo. At 200,000 nodes on the same 1,187 pairs it measures
  **+29.93 Elo [+23.66, +36.21]**, LLR +11.037 -> ACCEPT H1, against +24.63
  [+18.06, +31.22] at 20,000. The ranges overlap, so the honest reading is
  "no measured decay with depth", not "it gets better". 409 of the 1,187 pairs
  came out differently at the deeper search, so the two are genuinely separate
  instruments and are not pooled.
* ~~One rule is assumed~~ **Verified on the live board 2026-08-05**: a king
  may not be placed onto an attacked square, and non-king pieces may. The
  lockout tactic rests on real rules, not an assumption.
* **The chess phase does not always start with White**, which cost a live game
  before it was measured. A finished side *passes* rather than being skipped
  (the server writes `P` in the move list), so the turns keep alternating and
  whoever follows the final placement moves first. Verified over a full
  26-placement game against chess.com's own bot: White placed last, Black
  opened with `Qh3+`, and our FEN matched the server's byte for byte.
  `handoff_fen()` is the only correct source for this; `setup_fen()` gives
  White the move by convention because two finished armies carry no placement
  order.
* **The payoff matrix therefore always hands White the tempo**, while a real
  game hands it to whichever side the placement count lands on. Both colours
  are played in every pair so it cancels in aggregate, but the armies were
  never selected for the parity they will actually get. Unmeasured.
* **The pool is finite.** 87 armies after 21 expansion rounds, and it stopped
  because it filled rather than because it converged: at `--max-pool` every
  further round only prunes and re-admits at rising cost while exploitability
  has been pinned at 0 throughout. The equilibrium is now genuinely mixed over
  11 setups, which is a better sign than the old pure one, but the pool is
  still a sample of the space rather than a cover of it.
