"""attack.py 攻击图引擎测试。"""

from xiangqi.attack import (attack_targets, hanging_pieces, pinned_pieces,
                            self_check, square_report)
from xiangqi.board import Board, iccs_to_rc, rc_to_iccs
from xiangqi.rules import apply_move


def _targets(board, sq):
    """attack_targets 的便捷形式：{目标格: via_kind}。"""
    r, c = iccs_to_rc(sq)
    return {rc_to_iccs(*a["square"]): a["via_kind"]
            for a in attack_targets(board, r, c)}


def test_cannon_needs_screen():
    """炮：无炮架不攻击；恰好一个炮架才能打。"""
    b = Board.from_fen("4k4/9/9/4p4/9/9/9/4C4/9/4K4 w - - 0 1")
    # 炮 e2，卒 e6：中间无子，e6 只是"炮架候选"而非目标
    assert "e6" not in _targets(b, "e2")
    b2 = Board.from_fen("4k4/9/9/4p4/9/4P4/9/4C4/9/4K4 w - - 0 1")
    # 兵 e4 当炮架 → 炮 e2 打 卒 e6
    assert _targets(b2, "e2").get("e6") == "炮架"


def test_rook_stops_at_first_piece():
    b = Board.from_fen("r3k4/9/9/9/9/9/9/9/9/R3K4 w - - 0 1")
    t = _targets(b, "a0")
    assert t.get("a9") is None and "a9" in t     # 直线打到 車a9
    assert "a8" not in t                          # 不穿透；e0 是守护己帅


def test_horse_leg_blocks():
    b = Board.from_fen("4k4/9/9/9/9/9/9/9/9/RN2K4 w - - 0 1")
    assert set(_targets(b, "b0")) >= {"a2", "c2", "d1"}   # 马 b0 自由
    b2 = Board.from_fen("4k4/9/9/9/9/9/9/9/1P7/RN2K4 w - - 0 1")
    t2 = _targets(b2, "b0")                      # 兵 b1 蹩住往 a2/c2 的马腿
    assert "a2" not in t2 and "c2" not in t2 and "d1" in t2


def test_flying_general():
    b = Board.from_fen("4k4/9/9/9/9/9/9/9/9/4K4 w - - 0 1")
    assert _targets(b, "e0").get("e9") == "白脸将"


def test_square_report_initial_cannon_screen():
    """初始局面：炮 b2 经炮架 砲b7 攻击 馬b9。"""
    rep = square_report(Board.initial(), "b9")
    assert "馬b9" in rep and "炮b2" in rep and "炮架 b7" in rep


def test_square_report_defense():
    """a5 炮被 車a9 攻击、被 车a0 守护。"""
    b = Board.from_fen("r3k4/9/9/9/C8/9/9/9/9/R3K4 w - - 0 1")
    rep = square_report(b, "a5")
    assert "車a9" in rep and "车a0" in rep


def test_hanging_initial_is_empty():
    assert hanging_pieces(Board.initial(), "r") == {}
    assert hanging_pieces(Board.initial(), "b") == {}


def test_self_check_detects_opened_line():
    """炮 a5 移开后，车 a0 暴露在 車a9 枪口下：新挂子 + 新攻击线。"""
    b = Board.from_fen("r2k5/9/9/9/C8/9/9/9/9/R3K4 w - - 0 1")
    nb = apply_move(b, "a5b5")
    rep = self_check(b, nb, "r", "a5", "b5")
    assert "车a0" in rep and "⚠️" in rep


def test_self_check_quiet_move():
    """炮二平五不给红方制造新漏洞。"""
    b = Board.initial()
    nb = apply_move(b, "h2e2")
    rep = self_check(b, nb, "r", "h2", "e2")
    assert "没有新送入口的子" in rep


def test_pin_detection():
    """炮 a5 挡在 車a9 与 车a0 之间 = 被牵制（走后车暴露）。"""
    b = Board.from_fen("r3k4/9/9/9/C8/9/9/9/9/R3K4 w - - 0 1")
    pins = pinned_pieces(b, "r")
    assert "a5" in pins and "车a0" in pins["a5"]
