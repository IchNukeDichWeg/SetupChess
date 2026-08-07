# Setup Chess

An engine for the chess.com variant [Setup Chess](https://www.chess.com/variants/setup-chess):
before play, each side spends **39 points** placing an army on its own first
three ranks (P=1, N=3, B=3, R=5, Q=9, king free and mandatory, duplicates
unlimited). Once both armies are down, it is ordinary chess.

The interesting half is the drafting. This repo builds a candidate pool of
armies, measures them against each other by playing games, solves for the
best mix, and plays the placement phase with the tactics that phase actually
has -- including two ways to win before move one.

Python is the harness; the move generator is C.

## What it found

**Bishops dominate, and it is not an artifact.** The solved army is eleven
bishops and six pawns, bred by the expansion loop rather than hand-written:

```
  3 | B B B B B P . .
  2 | B B . P P P P P
  1 | B B B B K . . .
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
Score   | 0.9425 +/- 0.0116
W/D/L   | 712 / 84 / 4
Elo     | +485.85  [+451.79, +527.08]
SPRT    | [0,4] LLR +72.357 -> ACCEPT H1
Pool    | 60 setups, exploitability 0 in every round
TC      | 20,000 nodes fixed, 15% per-game jitter
Machine | Mac14,9 arm64, macOS, fairy-stockfish 14.0.1
```

Four losses in 800 games. Read that as "much better than hand-written
guesses", not "strong": the archetype field includes deliberately bad armies
and one of them scores 0.016. See [Known limits](#known-limits).

### The superseded Stockfish numbers

Kept because they are what the earlier commits measured, and because the two
sets are **different instruments and cannot be differenced**. The old champion
scored 0.9346, +462.06 [+440.79, +485.88] over 1,820 of 2,000 games, with 90
pairs unplayable, on an 87-setup pool after 21 rounds.

**It holds at ten times the depth.** Every number in this section and the two
after it describes the **PREVIOUS** champion, 12 bishops and 3 pawns, because
that is what `campaigns/gate_depth_pool.json` holds at index 0. They have not
been re-run for the current champion. Re-gated at 200,000 nodes, that army
alone against the archetype field:

```
Pairs   | 440 of 480 (archetype 3 unplayable, all 40 pairs)
Score   | 0.9324 +/- 0.0126
Pairs   | 341 swept / 79 at 0.75 / 20 drawn / 0 lost
Elo     | +455.82 +/- 35.0   [+423.88, +493.83]
SPRT    | [0,4] LLR +60.252 -> ACCEPT H1
TC      | 200,000 nodes fixed, 15% per-game jitter
Machine | Mac14,9 arm64, macOS, Stockfish 17, 10 workers
```

Not one pair lost in 880 games. Solving the 13-army matrix at this depth puts
**all the equilibrium weight on the champion**, exploitability 0.0000, so it is
a best response to the whole archetype field and not merely a good average.

**Measured again with no blind spot.** Every gate above is refereed by vanilla
Stockfish, which segfaults on high piece counts, so all of them silently drop
580 of 3,380 pairs and archetype 3 entirely. Re-run on `fairy-stockfish` at no
piece ceiling, the PREVIOUS champion against all **twelve** archetypes:

```
20k    | 0.9094 +/- 0.0144   +400.60 [+372.18, +433.42]   349 swept / 41 drawn
200k   | 0.9297 +/- 0.0125   +448.52 [+417.80, +484.77]   371 swept / 26 drawn
Pairs  | 480 of 480, ZERO piece-count skips (was 420)
Solve  | pure on the champion at both depths, exploitability 0.0000, nothing dropped
```

Archetype 3, unmeasurable for the entire project until now, turns out to be
mid-pack at 0.8938. The hardest opponent is still archetype 5 at 0.6375. The
champion loses exactly one game across 960, at 20,000 nodes.

The 160 remaining skips are `opposite_check` positions, which are unplayable by
rule rather than by engine, so this is full coverage.

**Depth behaves differently on this referee.** Paired over the identical 480
pairs, 200k minus 20k is **+0.0203 +/- 0.0136**, which excludes zero;
+0.0170 +/- 0.0135 with archetype 3 removed, so it is not the new opponent
driving it. On Stockfish the same comparison was flat (+0.0023 +/- 0.0129).
Two different referees disagreeing about whether depth helps is a reason to
trust neither number far, and the effect is small either way.

Still not differenceable against the +462 above -- that gate sampled the
*solved mix*, this one plays the *champion army alone* -- so the same pool was
re-run at 20,000 nodes to isolate depth. **Depth changes nothing:**

```
20k    | 0.9301   +449.66 [+418.62, +486.36]
200k   | 0.9324   +455.82 [+423.88, +493.83]
Paired | +0.0023 +/- 0.0129 over 440 identically-indexed pairs
Games  | 122 of 440 pairs came out differently
```

Both node counts put the entire equilibrium on the champion, exploitability
0.0000, and both drop archetype 3 for the same piece-count reason. A tenth of
the search buys the same verdict, so the remaining gap between +449.66 and
+462.06 is the mix-versus-champion difference, not depth.

## Against a model of the real opponent

The archetypes are hand-written guesses. `play.py --opponent bot` is
chess.com's own setup policy rebuilt from its shipped client
(`docs/BOT_MODEL.md`): king to a corner on move one, then 16 pawns and 7
knights, because material is absent from its setup eval entirely.

> These numbers are the **PREVIOUS** champion's: they were measured when
> `DEFAULT_TARGET` still pointed at `champion_own.json`. The current champion
> has not been gated against the bot.

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
python3 expand.py --state campaigns/expand_bot_fsf.json --seed-bot \
  --engine fairy-stockfish --max-pieces 0 --rounds 30 --workers 0 \
  --final-games 400
```

This starts a FRESH pool (the twelve archetypes plus the pinned wall), so it
inherits nothing from `expand_own.json`. Different referee from every campaign
above, so it is a different instrument, a separate state file, and its gate
number is not comparable with the Stockfish-refereed ones. Comparing the old
champion with whatever this produces needs a head-to-head under a single
referee, not a subtraction.

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

## Why a C core rather than Stockfish

Setup Chess positions are legal in the variant and illegal in *standard*
chess: sixteen pawns, nine bishops, up to 48 pieces on the board. python-chess
rejects them by ordinary-chess history rules, and Stockfish's data structures
assume 32 pieces -- the 42-piece pawn-wall mirror answers `depth 4` fine and
then **segfaults at 20,000 nodes** (measured threshold on this build: 36
pieces survive, 38 crash). The 40-piece champion-versus-bot matchup dies the
same way, exit code -11. That is why 9% of gate pairs are unmeasured in every
Stockfish-refereed campaign here.

> **Largely superseded.** `fairy-stockfish` 14.0.1 plays all of it: the
> 40-piece matchup, the 42-piece wall, and the 48-piece mirror of the bot's
> army, which is this variant's theoretical maximum. It defaults to
> `UCI_Variant chess`, agreed with vanilla Stockfish on three forced-tactic
> oracles, and costs 34.9 ms/move against our core's 15.6 ms at 20,000 nodes on
> the 40-piece position. It is a Stockfish 14 derivative, so it is vastly
> stronger than our core's -327 Elo.
>
> Pass `--engine fairy-stockfish --max-pieces 0` to any harness here. The C
> core stays as an independent cross-check and the perft oracle; it should not
> referee a measurement again. This also removes the case for training an
> NNUE, which existed only because nothing strong could play these positions.

The C core is bitboard-based, so it has no piece-count ceiling:

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
| `docs/BOT_MODEL.md` | chess.com's own bot, decoded from its shipped client: king to a corner on move one, material ignored while drafting |
| `rules.py` | placement legality, points, FEN emission, validation |
| `pool.py` | archetype seeds, mutation and crossover operators |
| `arena.py` | fills the payoff matrix, engine vs engine, resumable |
| `solve.py` | equilibrium mix, best response, exploitability |
| `expand.py` | the double-oracle pool expansion loop; `--seed-bot` breeds against the modelled opponent |
| `stats.py` | Elo, confidence intervals, SPRT |
| `play.py` | drafts an army and plays the game out; `--opponent bot` is chess.com's own setup policy |
| `match.py` | paired full-game A/B for a drafting change |
| `duel.py` | engine versus engine over setup positions |
| `Constants.h`, `movegen.c`, `eval.c`, `search.c` | the C core |
| `cengine.py`, `cuci.py` | ctypes binding and the UCI front end |
| `selftest.py` | run before every commit |
| `campaigns/` | campaign state and gate results, in git on purpose |

## Getting started

```bash
./setup.sh
```

Installs dependencies, builds the C core and checks it with a perft oracle,
and verifies a UCI engine answers `uci`.

```bash
python3 selftest.py
```

```bash
python3 play.py --opponent classic
```

Plays one game each colour, setup through result. `--opponent stdin` reads
placements as `@Qd1` tokens for driving a game elsewhere.

Longer jobs, which take minutes to hours:

```bash
python3 arena.py --out ~/matrix.json --nodes 20000 --pairs 4 --workers 0
```

```bash
python3 expand.py --state ~/expand.json --rounds 30 --challengers 32 --pairs 4 --screen-pairs 2 --workers 0 --final-games 400
```

Both are resumable; Ctrl-C checkpoints and exits cleanly.

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
* **Only the headline has been measured for the CURRENT champion.** The depth
  gates, the no-blind-spot gate and the bot gate all describe the previous
  12-bishop army, because they predate it. They remain valid measurements of
  what they measured; they are simply not about the army that now ships.
  `campaigns/gate_depth_pool_fsf.json` is the pool for redoing them.
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
