# Setup Chess -- Rules (chess.com variant)

Compiled 2026-08-04. Every line carries a source tag; anything unsourced is an
explicit `ASSUMPTION:` line.

## Sources

| Tag | Source |
|---|---|
| [DLG] | Official variant dialog at <https://www.chess.com/variants/setup-chess> (game-start popup, read 2026-08-04) |
| [AB] | Empirical tests performed on the official analysis board <https://www.chess.com/variants/setup-chess/analysis> (2026-08-04; each claim names the test) |
| [HDR] | PGN4 / `StartFen4` header emitted by that analysis board: `{'setupPoints':(39,0,39,0),'pawnBaseRank':5,'dim':'8x8','bank':('rP,rB,rN,rR,rQ,rK',...)}`, `[RuleVariants "EnPassant Play4Mate Setup=39"]` |
| [LIVE] | A full setup phase played out against chess.com's own bot on the analysis board (2026-08-05), 26 placements, relayed move by move through `play.py --live`; the resulting FEN matched the server's byte for byte |
| [NEWS] | Official launch announcement <https://www.chess.com/news/view/chesscom-launches-duck-seirawan-setup-chess> |
| [ENG] | Live variants client engine <https://www.chess.com/r2/client-packages/variants/2026.8.1/variants.js> (minified; quotes are the relevant expressions) |
| [ENG22] | Archived 2022 build of the same client, <https://web.archive.org/web/20221012141324js_/https://www.chess.com/bundles/app/js/variants-beta.client.ad2146db.js> -- used only where [ENG] agrees |
| [BLOG] | <https://www.chess.com/blog/Fredericsiow/strategy-of-chess-com-variant-setup-chess> |
| [FRM] | <https://www.chess.com/clubs/forum/view/how-to-make-different-setup-chess-variants> ("Normal setup chess - placement on 1-3 rank, 5 types of pieces, no matter how many pieces of each type, 39 points to setup") |
| [WIKI] | chess-variants.fandom.com piece pages, "Setup Value" field (community wiki for chess.com's variants server) |

Primacy: [DLG]/[AB]/[HDR]/[NEWS]/[ENG] are primary (official UI, official
announcement, shipped engine code, and behaviour observed directly);
[ENG22]/[BLOG]/[FRM]/[WIKI] are secondary corroboration. Where the 2022 and
2026 engine builds disagree, [ENG] wins.

## Budget and piece costs

* Each player has **39 material points** to spend. [NEWS] "Players start the game with 39 material points each."; [DLG] "Each player has 39 material points to spend"; [HDR] `Setup=39`, `setupPoints:(39,...)`; [FRM]
* Costs: **P=1**, **N=3**, **B=3**, **R=5**, **Q=9**, **K=0**. [ENG/ENG22 setup-cost function: `if("K"===ss)return 0; ... if("B"===ss){...if(+ss[0]*+ss[1]<100)return 3}` -- i.e. the bishop's generic `pointValue:5` is overridden to 3 on boards under 100 squares, so 3 on 8×8 -- over the base table `P:{pointValue:1}, N:{pointValue:3}, R:{pointValue:5}, Q:{pointValue:9}`; AB: Q 39→30, R 21→16, P 30→29, K unchanged; WIKI; BLOG "36 points for 4 queens"]
* The **king is free and mandatory**: exactly one per side, removed from the bank once placed, and a player who ends setup without one **loses outright**. [ENG22 `"failed to set up his king"` → `followup:"#"`; ENG bank `("PBNRQ"+(_s?"K":""))`; AB: counter unchanged on @Ke1/@Ke6]
* **Duplicates are unlimited** for the 5 buyable types, bounded only by points (and board space). [ENG bank filter is per-type affordability only; FRM "no matter how many pieces of each type"; AB: three queens placed for one side; BLOG]
* **Leftover points are forfeited**, never converted: when a player has no affordable legal placement left, the engine empties their bank, zeroes their points and marks their setup complete. [ENG22 `(Qi[Po]=[],mi[Po]=0,fi[Po]=!0,ss.followup="P")`] Otherwise there is no pass -- normal moves stay illegal while a player still has spendable points. [AB: with 21 points unspent, moving an already-placed queen was rejected; play became legal exactly when both counters hit 0]

## Placement squares

* Pieces go on **your first three ranks** (White 1–3, Black 8–6), any file. [NEWS] "Players can place pawns on their second and third ranks and all the other pieces on their first three ranks."; [DLG]; [ENG: placement ceiling is `pawnBaseRank+1` with `pawnBaseRank=2`, and the drop scan filters only on rank/occupancy/pawn-rank, never on file]; [FRM]; [BLOG]
* **Pawns only on ranks 2 and 3** (Black: 7 and 6) -- **never rank 1**. [NEWS as quoted above; DLG "pawns on the 2nd and 3rd ranks"; ENG22 pawn-drop rejection below the base rank; AB: dragging a pawn to a1 was rejected] → the Stockfish handoff is safe; no pawn-on-back-rank FEN can arise.
* The king may go on any of the three ranks, **including rank 3** -- legal but tactically dangerous (see setup tactics). [AB: @Ke6 accepted for Black; BLOG]
* ASSUMPTION: a placement must land on an empty square (no capture-by-placement). Never observed otherwise; the UI never offered an occupied square, and the drop scan filters on occupancy [ENG].

## Placement protocol -- alternating and visible

* Players place **one piece per turn, alternating, White first**: W, B, W, B… [NEWS] "They take turns setting up the initial position of their pieces, spending their material points in any way they want."; [DLG] "players set up their pieces and pawns, one by one"; [AB: after each White placement the turn indicator switched to Black and back]
* Placements are **recorded as moves in the shared move list** (notation `@Qd1`, `@Ke8` …) and are therefore **visible to the opponent as they happen**. This is a perfect-information placement game, not hidden/simultaneous. [AB: move list `1. @Qd1 @Qd8 2. @Pa2 @Ke6 3. @Qe1+#`; NEWS; BLOG describes reacting to the opponent's placements]
* No normal move may be played until both players have finished spending. [AB: mid-setup move rejected] Then play proceeds as ordinary chess **with the side after the last placement to move -- NOT always White**. [LIVE, 2026-08-05: White placed 16th and last (`16. @Pa2`) and Black immediately answered `Qh3+`, a chess move. The alternation below runs through the passes, so the parity of the total placement count decides who opens.] Sources say only "they play the game normally" [NEWS], which is where the White-to-move guess came from.
* **A finished player passes; the other does not get consecutive turns.** [LIVE, 2026-08-05: the server move list reads `11. @Bf3 P  12. @Bg2 P  13. @Bb2 P  14. @Pf2 P  15. @Pg3 P  16. @Pa2 Qh3+` -- White places alone and Black is recorded as `P` on every one of its turns.] This was previously an ASSUMPTION of consecutive placement; the board state is identical either way, but the turn parity at handoff is not, which is why it mattered.

## Setup-phase tactics (load-bearing and easy to miss)

* A placement may give **check** -- and the setup phase has real terminal states: if the checked player cannot answer the check **with a placement** (the king cannot move during setup, and only squares inside your own three ranks are placeable), it is **checkmate during setup**. [AB: `@Qe1+#` against a king on e6 ended the game instantly -- the blocking squares e2–e5 all lie outside Black's zone; a follow-up placement attempt was rejected; BLOG: "my opponent setup his king at the third line, then I just setup a rook that is checking his king: Computer: Checkmate, white win."]
* Corollary: while a check *can* be blocked inside your zone, you are forced to spend a placement doing so. [BLOG "you must setup a piece that will blocking the check"]
* **A king may not be placed onto an attacked square; every other piece may.** [AB, 2026-08-05, three placements on the analysis board: with a white queen on e1 and the e-file open, dragging the black king to **e8 was refused** (bank unchanged, still Black to move); the *same* king to the unattacked **d8 was accepted** (`@Kd8`); and a black **rook to the attacked e7 was accepted** (`@Re7`, bank 39→34). So the restriction is king-specific, not a ban on attacked squares.]
* Consequence, and the reason the above matters: an opponent who holds the king back can be **locked out entirely**. Cover every empty square in their zone and they can never legally place a king, which ends setup without one and loses outright -- no checkmate required. Their own pieces block rays and occupy squares, so this works against sparse armies and fails against dense ones.
* Ending setup without a king is an immediate loss, so the king is not optional. [ENG22 `"failed to set up his king"`]
* Consequence for the engine: the drafting layer is not purely combinatorial -- placement legality depends on check state, and the placement tree contains forced lines and mates. `rules.py` implements in-setup check/mate logic.
* ASSUMPTION: an unanswerable setup check is *not* mate when the checker finishes on that very placement, because the setup ends there and the checked side has the first move of the chess game (see the turn rule above) and walks the king out. `rules.py` encodes this. Unobserved either way: the live mate [AB: `@Qe1+#`] was against a side whose opponent still had points to spend, so it does not settle the case. Earlier revisions made this exception White-only, on the belief that White always moves first after handoff; [LIVE] disproved that premise, and the condition is now the reason itself and applies to both colours.

## Post-setup rules

* Once both armies are placed, play is ordinary chess. [NEWS "they play the game normally"; DLG frames setup as "before the game"; AB: normal single steps, double steps and captures all behaved standardly]
* **No castling**, ever. [AB: Ke1→g1 with an untouched Rh1 and empty f1/g1 was rejected; ENG: the castling tables are computed once from the *start* position -- which in Setup Chess is empty -- and are never rebuilt after placements, so no side ever acquires rights] (The 2022 build [ENG22] still contained a 960-style "king onto rook" path; the live build and the live board both say no. Emit `-` in the FEN.)
* **En passant: enabled**, standard rules. [HDR `RuleVariants "EnPassant ..."`; ENG default rule set sets `enPassant=!0` and Setup Chess carries no "No En Passant" modifier]
* **Pawn double-step: only from rank 2** (Black: rank 7) -- a pawn placed on rank 3 gets **no** double-step. [ENG: the jump is tied to the configured pawn home rank, tooltip "Rank from which pawns can jump 2 squares (and from the rank right behind it as well)", with `pawnBaseRank` defaulting to 2; AB: d2–d4 and d7–d5 accepted, c3–c5 rejected; HDR `pawnBaseRank:5` = rank 2 in the server's +3-offset internal coordinates]
* **Promotion on the last rank** (8th for White), standard piece choice. [ENG promotion rank resolves to the far rank for 8×8 when the variant does not override it, and Setup Chess uses "Default"; NEWS]
* Win by checkmate (server flag `Play4Mate`). [HDR]
* ASSUMPTION: draw rules (stalemate, threefold, 50-move) are standard. Untested; irrelevant to the drafting layer, since Stockfish handles the played-out game.

## Time control

* Placements are **in-game turns played on the game's own clock** -- there is no separate setup time control. [NEWS] "in this variant, the action starts before the very first move"; the variant lobby offers ordinary chess time controls (e.g. 5|2) and nothing else.
* The **1:00** both clocks showed during my analysis-board tests [AB] is that board's own default, not a setup-specific rule -- treat it as unconfirmed for live play.
* ASSUMPTION (engineering-relevant): our placement decisions must fit inside the *game* clock alongside the moves that follow. Planning number: **≤2 s per placement**, ~20 s for a whole army at 5|2. The policy must therefore be precomputed offline; the online path may do at most a cheap lookup plus a shallow best-response check.
* One live game would settle both the clock question and the finish-order assumption above; neither blocks the build.

## Internal format notes (for `play.py`, non-normative)

* The variants server is the 4-player-chess engine; games are PGN4 with a `StartFen4` header; our 8×8 board is embedded in its 14×14 coordinates at offset +3 (a1 = internal d4; placement `@Qd1` = `@rQ-g4`). [HDR/AB]
* Placement notation in the move list is `@<Piece><square>`, checks/mates annotated as usual (`@Qe1+#`). [AB]
