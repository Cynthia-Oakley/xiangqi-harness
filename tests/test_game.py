import time

import pytest
from xiangqi.board import Board
from xqserver.game import AuthError, Game, GameOver, RuleError, TurnError


def make_game(**kw):
    g = Game("g1", "Kimi", "Claude", **kw)
    g.join("r")
    g.join("b")
    return g


def test_join_starts_the_clock():
    """双方 join 后才开始计时；只有一方入座不开赛。"""
    g = Game("g1", "Kimi", "Claude")
    assert g.started is False
    assert g.join("r") is False              # 只有红方，未开赛
    assert g.started is False
    assert g.join("b") is True               # 黑方到齐，本调用触发开赛
    assert g.started is True
    ts = g.turn_started
    assert ts is not None
    assert g.join("r") is False              # 重复入座不重置时钟
    assert g.turn_started == ts


def test_no_timeout_before_start():
    """开赛前永不超时：即使超过步时/局时，也不判负。"""
    import time
    g = Game("g1", "Kimi", "Claude", step_seconds=0.05, base_seconds=0.05)
    time.sleep(0.1)
    assert g.settle_timeout() is False
    assert g.result is None
    g.join("r")
    g.join("b")
    time.sleep(0.1)
    assert g.settle_timeout() is True        # 开赛后才结算超时
    assert g.result == "black_win" and g.result_reason == "timeout"


def test_no_move_before_start():
    g = Game("g1", "Kimi", "Claude")
    with pytest.raises(RuleError):
        g.propose_move("r", "h2e2")
    st = g.state_for("r")
    assert st["started"] is False and st["your_turn"] is False
    assert st["joined"] == {"r": False, "b": False}
    g.join("r")
    st = g.state_for("r")
    assert st["started"] is False and st["your_turn"] is False   # 仍等黑方
    g.join("b")
    st = g.state_for("r")
    assert st["started"] is True and st["your_turn"] is True


def test_clocks_frozen_before_start():
    import time
    g = Game("g1", "Kimi", "Claude", base_seconds=900, step_seconds=180)
    time.sleep(0.05)
    c = g.clocks()
    assert c["remaining"] == {"r": 900.0, "b": 900.0}   # 未开赛不递减
    assert c["step_remaining"] is None
    g.join("r")
    g.join("b")
    c = g.clocks()
    assert c["step_remaining"] is not None


def test_tokens_and_auth():
    g = make_game()
    assert g.tokens["r"] != g.tokens["b"]
    g.check_token("r", g.tokens["r"])
    with pytest.raises(AuthError):
        g.check_token("r", g.tokens["b"])
    with pytest.raises(AuthError):
        g.check_token("r", None)


def test_two_phase_commit():
    g = make_game()
    pv = g.propose_move("r", "h2e2", note="架中炮")
    assert pv["chinese"] == "炮二平五"
    assert pv["captured"] is None
    assert pv["gives_check"] is False
    assert g.board.turn == "r"           # 预览不改变棋局
    entry = g.commit("r", pv["pending_id"])
    assert entry["iccs"] == "h2e2" and entry["chinese"] == "炮二平五"
    assert g.board.turn == "b"           # commit 后轮到黑方
    assert g.notes[0]["text"] == "架中炮" and g.notes[0]["color"] == "r"


def test_cancel_then_repropose():
    g = make_game()
    pv = g.propose_move("r", "h2e2")
    g.cancel("r", pv["pending_id"])
    with pytest.raises(RuleError):
        g.commit("r", pv["pending_id"])  # 已作废
    pv2 = g.propose_move("r", "b0c2")
    g.commit("r", pv2["pending_id"])
    assert g.board.turn == "b"


def test_turn_enforcement():
    g = make_game()
    with pytest.raises(TurnError):
        g.propose_move("b", "b9c7")      # 红方先走
    pv = g.propose_move("r", "h2e2")
    with pytest.raises(TurnError):
        g.commit("b", pv["pending_id"])  # 黑方不能替红方 commit
    with pytest.raises(TurnError):
        g.cancel("b", pv["pending_id"])


def test_illegal_and_malformed():
    g = make_game()
    with pytest.raises(RuleError):
        g.propose_move("r", "h2e3")      # 炮斜走
    with pytest.raises(RuleError):
        g.propose_move("r", "z9z9")      # 畸形 ICCS
    assert g.pending is None             # 失败不留下 pending


