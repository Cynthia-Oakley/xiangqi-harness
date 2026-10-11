"""网页控制台：密钥鉴权、状态、强制终止对局、优雅关机、密钥跨重启复用。"""

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from xqserver.app import _load_or_create_admin_key, create_app


@pytest.fixture
async def client(tmp_path):
    app = create_app(data_dir=str(tmp_path), idle_minutes=0)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


def admin_key(tmp_path):
    return (tmp_path / "admin_key.txt").read_text().strip()


async def test_admin_status_auth(client, tmp_path):
    key = admin_key(tmp_path)
    assert (await client.get("/api/admin/status")).status_code == 403
    assert (await client.get("/api/admin/status",
                             params={"key": "wrong"})).status_code == 403
    r = await client.get("/api/admin/status", params={"key": key})
    assert r.status_code == 200
    d = r.json()
    assert d["pid"] > 0 and d["games"] == []
    assert d["idle_minutes"] == 0 and d["idle_in_s"] is None
    r2 = await client.get("/api/admin/status", headers={"X-Admin-Key": key})
    assert r2.status_code == 200


async def test_admin_key_persisted(tmp_path):
    k1 = _load_or_create_admin_key(str(tmp_path))
    assert _load_or_create_admin_key(str(tmp_path)) == k1   # 跨重启复用，书签不失效


async def test_admin_abort(client, tmp_path):
    key = admin_key(tmp_path)
    g = (await client.post("/api/games",
                           json={"red_name": "A", "black_name": "B"})).json()
    gid = g["game_id"]
    r = await client.post(f"/api/admin/games/{gid}/abort", params={"key": key})
    assert r.json() == {"result": "draw", "result_reason": "aborted"}
    st = (await client.get(f"/api/games/{gid}/state",
                           headers={"X-Seat-Token": g["red_token"]})).json()
    assert st["result"] == "draw" and st["result_reason"] == "aborted"
    r = await client.post(f"/api/admin/games/{gid}/abort", params={"key": key})
    assert r.status_code == 409          # 已结束的不能重复终止
    assert (await client.post(f"/api/admin/games/{gid}/abort")).status_code == 403


async def test_admin_shutdown_calls_hook(tmp_path):
    fired = []
    app = create_app(data_dir=str(tmp_path), idle_minutes=0,
                     on_shutdown=lambda: fired.append(1))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        key = admin_key(tmp_path)
        assert (await c.post("/api/admin/shutdown")).status_code == 403
        r = await c.post("/api/admin/shutdown", params={"key": key})
        assert r.status_code == 200 and r.json()["ok"]
        await asyncio.sleep(0.8)
        assert fired == [1]


async def test_admin_page_route(client):
    r = await client.get("/admin")
    assert r.status_code == 200 and "控制台" in r.text
