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


async def test_create_and_state(client):
    g = await new_game(client)
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
    r = await client.post(f"/api/games/{g['game_id']}/move",
                          json={"move": "b9c7"},
                          headers={"X-Seat-Token": g["black_token"]})
    assert r.status_code == 409


async def test_full_move_cycle_and_jsonl(client, tmp_path):
    g = await new_game(client, draw_halfmoves=2)
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
                first = await resp.aiter_lines().__anext__()
        assert "snapshot" in first
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


async def test_notes_not_duplicated_in_jsonl(client, tmp_path):
    """I1 回归：每条思考在棋谱里只出现一次。"""
    g = await new_game(client)
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
    """I2 回归：走棋方超时后不行动，对手查 state 也能拿到终局。"""
    g = await new_game(client, step_seconds=0.2, increment=0)
    await asyncio.sleep(0.4)
    st = await client.get(f"/api/games/{g['game_id']}/state",
                          headers={"X-Seat-Token": g["black_token"]})
    assert st.json()["result"] == "black_win"
    assert st.json()["result_reason"] == "timeout"
