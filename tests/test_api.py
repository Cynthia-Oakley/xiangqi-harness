import asyncio
import socket

import pytest
import uvicorn
from httpx import ASGITransport, AsyncClient
from xqserver.app import create_app


@pytest.fixture
async def client(tmp_path):
    app = create_app(data_dir=str(tmp_path))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def new_game(client, **kw):
    body = {"red_name": "Kimi", "black_name": "Claude", **kw}
    r = await client.post("/api/games", json=body)
    assert r.status_code == 200
    return r.json()


async def join(client, g, side):
    r = await client.post(f"/api/games/{g['game_id']}/join",
                          headers={"X-Seat-Token": g[f"{side}_token"]})
    assert r.status_code == 200
    return r.json()


async def join_both(client, g):
    await join(client, g, "red")
    await join(client, g, "black")


async def test_join_flow_and_start_gate(client):
    """双方 join 才开赛：开赛前不能走棋、超时也不判负。"""
    g = await new_game(client, step_seconds=0.2, step_timeout="lose")
    gid = g["game_id"]
    r = await client.post(f"/api/games/{gid}/move", json={"move": "h2e2"},
                          headers={"X-Seat-Token": g["red_token"]})
    assert r.status_code == 400                     # 开赛前禁止走棋
    r = await client.post(f"/api/games/{gid}/join",
                          headers={"X-Seat-Token": "wrong"})
    assert r.status_code == 403
    rj = await join(client, g, "red")
    assert rj["started"] is False and rj["waiting_for"] == "Claude"
    st = (await client.get(f"/api/games/{gid}/state",
                           headers={"X-Seat-Token": g["red_token"]})).json()
    assert st["started"] is False and st["your_turn"] is False
    await asyncio.sleep(0.4)                        # 超过步时，但未开赛
    st = (await client.get(f"/api/games/{gid}/state",
                           headers={"X-Seat-Token": g["black_token"]})).json()
    assert st["result"] is None
    rj = await join(client, g, "black")
    assert rj["started"] is True and rj["waiting_for"] is None
    st = (await client.get(f"/api/games/{gid}/state",
                           headers={"X-Seat-Token": g["red_token"]})).json()
    assert st["started"] is True and st["your_turn"] is True
    await asyncio.sleep(0.4)                        # 开赛后超时结算恢复
    st = (await client.get(f"/api/games/{gid}/state",
                           headers={"X-Seat-Token": g["black_token"]})).json()
    assert st["result"] == "black_win" and st["result_reason"] == "timeout"


async def test_create_and_state(client):
    g = await new_game(client)
    await join_both(client, g)
    r = await client.get(f"/api/games/{g['game_id']}/state",
                         headers={"X-Seat-Token": g["red_token"]})
    st = r.json()
    assert st["your_turn"] is True and st["your_color"] == "r"
    assert "fen" in st and "ascii" in st and "notes" not in st
    assert "legal_moves" in st and "h2e2" in st["legal_moves"]
    r2 = await client.get(f"/api/games/{g['game_id']}/state",
                          headers={"X-Seat-Token": g["black_token"]})
    assert r2.json()["your_turn"] is False
    assert "legal_moves" not in r2.json()


async def test_auth_rejection(client):
    g = await new_game(client)
    r = await client.get(f"/api/games/{g['game_id']}/state",
                         headers={"X-Seat-Token": "wrong"})
    assert r.status_code == 403
    r2 = await client.get(f"/api/games/{g['game_id']}/state")
    assert r2.status_code == 403


async def test_malformed_move_400(client):
    g = await new_game(client)
    await join_both(client, g)
    for bad in ("z9z9", "h2e3", "", "h2h2"):
        r = await client.post(f"/api/games/{g['game_id']}/move",
                              json={"move": bad},
                              headers={"X-Seat-Token": g["red_token"]})
        assert r.status_code == 400, bad
    st = await client.get(f"/api/games/{g['game_id']}/state",
                          headers={"X-Seat-Token": g["red_token"]})
    assert st.json()["ply"] == 0     # 棋局未被改变


async def test_wrong_turn_409(client):
    g = await new_game(client)
    await join_both(client, g)
    r = await client.post(f"/api/games/{g['game_id']}/move",
                          json={"move": "b9c7"},
                          headers={"X-Seat-Token": g["black_token"]})
    assert r.status_code == 409


