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


def test_explain_illegal_none_for_legal():
    from xiangqi.rules import explain_illegal
    b = Board.initial()
    assert explain_illegal(b, "r", "h2e2") is None


def test_explain_illegal_format():
    from xiangqi.rules import explain_illegal
    b = Board.initial()
    assert "ICCS" in explain_illegal(b, "r", "z9z9")
    assert "ICCS" in explain_illegal(b, "r", "h2e")


def test_explain_illegal_empty_source():
    from xiangqi.rules import explain_illegal
    b = Board.initial()
    assert explain_illegal(b, "r", "e4e5") == "起点格没有棋子"


def test_explain_illegal_opponent_piece():
    from xiangqi.rules import explain_illegal
    b = Board.initial()
    assert explain_illegal(b, "r", "b9c7") == "不能走对方的棋子"


def test_explain_illegal_own_target():
    from xiangqi.rules import explain_illegal
    b = Board.initial()
    assert explain_illegal(b, "r", "a0b0") == "目标格是己方棋子"


def test_explain_illegal_horse_leg():
    from xiangqi.rules import explain_illegal
    # b1 放一个红兵：红马 b0 两个日字点都被蹩住
    b = Board.from_fen("rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/1P7/RNBAKABNR w - - 0 1")
    assert explain_illegal(b, "r", "b0a2") == "蹩马腿或走位不符"
    assert explain_illegal(b, "r", "b0c2") == "蹩马腿或走位不符"


def test_explain_illegal_general_range():
    from xiangqi.rules import explain_illegal
    b = Board.initial()
    assert explain_illegal(b, "r", "e0e2") == "走位不合该子规则"


def test_explain_illegal_self_check_pin():
    from xiangqi.rules import explain_illegal
    # 红车 d2 沿 d 路钉住黑马 d7（背后是黑将 d9），马走开即送将
    b = Board.from_fen("3k5/9/3n5/9/9/9/9/3R5/9/9 b - - 0 1")
    assert explain_illegal(b, "b", "d7f8") == "走后己方被将军（送将/该子正被钉住）"


def test_explain_illegal_flying_general():
    from xiangqi.rules import explain_illegal
    # 红炮 e2 是帅将之间唯一的遮挡，炮无论直走横走离开都会白脸将
    b = Board.from_fen("4k4/9/9/9/9/9/9/4C4/9/4K4 w - - 0 1")
    assert explain_illegal(b, "r", "e2e3") is None   # 炮直走仍挡在 e 路，合法
    assert explain_illegal(b, "r", "e2d2") == "走后两帅将照面（白脸将）"
