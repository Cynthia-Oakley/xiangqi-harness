from xiangqi.board import Board, iccs_to_rc
from xiangqi.moves import pseudo_destinations


def dests(board, iccs):
    r, c = iccs_to_rc(iccs)
    return set(pseudo_destinations(board, r, c))


def test_rook_blocked_by_own_pawn():
    b = Board.initial()
    d = dests(b, "a0")           # 红车 (9,0)
    assert (8, 0) in d and (7, 0) in d
    assert (6, 0) not in d       # 己方兵 a3 挡路
    assert (9, 1) not in d       # b0 是己方马


def test_rook_capture():
    b = Board.from_fen("k8/9/9/9/9/9/9/p8/9/R3K4 w - - 0 1")
    d = dests(b, "a0")           # 红车 (9,0)，黑卒在 (7,0)
    assert (8, 0) in d
    assert (7, 0) in d           # 可吃卒
    assert (6, 0) not in d       # 不能越过


def test_horse_initial():
    b = Board.initial()
    d = dests(b, "b0")           # 红马 (9,1)
    assert (7, 0) in d and (7, 2) in d
    assert (8, 3) not in d       # 马腿 (9,2) 被己方相占住


def test_horse_leg_blocked():
    b = Board.from_fen("k8/9/9/9/9/9/9/9/1p6/1N2K4 w - - 0 1")
    d = dests(b, "b0")           # 红马 (9,1)，马腿 (8,1) 被黑卒蹩住
    assert (7, 0) not in d and (7, 2) not in d
    assert (8, 3) in d           # 侧向马腿 (9,2) 空，不受影响


def test_cannon_screen():
    b = Board.from_fen("k8/9/9/9/4p4/9/9/4p4/9/3KC4 w - - 0 1")
    d = dests(b, "e0")           # 红炮 (9,4)，(7,4) 黑卒是炮架，(4,4) 黑卒是目标
    assert (8, 4) in d           # 空格平走
    assert (7, 4) not in d       # 炮架本身不能吃
    assert (4, 4) in d           # 隔一子吃
    assert (0, 4) not in d       # 不能隔两子


def test_advisor_palace():
    b = Board.initial()
    d = dests(b, "d0")           # 红仕 (9,3)
    assert d == {(8, 4)}         # 底排仕只有九宫中心一格


def test_elephant_initial():
    b = Board.initial()
    d = dests(b, "c0")           # 红相 (9,2)，象眼 (8,1)/(8,3) 初始为空
    assert (7, 0) in d and (7, 4) in d


def test_elephant_cannot_cross_river():
    b = Board.from_fen("k8/9/9/9/9/2B6/9/9/9/4K4 w - - 0 1")
    d = dests(b, "c4")           # 红相在河边 (5,2)
    assert (3, 0) not in d and (3, 4) not in d   # 过河禁止
    assert (7, 0) in d and (7, 4) in d


def test_king_palace():
    b = Board.initial()
    d = dests(b, "e0")           # 红帅 (9,4)，横竖被己方子堵住
    assert d == {(8, 4)}


def test_pawn_before_and_after_river():
    b = Board.from_fen("4k4/9/9/9/9/9/9/9/4P4/4K4 w - - 0 1")
    d = dests(b, "e1")           # 红兵 (8,4) 未过河
    assert d == {(7, 4)}         # 只能向前
    b2 = Board.from_fen("4k4/9/9/9/9/4P4/9/9/9/4K4 w - - 0 1")
    d2 = dests(b2, "e5")         # 红兵 (4,4) 已过河
    assert (3, 4) in d2 and (4, 3) in d2 and (4, 5) in d2
    assert (5, 4) not in d2      # 兵不后退


def test_black_pawn_direction():
    b = Board.from_fen("k6p1/9/9/9/9/9/9/9/9/4K4 b - - 0 1")
    d = dests(b, "h9")           # 黑卒 (0,7) 未过河，向前 = row 增大
    assert d == {(1, 7)}
