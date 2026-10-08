import pytest
from xqserver.game import AuthError, Game, GameOver, RuleError, TurnError


def make_game(**kw):
    return Game("g1", "Kimi", "Claude", **kw)


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
    g = make_game(step_seconds=0)        # 步时 0：下一步操作即超时
    with pytest.raises(GameOver):
        g.propose_move("r", "h2e2")
    assert g.result == "black_win" and g.result_reason == "timeout"


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
