# chess.com's Setup Chess bot, decoded

Derived from the shipped variants client (`variants.js`, the same build the
browser runs) plus its own console output. Behaviour is described here rather
than their code being copied; expressions are quoted only as evidence, the way
docs/RULES.md cites the engine.

This is an OPPONENT MODEL, not a rule set. Rules live in docs/RULES.md.

## The setup phase uses a different eval from normal play

Both are a sum of the same named terms, but the setup branch drops two of them
and reweights two others:

```
setup:   mates + mobility/2 + coord + pinned + discovs + king*2 + checkable + exposed + hill
play:    mates + myMaterial + oppMaterial + mobility + coord + offer + pinned + discovs + king + checkable + exposed + hill
```

Three differences, all load-bearing:

* **Material is absent from the setup eval entirely.** `myMaterial` and
  `oppMaterial` are computed, and they appear in the debug object the console
  prints, but they are not added to the score while placing. The bot does not
  value the material it buys.
* **Mobility is halved** during setup and full during play.
* **King safety is doubled** during setup, and `offerEval` is dropped.

## It places its king first, in a corner

After the sum, a piece-type bonus is applied. The king clause is
`+100 * Y(player)`, where `Y` is the distance from the king to the nearest of
four target squares, computed as `(dx+dy)/2 + max(dx,dy)/2`.

Those target squares are `g7 g8 h7 h8` in the engine's 14x14 coordinates. An
8x8 variant is embedded at offset +3 (a1 = engine d4, established from the
PGN4 header in docs/RULES.md), so they are **d4 d5 e4 e5: the centre of the
8x8 board**.

So the bonus rewards the king being FAR from the centre, and at roughly +450
it dwarfs every other term, which sit near +/-2.

Predicted against observed, from the console on an otherwise empty board:

| King square | Engine sq | Predicted 100*Y | Observed |
|---|---|---|---|
| a1, h1 | d4, k4 | 450.00 | 447.69 |
| a2, b1, g1 | d5, e4, j4 | 400.00 | 398.75 |
| d1, e1 | g4, h4 | 300.00 | - |

The residual is the other eval terms. **Its king goes to a corner, on its
first placement.**

## Its piece preferences are the opposite of ours

The remaining clauses of the same if-else chain, applied to the piece being
dropped:

| Piece | Adjustment |
|---|---|
| King | +100*Y (dominates everything) |
| Pawn | +0.2 |
| Knight | 0 (no clause; falls through) |
| Bishop | -0.2 |
| Queen | -0.3 |
| Rook | -0.4 |

Combined with material being absent from the sum, the bot has **no reason to
buy expensive pieces and a small standing bias against them**. It drifts
toward pawns and knights.

Our own expansion loop converged on the opposite: twelve bishops. That is a
plausible part of why the solved mix scores 0.93 against hand-written armies.

## What this changes for us

* **The lockout tactic never fires against their bot.** It needs an opponent
  who holds the king back; this one places it immediately. `play.py` already
  handles this correctly without a change, because the hunt requires their
  king to be unplaced AND their budget to be low, which never co-occur here.
  Against humans, the blog advice ("do not setup your king first") means the
  lockout stays live.
* **Their king square is known from move one**, and it is a corner. Setup
  check and mate pressure has a fixed target from the start rather than
  needing to wait for the king to appear.
* **Unverified here**: whether the live opponents in the pool are this bot or
  humans. Everything above is the bot's behaviour, read from its code and
  confirmed against its own debug output. It is not a claim about the human
  population.
