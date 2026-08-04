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

**Bishops dominate.** The solved army is nine bishops, a queen and three
pawns, bred by the expansion loop rather than hand-written:

```
  3 | B B B B B . . .
  2 | . . . . P P P .
  1 | B B B Q K . B .
      a b c d e f g h
```

Against the twelve hand-written archetypes, sampled uniformly:

```
Games   | 1,820 of 2,000 (90 pairs unplayable)
Score   | 0.9104 +/- 0.0106
W/D/L   | 1,555 / 204 / 61
Elo     | +402.85 +/- 22.7   [+381.33, +426.82]
SPRT    | [0,4] LLR +79.738 -> ACCEPT H1
TC      | 20,000 nodes fixed, 15% per-game jitter
Machine | Mac14,9 arm64, macOS, Stockfish 17
```

Read that as "much better than hand-written guesses", not "strong". The
archetype field includes deliberately bad armies, and one of them scores
0.016. See [Known limits](#known-limits).

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
pieces survive, 38 crash). That is why 9% of gate pairs are still unmeasured.

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
| `rules.py` | placement legality, points, FEN emission, validation |
| `pool.py` | archetype seeds, mutation and crossover operators |
| `arena.py` | fills the payoff matrix, engine vs engine, resumable |
| `solve.py` | equilibrium mix, best response, exploitability |
| `expand.py` | the double-oracle pool expansion loop |
| `stats.py` | Elo, confidence intervals, SPRT |
| `play.py` | drafts an army and plays the game out |
| `Constants.h`, `movegen.c` | the C core |
| `cengine.py` | ctypes binding to it |
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
python3 play.py --target campaigns/champion_v2.json --opponent classic
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

* **The baseline is weak.** +403 Elo is against hand-written archetypes, one
  of which scores 0.016 against the field. It is not a measurement against
  strong opposition.
* **9% of gate pairs are unmeasured**, and more games will not fix it. Those
  are the highest-piece-count matchups that Stockfish cannot survive. It is a
  coverage gap, not noise.
* **Everything is at 20,000 nodes.** Whether nine bishops still dominate at a
  longer time control is untested, and Stockfish may simply be mishandling
  unusual material.
* **Best-response re-targeting is off by default and measured negative.** It
  drops from 3/4 to 2/4 setup-phase wins, giving up a forced mate, because
  the payoff matrix is measured by playing the *chess* phase from finished
  armies and cannot see setup tactics. Enable with `--pool`; judging it
  properly needs a full-game match.
* **One rule is assumed, not verified**: that a king may not be placed onto
  an attacked square. The lockout tactic depends on it. It needs one
  placement on chess.com's analysis board to settle.
* **The pool is small.** 19 armies after five expansion rounds. The
  equilibrium is a pure strategy, which says more about the pool than about
  the game.
