# Setup Chess — Rules (chess.com variant)

Compiled 2026-08-04. Every line carries a source tag; anything unsourced is an
explicit `ASSUMPTION:` line.

## Sources

| Tag | Source |
|---|---|
| [DLG] | Official variant dialog at <https://www.chess.com/variants/setup-chess> (game-start popup, read 2026-08-04) |
| [AB] | Empirical tests performed on the official analysis board <https://www.chess.com/variants/setup-chess/analysis> (2026-08-04; each claim names the test) |
| [HDR] | PGN4 / `StartFen4` header emitted by that analysis board: `{'setupPoints':(39,0,39,0),'pawnBaseRank':5,'dim':'8x8','bank':('rP,rB,rN,rR,rQ,rK',...)}`, `[RuleVariants "EnPassant Play4Mate Setup=39"]` |
| [BLOG] | <https://www.chess.com/blog/Fredericsiow/strategy-of-chess-com-variant-setup-chess> |
| [FRM] | <https://www.chess.com/clubs/forum/view/how-to-make-different-setup-chess-variants> ("Normal setup chess - placement on 1-3 rank, 5 types of pieces, no matter how many pieces of each type, 39 points to setup") |
| [WIKI] | chess-variants.fandom.com piece pages, "Setup Value" field (community wiki for chess.com's variants server) |

Primacy: [DLG]/[AB]/[HDR] are primary (official UI and engine behaviour,
observed directly); [BLOG]/[FRM]/[WIKI] are secondary corroboration.

## Budget and piece costs

* Each player has **39 material points** to spend. [DLG] "Each player has 39 material points to spend"; [HDR] `Setup=39`, `setupPoints:(39,...)`; [FRM]
* Costs: **P=1** [AB: placing a pawn moved the counter 30→29; WIKI], **N=3** [WIKI; BLOG "3 points for a knight"], **B=3** [WIKI "worth five points, but only costs three points in Setup Chess"; BLOG], **R=5** [AB: 21→16; WIKI], **Q=9** [AB: 39→30; WIKI].
* The **king is free** (costs 0) and there is exactly one per side — the piece bank lists one K and it disappears from the tray once placed; every other type stays available. [AB: counter unchanged on @Ke1/@Ke6; HDR bank; WIKI K "Setup Value 0"]
* **Duplicates are unlimited** for the 5 buyable types, bounded only by points (and board space). [AB: three queens placed for one side; FRM "no matter how many pieces of each type"; BLOG "36 points for 4 queens"]
* Leftover points: there is **no pass and no conversion** — normal moves stay illegal while either player has points left, so the budget is effectively spend-to-zero. [AB: with 21 points unspent, moving an already-placed queen was rejected; play became legal exactly when both counters hit 0]
  * ASSUMPTION: behaviour when a player *cannot* spend remaining points (zone full) or lets the setup clock run out is unverified; presumed forfeited/auto-handled by the server. Irrelevant to our engine, which always spends to zero with the zone far from full.

## Placement squares

* Pieces go on **your first three ranks** (White 1–3, Black 8–6), any file. [DLG] "Pieces can be placed on the first 3 ranks"; [BLOG]; [FRM]
* **Pawns only on ranks 2 and 3** (Black: 7 and 6) — **never rank 1**. [DLG] "pawns on the 2nd and 3rd ranks"; [AB: dragging a pawn to a1 was rejected] → the Stockfish handoff is safe; no pawn-on-back-rank FEN can arise.
* The king may go on any of the three ranks, **including rank 3** — legal but tactically dangerous (see setup tactics). [AB: @Ke6 accepted for Black]
* ASSUMPTION: a placement must land on an empty square (no capture-by-placement). Never observed otherwise; the UI never offered an occupied square.

## Placement protocol — alternating and visible

* Players place **one piece per turn, alternating, White first**: W, B, W, B… [DLG] "players set up their pieces and pawns, one by one"; [AB: after each White placement the turn indicator switched to Black and back]
* Placements are **recorded as moves in the shared move list** (notation `@Qd1`, `@Ke8` …) and are therefore **visible to the opponent as they happen**. This is a perfect-information placement game, not hidden/simultaneous. [AB: move list `1. @Qd1 @Qd8 2. @Pa2 @Ke6 3. @Qe1+#`; BLOG describes reacting to the opponent's placements]
* No normal move may be played until both players have finished spending. [AB: mid-setup move rejected]
* After the last placement, **White moves first** in the chess game. [AB]
* ASSUMPTION: if one player finishes spending before the other, the other continues placing consecutively. Untested (both test armies finished simultaneously); `play.py` will follow the server's turn signal rather than assume.

## Setup-phase tactics (load-bearing and easy to miss)

* A placement may give **check** — and the setup phase has real terminal states: if the checked player cannot answer the check **with a placement** (the king cannot move during setup, and only squares inside your own three ranks are placeable), it is **checkmate during setup**. [AB: `@Qe1+#` against a king on e6 ended the game instantly — the blocking squares e2–e5 all lie outside Black's zone; a follow-up placement attempt was rejected; BLOG: "my opponent setup his king at the third line, then I just setup a rook that is checking his king: Computer: Checkmate, white win."]
* Corollary: while a check *can* be blocked inside your zone, you are forced to spend a placement doing so. [BLOG "you must setup a piece that will blocking the check"]
* Consequence for the engine: the drafting layer is not purely combinatorial — placement legality depends on check state, and the placement tree contains forced lines and mates. `rules.py` must implement in-setup check/mate logic.

## Post-setup rules

* Once both armies are placed, play is ordinary chess: [DLG] frames setup as "before the game"; [AB: normal single steps, double steps and captures all behaved standardly]
* **No castling**, ever (there are no "original" rook/king squares). [AB: Ke1→g1 with an untouched Rh1 and empty f1/g1 was rejected]
* **En passant: enabled**, standard rules. [HDR `RuleVariants "EnPassant ..."`]
* **Pawn double-step: only from rank 2** (Black: rank 7) — a pawn placed on rank 3 gets **no** double-step. [AB: d2–d4 and d7–d5 accepted; c3–c5 rejected; HDR `pawnBaseRank:5` = rank 2 in the server's +3-offset internal coordinates]
* Win by checkmate (server flag `Play4Mate`). [HDR]
* ASSUMPTION: promotion is standard (rank 8/1, choice of Q/R/B/N). Not reachable in the test games; no source suggests otherwise.
* ASSUMPTION: draw rules (stalemate, threefold, 50-move) are standard. Untested.

## Time control

* The setup phase runs on its own clock, displayed as **1:00 per player**, separate from the main game clock (tested with a 5|2 game TC). [AB: both clocks showed 1:00 during setup]
* ASSUMPTION: the 1:00 is fixed per player for the whole setup phase regardless of main TC, and expiry behaviour is unknown (needs one live game to confirm). Planning number for the engine: **≈60 s for all our placement decisions**, so the placement policy must be precomputed or near-instant.

## Internal format notes (for `play.py`, non-normative)

* The variants server is the 4-player-chess engine; games are PGN4 with a `StartFen4` header; our 8×8 board is embedded in its 14×14 coordinates at offset +3 (a1 = internal d4; placement `@Qd1` = `@rQ-g4`). [HDR/AB]
* Placement notation in the move list is `@<Piece><square>`, checks/mates annotated as usual (`@Qe1+#`). [AB]
