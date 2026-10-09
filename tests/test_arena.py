import asyncio
import json
import socket
import stat
import os

import httpx
import uvicorn

from xqcli.arena import setup_arena
from xqcli.prompt_template import render_agent_prompt


def test_prompt_with_custom_xq_cmd():
    p = render_agent_prompt("g-1", "r", "tok123", "http://127.0.0.1:8000",
                            xq_cmd="/abs/path/xq")
    assert "/abs/path/xq join g-1 red tok123" in p
    assert "/abs/path/xq" in p.splitlines()[3] or "完整路径" in p  # 有醒目提示


async def _serve(app):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                           log_level="error"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    return server, task, port


async def test_setup_arena_full(tmp_path, monkeypatch):
    """一条 setup_arena 完成：建对局、建目录、写提示词、预入座，且座位可直接走棋。"""
    monkeypatch.chdir(tmp_path)
    from xqserver.app import create_app
    server, task, port = await _serve(create_app(data_dir=str(tmp_path / "data")))
    try:
        url = f"http://127.0.0.1:{port}"
        # setup_arena 是同步函数（供 CLI 用）；放线程里跑以免堵住测试事件循环
        info = await asyncio.to_thread(setup_arena, "Kimi", "DeepSeek", url,
                                       "arena", "/abs/xq")
        gid = info["game_id"]
        for cn in ("red", "black"):
            d = tmp_path / "arena" / f"{gid}-{cn}"
            prompt = (d / "PROMPT.md").read_text(encoding="utf-8")
            assert gid in prompt and "/abs/xq" in prompt
            assert stat.S_IMODE(os.stat(d / "PROMPT.md").st_mode) == 0o600
            seat = json.loads((d / ".xqseat.json").read_text())
            assert stat.S_IMODE(os.stat(d / ".xqseat.json").st_mode) == 0o600
            assert seat["game_id"] == gid
        # 预入座后可直接用座位走棋：红方 state → move → commit
        seat = json.loads((tmp_path / "arena" / f"{gid}-red" / ".xqseat.json").read_text())
        h = {"X-Seat-Token": seat["token"]}
        async with httpx.AsyncClient(base_url=url) as c:
            st = (await c.get(f"/api/games/{gid}/state", headers=h)).json()
            assert st["your_turn"] is True
            pv = (await c.post(f"/api/games/{gid}/move",
                               json={"move": "h2e2"}, headers=h)).json()
            r = await c.post(f"/api/games/{gid}/commit",
                             json={"pending_id": pv["pending_id"]}, headers=h)
            assert r.status_code == 200
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
