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

## Rules verified live, not assumed

Played against chess.com's own board and client:

- a king may **not** be placed on an attacked square; every other piece may
- checkmate during setup ends the game (`@Qe1+#`)
- ending setup without a king loses outright, so covering every empty square in
  the opponent's zone wins without a checkmate
- a finished side **passes**, and the chess phase starts with whoever follows
  the last placement

Full sourcing in `docs/RULES.md`, opponent model in `docs/BOT_MODEL.md`.