async def test_full_move_cycle_and_jsonl(client, tmp_path):
    g = await new_game(client, draw_halfmoves=2)
    await join_both(client, g)
    r = await client.post(f"/api/games/{g['game_id']}/move",
                          json={"move": "h2e2", "note": "架中炮"},
                          headers={"X-Seat-Token": g["red_token"]})
    pv = r.json()
    assert pv["chinese"] == "炮二平五"
    r = await client.post(f"/api/games/{g['game_id']}/commit",
                          json={"pending_id": pv["pending_id"]},
                          headers={"X-Seat-Token": g["red_token"]})
    assert r.status_code == 200 and r.json()["result"] is None
    r = await client.post(f"/api/games/{g['game_id']}/move",
                          json={"move": "b9c7"},
                          headers={"X-Seat-Token": g["black_token"]})
    pv = r.json()
    r = await client.post(f"/api/games/{g['game_id']}/commit",
                          json={"pending_id": pv["pending_id"]},
                          headers={"X-Seat-Token": g["black_token"]})
    assert r.json()["result"] == "draw"      # draw_halfmoves=2 触发自然限着
    assert r.json()["result_reason"] == "halfmove_limit"
    log = (tmp_path / "games" / f"{g['game_id']}.jsonl").read_text(encoding="utf-8")
    assert "炮二平五" in log and "架中炮" in log


async def test_spectate_snapshot(tmp_path):
    # ASGITransport 会把整个 ASGI app await 到完成，无法测试永不结束的 SSE 流，
    # 因此本用例在真实 uvicorn 服务器上跑。
    app = create_app(data_dir=str(tmp_path))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                           log_level="error"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    try:
        async with AsyncClient(base_url=f"http://127.0.0.1:{port}") as c:
            r = await c.post("/api/games",
                             json={"red_name": "Kimi", "black_name": "Claude"})
            gid = r.json()["game_id"]
            async with c.stream("GET", f"/api/games/{gid}/spectate") as resp:
                assert resp.status_code == 200
                lines = resp.aiter_lines()
                first = await lines.__anext__()
                second = await lines.__anext__()
        assert "snapshot" in first
        assert '"started"' in second and '"names"' in second
    finally:
        # uvicorn 等待连接任务自然结束，而 SSE 连接任务要等下一次读才感知断连，
        # 优雅退出会卡住；测试中直接取消服务任务。
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def test_notes_not_in_state(client):
    g = await new_game(client)
    await client.post(f"/api/games/{g['game_id']}/note",
                      json={"text": "秘密"},
                      headers={"X-Seat-Token": g["red_token"]})
    st = await client.get(f"/api/games/{g['game_id']}/state",
                          headers={"X-Seat-Token": g["black_token"]})
    assert "秘密" not in st.text


async def test_game_not_found(client):
    r = await client.get("/api/games/nope/state", headers={"X-Seat-Token": "x"})
    assert r.status_code == 404


async def test_pass_mode_via_api(client, tmp_path):
    """默认宽松模式：步时超时记停着并轮转换边，落盘与状态一致。"""
    g = await new_game(client, step_seconds=0.2, increment=0)
    await join_both(client, g)
    gid = g["game_id"]
    await asyncio.sleep(0.4)
    st = (await client.get(f"/api/games/{gid}/state",
                           headers={"X-Seat-Token": g["red_token"]})).json()
    assert st["result"] is None and st["turn"] == "b" and st["ply"] == 1
    # 停着的一手已落盘
    log = (tmp_path / "games" / f"{gid}.jsonl").read_text(encoding="utf-8")
    assert "停着" in log
    # 黑方照常走棋
    r = await client.post(f"/api/games/{gid}/move", json={"move": "b9c7"},
                          headers={"X-Seat-Token": g["black_token"]})
    assert r.status_code == 200


async def test_lose_mode_via_api(client):
    """正式规则模式：步时超时即判负。"""
    g = await new_game(client, step_seconds=0.2, increment=0, step_timeout="lose")
    await join_both(client, g)
    await asyncio.sleep(0.4)
    st = (await client.get(f"/api/games/{g['game_id']}/state",
                           headers={"X-Seat-Token": g["black_token"]})).json()
    assert st["result"] == "black_win" and st["result_reason"] == "timeout"


async def test_invalid_step_timeout_400(client):
    r = await client.post("/api/games", json={
        "red_name": "A", "black_name": "B", "step_timeout": "banana"})
    assert r.status_code == 400


