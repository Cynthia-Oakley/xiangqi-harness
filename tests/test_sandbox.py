"""多步推演沙盒：probe/sim、配额、分支失效、只给事实。"""

import pytest
from httpx import ASGITransport, AsyncClient

from xiangqi.board import Board
from xqserver.app import create_app
from xqserver.game import Game, RuleError

MATE_FEN = "3k5/R2R5/9/9/9/9/9/9/9/K1R1R4 b - - 0 1"  # 黑方被将死


def make_game(**kw):
    kw.setdefault("sandbox_budget", 30)
    g = Game("g1", "Kimi", "Claude", **kw)
    g.join("r")
    g.join("b")
    return g


# ---- Game 层 ----

def test_sandbox_disabled_by_default():
    g = Game("g1", "A", "B")     # 不传 sandbox_budget
    g.join("r")
    g.join("b")
    with pytest.raises(RuleError, match="未开启"):
        g.probe("r", ["h2e2"])
    with pytest.raises(RuleError, match="未开启"):
        g.sim_start("r")
    assert "sandbox" not in g.state_for("r")


def test_sandbox_requires_start():
    g = Game("g1", "A", "B", sandbox_budget=30)
    with pytest.raises(RuleError, match="尚未开始"):
        g.probe("r", ["h2e2"])


def test_probe_line_facts_and_budget():
    g = make_game()
    v = g.probe("r", ["h2e2", "h9g7"])
    assert [s["chinese"] for s in v["steps"]] == ["炮二平五", "马8进7"]
    assert all(s["legal"] for s in v["steps"])
    assert v["turn"] == "r"                       # 两步后又轮红
    assert v["sandbox"]["budget_left"] == 28
    assert g.board.to_fen() == Board.initial().to_fen()   # 正式局面不变
    st = g.state_for("r")
    assert st["sandbox"]["budget_left"] == 28 and st["sandbox"]["budget_total"] == 30


def test_probe_illegal_midline_stops_and_refunds():
    g = make_game()
    v = g.probe("r", ["h2e2", "h9h0", "b9c7"])    # 第 2 步黑车 h9h0 非法（h0 有红车且越子）
    assert v["steps"][0]["legal"] is True
    assert v["steps"][1]["legal"] is False and v["steps"][1]["reason"]
    assert len(v["steps"]) == 2                   # 中止，不走第 3 步
    assert v["sandbox"]["budget_left"] == 28      # 未执行的第 3 步退回


def test_probe_rejects_over_budget():
    g = make_game(sandbox_budget=2)
    with pytest.raises(RuleError, match="配额不足"):
        g.probe("r", ["h2e2", "h9g7", "h0g2"])
    assert g.sim_remaining["r"] == 2              # 未扣减


def test_probe_terminal_fact_on_mated_board():
    g = make_game()
    g.board = Board.from_fen(MATE_FEN)            # 黑方已被将死的局面
    v = g.probe("r", ["d9e9"])                    # 黑方已死，任何假设走子都非法
    assert v["steps"][0]["legal"] is False


def test_terminal_fact_in_sim_view():
    g = make_game()
    g.board = Board.from_fen(MATE_FEN)
    v = g.sim_start("r")
    assert v["terminal"] == "红方胜（将死）"


def test_sim_lifecycle_and_limit():
    g = make_game()
    a = g.sim_start("r")["sim_id"]
    b = g.sim_start("r")["sim_id"]
    with pytest.raises(RuleError, match="最多同时"):
        g.sim_start("r")
    v = g.sim_push("r", a, "h2e2")
    assert v["depth"] == 1 and v["last"]["chinese"] == "炮二平五"
    assert v["sandbox"]["budget_left"] == 29
    v = g.sim_push("r", a, "h9g7")
    assert v["depth"] == 2 and v["turn"] == "r"
    v = g.sim_pop("r", a)
    assert v["depth"] == 1
    v = g.sim_board("r", a)
    assert v["depth"] == 1 and v["ascii"]
    g.sim_end("r", a)
    g.sim_end("r", b)
    c = g.sim_start("r")["sim_id"]                # 关掉后能再开
    assert c in g.sims["r"]


def test_sim_pop_at_root_rejected():
    g = make_game()
    sid = g.sim_start("r")["sim_id"]
    with pytest.raises(RuleError, match="分支起点"):
        g.sim_pop("r", sid)


def test_sim_push_illegal_charges_but_keeps_branch():
    g = make_game()
    sid = g.sim_start("r")["sim_id"]
    with pytest.raises(RuleError, match="非法走法"):
        g.sim_push("r", sid, "h9g7")              # 轮到红方假设，走黑子的马非法
    assert g.sim_remaining["r"] == 29             # 配额照计
    assert len(g.sims["r"][sid]) == 1             # 分支未变