def test_no_commit_without_pending():
    g = make_game()
    with pytest.raises(RuleError):
        g.commit("r", "nonexistent")


def test_step_timeout_loss():
    g = make_game(step_seconds=0, step_timeout="lose")  # 正式规则：步时 0，下一步操作即超时
    with pytest.raises(GameOver):
        g.propose_move("r", "h2e2")
    assert g.result == "black_win" and g.result_reason == "timeout"


def test_pass_mode_step_timeout():
    """宽松模式（默认）：步时超时记停着一手，轮转到对方，耗时照扣不加秒。"""
    g = make_game(step_seconds=0.05)
    assert g.step_timeout == "pass"
    time.sleep(0.1)
    assert g.settle_timeout() is True
    assert g.result is None
    assert g.board.turn == "b"
    entry = g.history[-1]
    assert entry["iccs"] == "pass" and entry["chinese"] == "停着"
    assert entry["color"] == "r" and entry["ply"] == 1
    assert g.remaining["r"] < 900                      # 耗时照扣
    evs = g.pop_events()
    assert evs[0]["event"] == "pass"
    assert g.pop_events() == []                        # 事件取出即清空
    pv = g.propose_move("b", "b9c7")                   # 黑方正常走棋
    g.commit("b", pv["pending_id"])
    assert g.board.turn == "r"


def test_pass_mode_consecutive_passes():
    g = make_game(step_seconds=0.05)
    time.sleep(0.1); g.settle_timeout()                # 红停着
    time.sleep(0.1); g.settle_timeout()                # 黑停着
    assert [h["color"] for h in g.history] == ["r", "b"]
    assert g.board.turn == "r" and g.result is None


def test_pass_mode_check_still_loses():
    """被将军时不能停着：超时仍判负。"""
    g = make_game(step_seconds=0.05)
    g.board = Board.from_fen("4k4/9/9/9/9/9/9/9/4r4/4K4 w - - 0 1")  # 黑车将着红帅
    time.sleep(0.1)
    g.settle_timeout()
    assert g.result == "black_win" and g.result_reason == "timeout"


def test_pass_mode_base_time_still_loses():
    """局时耗尽不受宽松模式影响，仍判负。"""
    g = make_game(base_seconds=0.1, step_seconds=0.05, increment=0)
    time.sleep(0.2)
    g.settle_timeout()
    assert g.result == "black_win" and g.result_reason == "timeout"


def test_pass_invalidates_pending_preview():
    """超时停着后，该方此前未确认的预览作废。"""
    g = make_game(step_seconds=0.2)
    pv = g.propose_move("r", "h2e2")
    time.sleep(0.3)
    g.settle_timeout()                                 # 红停着，轮到黑
    with pytest.raises(RuleError):
        g.commit("r", pv["pending_id"])


def test_base_time_exhaustion():
    g = make_game(base_seconds=-1)       # 局时已空
    with pytest.raises(GameOver):
        g.propose_move("r", "h2e2")
    assert g.result == "black_win"


def test_increment_added():
    g = make_game(increment=10)
    before = g.remaining["r"]
    pv = g.propose_move("r", "h2e2")
    g.commit("r", pv["pending_id"])
    assert g.remaining["r"] > before - 1  # 扣掉耗时但加回 10s


def test_resign_and_draw_agreement():
    g = make_game()
    g.resign("r")
    assert g.result == "black_win" and g.result_reason == "resign"
    with pytest.raises(GameOver):
        g.propose_move("b", "b9c7")

    g2 = make_game()
    g2.offer_draw("r")
    g2.accept_draw("b")
    assert g2.result == "draw" and g2.result_reason == "draw_agreement"

    g3 = make_game()
    with pytest.raises(RuleError):
        g3.accept_draw("b")              # 没有提议可接受


def test_halfmove_draw():
    g = make_game(draw_halfmoves=2)
    pv = g.propose_move("r", "h2e2"); g.commit("r", pv["pending_id"])
    pv = g.propose_move("b", "b9c7"); g.commit("b", pv["pending_id"])
    assert g.result == "draw" and g.result_reason == "halfmove_limit"


def test_state_hides_notes():
    g = make_game()
    g.add_note("r", "秘密计划")
    st = g.state_for("b")
    assert "notes" not in st and "秘密计划" not in str(st)
    st_r = g.state_for("r")
    assert "notes" not in st_r           # 自己的 note 也不经 state 回传