async def test_watch_page_served(client):
    """观战页：无需鉴权，内嵌 EventSource 指向 spectate 流。"""
    g = await new_game(client)
    r = await client.get(f"/watch/{g['game_id']}")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "EventSource" in r.text and "/api/games/" in r.text
    assert "楚河" in r.text                        # 象棋元素
    assert 'addEventListener("start"' in r.text    # 开赛事件：撤下等待横幅
    assert "m.clocks" in r.text                    # 每步用服务端时钟重新校准


async def test_state_settle_broadcasts_result(tmp_path):
    """超时由 state 查询结算时，观战 SSE 也要收到 result 事件。"""
    app = create_app(data_dir=str(tmp_path))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                           log_level="error"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    try:
        async with AsyncClient(base_url=f"http://127.0.0.1:{port}") as c:
            g = (await c.post("/api/games", json={
                "red_name": "A", "black_name": "B",
                "step_seconds": 0.2, "increment": 0, "step_timeout": "lose"})).json()
            gid = g["game_id"]
            for t in (g["red_token"], g["black_token"]):
                await c.post(f"/api/games/{gid}/join", headers={"X-Seat-Token": t})
            async with c.stream("GET", f"/api/games/{gid}/spectate") as resp:
                await asyncio.sleep(0.4)            # 红方超步时
                st = (await c.get(f"/api/games/{gid}/state",
                                  headers={"X-Seat-Token": g["black_token"]})).json()
                assert st["result"] == "black_win"
                buf = ""
                got_result_event = False
                async for line in resp.aiter_lines():
                    buf += line + "\n"
                    if line.startswith("event: result"):
                        got_result_event = True
                    elif got_result_event and line.startswith("data:"):
                        break
                assert "event: result" in buf and "black_win" in buf
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def test_notes_not_duplicated_in_jsonl(client, tmp_path):
    """I1 回归：每条思考在棋谱里只出现一次。"""
    g = await new_game(client)
    await join_both(client, g)
    gid = g["game_id"]
    await client.post(f"/api/games/{gid}/note", json={"text": "单独思考"},
                      headers={"X-Seat-Token": g["red_token"]})
    r = await client.post(f"/api/games/{gid}/move",
                          json={"move": "h2e2", "note": "走棋思考"},
                          headers={"X-Seat-Token": g["red_token"]})
    pv = r.json()
    await client.post(f"/api/games/{gid}/commit",
                      json={"pending_id": pv["pending_id"]},
                      headers={"X-Seat-Token": g["red_token"]})
    log = (tmp_path / "games" / f"{gid}.jsonl").read_text(encoding="utf-8")
    assert log.count("单独思考") == 1
    assert log.count("走棋思考") == 1


async def test_timeout_settled_on_state(client):
    """I2 回归：开赛后走棋方超时且不行动，对手查 state 也能拿到终局。"""
    g = await new_game(client, step_seconds=0.2, increment=0, step_timeout="lose")
    await join_both(client, g)
    await asyncio.sleep(0.4)
    st = await client.get(f"/api/games/{g['game_id']}/state",
                          headers={"X-Seat-Token": g["black_token"]})
    assert st.json()["result"] == "black_win"
    assert st.json()["result_reason"] == "timeout"


async def test_log_endpoint(client):
    """P0-1：着法日志端点返回 history 摘要，需座位令牌鉴权。"""
    g = await new_game(client)
    await join_both(client, g)
    gid = g["game_id"]
    pv = (await client.post(f"/api/games/{gid}/move", json={"move": "h2e2"},
                            headers={"X-Seat-Token": g["red_token"]})).json()
    await client.post(f"/api/games/{gid}/commit",
                      json={"pending_id": pv["pending_id"]},
                      headers={"X-Seat-Token": g["red_token"]})
    r = await client.get(f"/api/games/{gid}/log",
                         headers={"X-Seat-Token": g["black_token"]})
    assert r.status_code == 200
    body = r.json()
    assert "time_status" in body
    assert len(body["moves"]) == 1
    m = body["moves"][0]
    assert m["ply"] == 1 and m["color"] == "r" and m["iccs"] == "h2e2"
    assert m["chinese"] == "炮二平五" and m["elapsed"] >= 0
    r2 = await client.get(f"/api/games/{gid}/log")
    assert r2.status_code == 403