def test_real_move_invalidates_sims_and_refills_budget():
    g = make_game()
    sid = g.sim_start("r")["sim_id"]
    g.sim_push("r", sid, "h2e2")
    g.probe("r", ["h2e2", "h9g7"])
    assert g.sim_remaining["r"] < 30
    g.play("r", "h2e2")                           # 正式落子
    assert g.sim_remaining["r"] == 30             # 配额恢复
    assert g.sims["r"] == {}                      # 分支失效
    with pytest.raises(RuleError, match="不存在"):
        g.sim_push("r", sid, "h9g7")


def test_probe_neutral_about_eval():
    """只给事实：返回里不得含评分/推荐字段。"""
    g = make_game()
    v = g.probe("r", ["h2e2"])
    banned = {"score", "eval", "best", "recommendation"}
    assert not (banned & set(v))
    assert not (banned & set(v["steps"][0]))


# ---- API 层 ----

@pytest.fixture
async def client(tmp_path):
    app = create_app(data_dir=str(tmp_path))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def _started_game(client, **kw):
    r = await client.post("/api/games", json={
        "red_name": "Kimi", "black_name": "Claude",
        "sandbox_budget": 30, **kw})
    g = r.json()
    for side in ("red", "black"):
        await client.post(f"/api/games/{g['game_id']}/join",
                          headers={"X-Seat-Token": g[f"{side}_token"]})
    return g


async def test_probe_api_and_auth(client):
    g = await _started_game(client)
    gid = g["game_id"]
    r = await client.post(f"/api/games/{gid}/probe",
                          json={"moves": ["h2e2", "h9g7"]},
                          headers={"X-Seat-Token": g["red_token"]})
    assert r.status_code == 200
    d = r.json()
    assert [s["chinese"] for s in d["steps"]] == ["炮二平五", "马8进7"]
    assert d["sandbox"]["budget_left"] == 28
    r = await client.post(f"/api/games/{gid}/probe", json={"moves": ["h2e2"]},
                          headers={"X-Seat-Token": "wrong"})
    assert r.status_code == 403


async def test_probe_api_disabled_game(client):
    r = await client.post("/api/games", json={"red_name": "A", "black_name": "B"})
    g = r.json()
    for side in ("red", "black"):
        await client.post(f"/api/games/{g['game_id']}/join",
                          headers={"X-Seat-Token": g[f"{side}_token"]})
    r = await client.post(f"/api/games/{g['game_id']}/probe",
                          json={"moves": ["h2e2"]},
                          headers={"X-Seat-Token": g["red_token"]})
    assert r.status_code == 400 and "未开启" in r.json()["error"]


async def test_sim_api_flow(client):
    g = await _started_game(client)
    gid, tok = g["game_id"], g["black_token"]
    r = await client.post(f"/api/games/{gid}/sim/start",
                          headers={"X-Seat-Token": tok})
    assert r.status_code == 200
    sid = r.json()["sim_id"]
    assert sid.startswith("s")                    # id 不会以 - 开头（CLI 位置参数安全）
    r = await client.post(f"/api/games/{gid}/sim/{sid}/push",
                          json={"move": "h2e2"}, headers={"X-Seat-Token": tok})
    assert r.status_code == 200 and r.json()["depth"] == 1
    r = await client.get(f"/api/games/{gid}/sim/{sid}",
                         headers={"X-Seat-Token": tok})
    assert r.status_code == 200 and r.json()["ascii"]
    r = await client.post(f"/api/games/{gid}/sim/{sid}/pop",
                          headers={"X-Seat-Token": tok})
    assert r.json()["depth"] == 0
    r = await client.post(f"/api/games/{gid}/sim/{sid}/end",
                          headers={"X-Seat-Token": tok})
    assert r.json()["ok"] is True
    # 对方看不到/操作不了我的分支
    r = await client.post(f"/api/games/{gid}/sim/{sid}/push",
                          json={"move": "h2e2"},
                          headers={"X-Seat-Token": g["red_token"]})
    assert r.status_code == 400


async def test_sandbox_never_persisted(client, tmp_path):
    """沙盒不落盘：对局记录文件里没有任何 probe/sim 痕迹（观众也看不到）。"""
    g = await _started_game(client)
    gid = g["game_id"]
    await client.post(f"/api/games/{gid}/probe",
                      json={"moves": ["h2e2", "h9g7"]},
                      headers={"X-Seat-Token": g["red_token"]})
    await client.post(f"/api/games/{gid}/sim/start",
                      headers={"X-Seat-Token": g["red_token"]})
    rec = tmp_path / "games" / f"{gid}.jsonl"
    if rec.exists():
        for line in rec.read_text(encoding="utf-8").splitlines():
            assert "sim" not in line and "probe" not in line
