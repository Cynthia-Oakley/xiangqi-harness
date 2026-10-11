from httpx import ASGITransport, AsyncClient
from xqserver.app import create_app


async def test_two_agents_play_full_game(tmp_path):
    """两个模拟 Agent 各用合法走法列表的第一步轮流走，直到自然限着和棋。"""
    app = create_app(data_dir=str(tmp_path))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        g = (await c.post("/api/games", json={
            "red_name": "AgentA", "black_name": "AgentB",
            "draw_halfmoves": 8, "step_seconds": 600})).json()
        tokens = {"r": g["red_token"], "b": g["black_token"]}
        for color in ("r", "b"):
            r = await c.post(f"/api/games/{g['game_id']}/join",
                             headers={"X-Seat-Token": tokens[color]})
            assert r.status_code == 200
        result = None
        for _ in range(30):
            for color in ("r", "b"):
                h = {"X-Seat-Token": tokens[color]}
                st = (await c.get(f"/api/games/{g['game_id']}/state", headers=h)).json()
                if st["result"]:
                    result = st["result"]
                    break
                if not st["your_turn"]:
                    continue
                move = st["legal_moves"][0]
                pv = (await c.post(f"/api/games/{g['game_id']}/move",
                                   json={"move": move,
                                         "note": f"{color} 思考: 走 {move}"},
                                   headers=h)).json()
                # 模拟 Agent 的落棋确认：预览的走法必须与自己的意图一致
                assert pv["move"] == move and "fen_after" in pv
                r = await c.post(f"/api/games/{g['game_id']}/commit",
                                 json={"pending_id": pv["pending_id"]}, headers=h)
                assert r.status_code == 200
                if r.json().get("result"):
                    result = r.json()["result"]
                    break
            if result:
                break
        assert result == "draw"

        # 防泄漏断言：黑方视角的任何响应都不含红方 note
        st = await c.get(f"/api/games/{g['game_id']}/state",
                         headers={"X-Seat-Token": g["black_token"]})
        assert "思考" not in st.text
