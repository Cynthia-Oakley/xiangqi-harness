"""FastAPI 裁判服务：HTTP API + SSE 观战广播。"""

import asyncio
import json

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from xiangqi.rules import legal_moves
from xqserver.game import AuthError, Game, GameOver, RuleError, TurnError
from xqserver.store import Store

RULES_TEXT = """中国象棋规则速查（ICCS 坐标：列 a-i 红方视角从左到右，行 0-9，0 为红方底线）
车：直线任意远，不可越子。马：日字，蹩马腿方向被占则不可走。
相/象：田字斜走，塞象眼不可走，不可过河。仕/士：九宫内斜走一格。
帅/将：九宫内直走一格；两帅将不得照面（白脸将）。
炮：直走；吃子须恰好隔一子（炮架）。兵/卒：只进不退，过河后可横走。
将死/困毙判负；超时判负；连续无吃子达到自然限着判和。"""


def create_app(data_dir="data") -> FastAPI:
    app = FastAPI(title="Xiangqi Arena")
    store = Store(data_dir)
    subscribers: dict[str, list[asyncio.Queue]] = {}

    def broadcast(gid, event, payload):
        for q in subscribers.get(gid, []):
            q.put_nowait({"event": event, "data": payload})

    def get_game(gid) -> Game:
        try:
            return store.get(gid)
        except KeyError:
            raise HTTPException(404, "对局不存在")

    def seat_color(game: Game, token: str | None) -> str:
        for color in ("r", "b"):
            try:
                game.check_token(color, token)
                return color
            except AuthError:
                continue
        raise HTTPException(403, "令牌无效")

    @app.exception_handler(AuthError)
    async def auth_err(request, e):
        return JSONResponse({"error": str(e)}, 403)

    @app.exception_handler(TurnError)
    async def turn_err(request, e):
        return JSONResponse({"error": str(e)}, 409)

    @app.exception_handler(GameOver)
    async def over_err(request, e):
        return JSONResponse({"error": str(e)}, 409)

    @app.exception_handler(RuleError)
    async def rule_err(request, e):
        return JSONResponse({"error": str(e)}, 400)

    class NewGame(BaseModel):
        red_name: str
        black_name: str
        base_seconds: float = 900
        step_seconds: float = 180
        increment: float = 10
        draw_halfmoves: int = 120

    @app.post("/api/games")
    async def new_game(body: NewGame):
        g = store.create(body.red_name, body.black_name,
                         base_seconds=body.base_seconds,
                         step_seconds=body.step_seconds,
                         increment=body.increment,
                         draw_halfmoves=body.draw_halfmoves)
        return {"game_id": g.id, "red_token": g.tokens["r"],
                "black_token": g.tokens["b"], "watch_url": f"/watch/{g.id}"}

    @app.get("/api/games/{gid}/state")
    async def state(gid: str, x_seat_token: str | None = Header(None)):
        g = get_game(gid)
        color = seat_color(g, x_seat_token)
        st = g.state_for(color)
        if st["your_turn"]:
            st["legal_moves"] = legal_moves(g.board, color)
        return st

    class MoveIn(BaseModel):
        move: str
        note: str | None = None

    @app.post("/api/games/{gid}/move")
    async def move(gid: str, body: MoveIn, x_seat_token: str | None = Header(None)):
        g = get_game(gid)
        return g.propose_move(seat_color(g, x_seat_token), body.move, body.note)

    class PendingIn(BaseModel):
        pending_id: str

    @app.post("/api/games/{gid}/commit")
    async def commit(gid: str, body: PendingIn, x_seat_token: str | None = Header(None)):
        g = get_game(gid)
        entry = g.commit(seat_color(g, x_seat_token), body.pending_id)
        store.append_record(g, {"type": "move", **entry})
        for n in g.notes:
            if n["ply"] == entry["ply"]:
                store.append_record(g, {"type": "note", **n})
        broadcast(gid, "move", entry)
        if g.result:
            broadcast(gid, "result", {"result": g.result, "reason": g.result_reason})
        return entry | {"result": g.result, "result_reason": g.result_reason}

    @app.post("/api/games/{gid}/cancel")
    async def cancel(gid: str, body: PendingIn, x_seat_token: str | None = Header(None)):
        g = get_game(gid)
        g.cancel(seat_color(g, x_seat_token), body.pending_id)
        return {"ok": True}

    class NoteIn(BaseModel):
        text: str

    @app.post("/api/games/{gid}/note")
    async def note(gid: str, body: NoteIn, x_seat_token: str | None = Header(None)):
        g = get_game(gid)
        color = seat_color(g, x_seat_token)
        g.add_note(color, body.text)
        n = g.notes[-1]
        store.append_record(g, {"type": "note", **n})
        broadcast(gid, "note", n)   # 只去观战 SSE；Agent 无任何接口能读到
        return {"ok": True}

    @app.post("/api/games/{gid}/resign")
    async def resign(gid: str, x_seat_token: str | None = Header(None)):
        g = get_game(gid)
        g.resign(seat_color(g, x_seat_token))
        broadcast(gid, "result", {"result": g.result, "reason": g.result_reason})
        return {"result": g.result}

    @app.post("/api/games/{gid}/offer-draw")
    async def offer_draw(gid: str, x_seat_token: str | None = Header(None)):
        g = get_game(gid)
        g.offer_draw(seat_color(g, x_seat_token))
        return {"ok": True}

    @app.post("/api/games/{gid}/accept-draw")
    async def accept_draw(gid: str, x_seat_token: str | None = Header(None)):
        g = get_game(gid)
        g.accept_draw(seat_color(g, x_seat_token))
        broadcast(gid, "result", {"result": g.result, "reason": g.result_reason})
        return {"result": g.result}

    @app.get("/api/games/{gid}/wait")
    async def wait(gid: str, ply: int = 0, x_seat_token: str | None = Header(None)):
        g = get_game(gid)
        color = seat_color(g, x_seat_token)
        for _ in range(300):        # 最长 30s
            if g.result or len(g.history) > ply:
                break
            await asyncio.sleep(0.1)
        return g.state_for(color)

    @app.get("/api/games/{gid}/spectate")
    async def spectate(gid: str):
        g = get_game(gid)
        q: asyncio.Queue = asyncio.Queue()
        subscribers.setdefault(gid, []).append(q)

        async def stream():
            snapshot = {
                "fen": g.board.to_fen(), "history": g.history,
                "notes": g.notes, "clocks": g.clocks(),
                "names": g.names, "result": g.result,
            }
            yield f"event: snapshot\ndata: {json.dumps(snapshot, ensure_ascii=False)}\n\n"
            try:
                while True:
                    ev = await q.get()
                    yield f"event: {ev['event']}\ndata: {json.dumps(ev['data'], ensure_ascii=False)}\n\n"
                    if ev["event"] == "result":
                        break
            finally:
                subscribers[gid].remove(q)

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.get("/api/rules")
    async def rules():
        return {"rules": RULES_TEXT}

    return app