async def test_play_endpoint(client):
    """P1-5：原子走子端点——相符/缺省 expect 落子，不符返回 400 且带 actual。"""
    g = await new_game(client)
    await join_both(client, g)
    gid = g["game_id"]
    h = {"X-Seat-Token": g["red_token"]}
    r = await client.post(f"/api/games/{gid}/play",
                          json={"move": "h2e2", "expect": "炮八平五"}, headers=h)
    assert r.status_code == 400
    assert "actual" in r.json() and r.json()["actual"] == "炮二平五"
    st = (await client.get(f"/api/games/{gid}/state", headers=h)).json()
    assert st["ply"] == 0                    # 未落子
    r = await client.post(f"/api/games/{gid}/play",
                          json={"move": "h2e2", "expect": "炮二平五"}, headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["chinese"] == "炮二平五" and body["result"] is None
    assert "time_status" in body
    st = (await client.get(f"/api/games/{gid}/state", headers=h)).json()
    assert st["ply"] == 1 and st["turn"] == "b"
    # 无 expect 也照走
    hb = {"X-Seat-Token": g["black_token"]}
    r = await client.post(f"/api/games/{gid}/play",
                          json={"move": "b9c7", "note": "跳马"}, headers=hb)
    assert r.status_code == 200 and r.json()["ply"] == 2
    # 非法走法 400
    r = await client.post(f"/api/games/{gid}/play",
                          json={"move": "h2e3"}, headers=h)
    assert r.status_code == 400


async def test_moves_endpoint(client):
    """P1-8：结构化走法端点——轮到该方返回带中文/吃子/将军标记的列表。"""
    g = await new_game(client)
    await join_both(client, g)
    gid = g["game_id"]
    r = await client.get(f"/api/games/{gid}/moves",
                         headers={"X-Seat-Token": g["red_token"]})
    assert r.status_code == 200
    moves = r.json()["moves"]
    assert moves
    assert all({"iccs", "chinese", "capture", "gives_check", "piece"} <= set(m)
               for m in moves)
    cannon = [m for m in moves if m["iccs"] == "h2e2"][0]
    assert cannon["chinese"] == "炮二平五" and cannon["piece"] == "C"
    assert cannon["capture"] is False and cannon["gives_check"] is False
    # 先按棋子类型再按起点坐标排序
    keys = [(m["piece"].upper(), m["iccs"][:2]) for m in moves]
    assert keys == sorted(keys)
    # 未轮到的一方拿到空列表
    r2 = await client.get(f"/api/games/{gid}/moves",
                          headers={"X-Seat-Token": g["black_token"]})
    assert r2.status_code == 200 and r2.json()["moves"] == []
    # 未鉴权 403
    r3 = await client.get(f"/api/games/{gid}/moves")
    assert r3.status_code == 403


async def test_state_contains_names(client):
    """人机对弈页需要显示双方名字：state 必须带 names 字段。"""
    g = await new_game(client)
    st = (await client.get(f"/api/games/{g['game_id']}/state",
                           headers={"X-Seat-Token": g["red_token"]})).json()
    assert st["names"] == {"r": "Kimi", "b": "Claude"}
    st2 = (await client.get(f"/api/games/{g['game_id']}/state",
                            headers={"X-Seat-Token": g["black_token"]})).json()
    assert st2["names"] == {"r": "Kimi", "b": "Claude"}


async def test_play_page_route(client):
    """GET /play/{gid} 返回人机对弈静态页。"""
    g = await new_game(client)
    r = await client.get(f"/play/{g['game_id']}")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "人机对弈" in r.text


async def test_board_png_endpoint(client):
    """棋盘截图：PNG 格式、按座位视角渲染（黑方翻转）、令牌错误 403。"""
    g = await new_game(client)
    gid = g["game_id"]
    r = await client.get(f"/api/games/{gid}/board.png")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"
    red = await client.get(f"/api/games/{gid}/board.png",
                           headers={"X-Seat-Token": g["red_token"]})
    black = await client.get(f"/api/games/{gid}/board.png",
                             params={"token": g["black_token"]})
    assert red.status_code == black.status_code == 200
    assert red.content != black.content                # 黑方视角已翻转
    bad = await client.get(f"/api/games/{gid}/board.png", params={"token": "wrong"})
    assert bad.status_code == 403
