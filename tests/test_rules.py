import pytest
from xiangqi.board import Board
from xiangqi.rules import (
    IllegalMove, apply_move, game_result, generals_face, in_check, legal_moves,
)


def test_generals_face():
    b = Board.from_fen("4k4/9/9/9/9/9/9/9/9/4K4 w - - 0 1")
    assert generals_face(b)
    b2 = Board.from_fen("4k4/9/9/9/9/4P4/9/9/9/4K4 w - - 0 1")
    assert not generals_face(b2)     # 中间有兵隔开


def test_pinned_by_flying_general():
    # 红兵在 e 线挡着帅将照面；兵横走离开 e 线即白脸将 → 非法
    b = Board.from_fen("4k4/9/9/9/4P4/9/9/9/9/4K4 w - - 0 1")
    moves = legal_moves(b, "r")
    assert "e5d5" not in moves and "e5f5" not in moves
    assert "e5e6" in moves           # 前进仍挡在 e 线，合法
    assert "e0d0" in moves           # 帅横走离开 e 线，合法


def test_in_check_by_rook():
    b = Board.from_fen("4k4/9/9/9/9/9/9/9/4r4/4K4 b - - 0 1")
    assert in_check(b, "r")          # 黑车沉底将军
    assert not in_check(b, "b")


def test_must_resolve_check():
    # 黑车 e1 将军：红帅只能横走；e0e1 吃车会导致白脸将，非法
    b = Board.from_fen("4k4/9/9/9/9/9/9/9/4r4/4K4 w - - 0 1")
    moves = legal_moves(b, "r")
    assert set(moves) == {"e0d0", "e0f0"}
    assert "e0e1" not in moves


def test_checkmate():
    # 车 a9 锁第 9 排，车 e7 锁 e 路并在将军：黑将无处可逃
    b = Board.from_fen("R3k4/9/4R4/9/9/9/9/9/9/3K5 b - - 0 1")
    assert in_check(b, "b")
    assert legal_moves(b, "b") == []
    assert game_result(b) == "red_win"


def test_stalemate_is_loss():
    # 困毙：黑将未被将军，但 d9/f9 被第 8 排双车封住、e8 同样被锁，无路可走
    b = Board.from_fen("4k4/3R1R3/9/9/9/9/9/9/9/3K5 b - - 0 1")
    assert not in_check(b, "b")
    assert legal_moves(b, "b") == []
    assert game_result(b) == "red_win"


def test_apply_move_updates_clocks_and_turn():
    b = Board.initial()
    nb = apply_move(b, "h2e2")           # 炮二平五，未吃子
    assert nb.turn == "b" and nb.halfmove == 1 and nb.fullmove == 1
    nb2 = apply_move(nb, "b9c7")         # 黑马 2 进 3
    assert nb2.turn == "r" and nb2.halfmove == 2 and nb2.fullmove == 2


def test_capture_resets_halfmove():
    b = Board.from_fen("k8/9/9/9/p8/9/9/9/9/R3K4 w - - 5 1")
    nb = apply_move(b, "a0a5")           # 红车直上吃黑卒
    assert nb.halfmove == 0


def test_illegal_move_raises():
    b = Board.initial()
    with pytest.raises(IllegalMove):
        apply_move(b, "h2e3")            # 炮斜走非法
    with pytest.raises(IllegalMove):
        apply_move(b, "b9c7")            # 轮红走，黑子不能动


def test_draw_by_halfmove_clock():
    b = Board.from_fen("4k4/9/9/9/9/9/9/9/9/4K4 w - - 120 61")
    assert game_result(b) == "draw"
