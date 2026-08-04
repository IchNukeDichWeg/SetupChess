"""ctypes binding to the C core. Python stays the harness, C does the work.

The core exists because a Setup Chess position can hold up to 48 pieces and
an ordinary engine cannot: Stockfish segfaults on a 42-piece mirror once the
search runs (rules.ENGINE_MAX_PIECES). Bitboards have no piece-count ceiling,
so this generator takes the positions the drafting layer actually produces.

Build with `make`; the library lands in lib/ and is loaded from there.
"""

import ctypes
import os

import chess

_LIB = None
_SO = {"darwin": "libsetupcore.dylib"}.get(
    __import__("sys").platform, "libsetupcore.so")

# mirrors the Constants.h enum, which follows python-chess minus the 1-offset
PIECE_TO_C = {chess.PAWN: 0, chess.KNIGHT: 1, chess.BISHOP: 2,
              chess.ROOK: 3, chess.QUEEN: 4, chess.KING: 5}
C_TO_PIECE = {v: k for k, v in PIECE_TO_C.items()}
NO_SQ = 64
MAX_MOVES = 256


class Position(ctypes.Structure):
    _fields_ = [("pieces", ctypes.c_uint64 * 6 * 2),
                ("occ", ctypes.c_uint64 * 2),
                ("all", ctypes.c_uint64),
                ("side", ctypes.c_int),
                ("ep", ctypes.c_int),
                ("halfmove", ctypes.c_int)]


def lib():
    """Load the core, with a build hint rather than a bare OSError."""
    global _LIB
    if _LIB is not None:
        return _LIB
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib", _SO)
    if not os.path.exists(path):
        raise OSError("C core not built: %s missing -- run `make`" % path)
    l = ctypes.CDLL(path)
    l.pos_clear.argtypes = [ctypes.POINTER(Position)]
    l.pos_set.argtypes = [ctypes.POINTER(Position)] + [ctypes.c_int] * 3
    l.pos_finish.argtypes = [ctypes.POINTER(Position), ctypes.c_int,
                             ctypes.c_int]
    l.legal_moves.argtypes = [ctypes.POINTER(Position),
                              ctypes.POINTER(ctypes.c_uint32)]
    l.legal_moves.restype = ctypes.c_int
    l.perft_entry.argtypes = [ctypes.POINTER(Position), ctypes.c_int]
    l.perft_entry.restype = ctypes.c_uint64
    l.in_check.argtypes = [ctypes.POINTER(Position), ctypes.c_int]
    l.in_check.restype = ctypes.c_int
    l.position_size.restype = ctypes.c_int
    if l.position_size() != ctypes.sizeof(Position):
        raise RuntimeError(
            "Position layout mismatch: C says %d bytes, ctypes says %d"
            % (l.position_size(), ctypes.sizeof(Position)))
    _LIB = l
    return l


def from_board(board):
    """python-chess Board -> C Position. Castling rights are ignored: the
    variant has none (docs/RULES.md) and the core does not generate them."""
    l = lib()
    p = Position()
    l.pos_clear(ctypes.byref(p))
    for sq, piece in board.piece_map().items():
        l.pos_set(ctypes.byref(p), 0 if piece.color == chess.WHITE else 1,
                  PIECE_TO_C[piece.piece_type], sq)
    ep = board.ep_square if board.ep_square is not None else NO_SQ
    l.pos_finish(ctypes.byref(p), 0 if board.turn == chess.WHITE else 1, ep)
    return p


def _decode(mv):
    promo = (mv >> 12) & 0x7
    return chess.Move(mv & 0x3F, (mv >> 6) & 0x3F,
                      promotion=C_TO_PIECE[promo] if promo else None)


def legal_moves(board):
    """Legal moves from the C core, as python-chess Move objects."""
    l = lib()
    p = from_board(board)
    buf = (ctypes.c_uint32 * MAX_MOVES)()
    n = l.legal_moves(ctypes.byref(p), buf)
    return [_decode(buf[i]) for i in range(n)]


def perft(board, depth):
    l = lib()
    p = from_board(board)
    return int(l.perft_entry(ctypes.byref(p), depth))


def in_check(board, color=None):
    l = lib()
    p = from_board(board)
    c = board.turn if color is None else color
    return bool(l.in_check(ctypes.byref(p), 0 if c == chess.WHITE else 1))
