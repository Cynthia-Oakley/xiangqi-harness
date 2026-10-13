"""FastAPI 裁判服务：HTTP API + SSE 观战广播 + 网页控制台。"""

import asyncio
import json
import os
import secrets
import signal
import time
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from xiangqi.rules import legal_moves
from xqserver.board_image import render_board_png
from xqserver.game import AuthError, ExpectMismatch, Game, GameOver, RuleError, TurnError
from xqserver.store import Store

RULES_TEXT = """中国象棋规则速查（ICCS 坐标：列 a-i 红方视角从左到右，行 0-9，0 为红方底线）
车：直线任意远，不可越子。马：日字，蹩马腿方向被占则不可走。
相/象：田字斜走，塞象眼不可走，不可过河。仕/士：九宫内斜走一格。
帅/将：九宫内直走一格；两帅将不得照面（白脸将）。
炮：直走；吃子须恰好隔一子（炮架）。兵/卒：只进不退，过河后可横走。
将死/困毙判负；超时判负；连续无吃子达到自然限着判和。"""


def _load_or_create_admin_key(data_dir) -> str:
    """控制台密钥：每个数据目录一份，跨重启复用（书签不失效），0600 权限。"""
    path = Path(data_dir) / "admin_key.txt"
    if path.exists():
        return path.read_text().strip()
    key = secrets.token_urlsafe(12)
    path.write_text(key)
    os.chmod(path, 0o600)
    return key


def create_app(data_dir="data", idle_minutes: float = 60, on_shutdown=None) -> FastAPI:
    """idle_minutes<=0 关闭空闲自动关机；on_shutdown 供测试替换真正的自杀动作。"""
    app = FastAPI(title="Xiangqi Harness")
    store = Store(data_dir)
    subscribers: dict[str, list[asyncio.Queue]] = {}
    notes_persisted: dict[str, int] = {}   # gid → 已落盘的 note 条数（防重复写入）
    admin_key = _load_or_create_admin_key(data_dir)
    started_at = time.monotonic()
    activity = {"last": time.monotonic()}   # 除健康检查/控制台轮询外的最近请求时刻

    @app.middleware("http")
    async def touch_activity(request, call_next):
        if not request.url.path.startswith(("/api/rules", "/api/admin")):
            activity["last"] = time.monotonic()
        return await call_next(request)

    def check_admin(key):
        if not key or not secrets.compare_digest(key, admin_key):
            raise HTTPException(403, "控制台密钥无效")

    @app.on_event("startup")
    async def idle_watchdog():
        if idle_minutes <= 0:
            return

        async def loop():
            while True:
                await asyncio.sleep(15)
                if time.monotonic() - activity["last"] > idle_minutes * 60:
                    shutdown()
        asyncio.create_task(loop())

    def shutdown():
        if on_shutdown:
            on_shutdown()
        else:
            os.kill(os.getpid(), signal.SIGTERM)

    def broadcast(gid, event, payload):
        for q in subscribers.get(gid, []):
            q.put_nowait({"event": event, "data": payload})

    def flush_events(g: Game):
        """把对局内累积的事件落盘并广播（走子/停着以 move 事件发给观战页）。"""
        for ev in g.pop_events():
            if ev["event"] in ("move", "pass"):
                store.append_record(g, {"type": "move", **ev["data"]})
                broadcast(g.id, "move", ev["data"])
            elif ev["event"] == "result":
                broadcast(g.id, "result", ev["data"])

    def persist_note(g: Game, n: dict):
        store.append_record(g, {"type": "note", **n})
        notes_persisted[g.id] = notes_persisted.get(g.id, 0) + 1

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

    @app.exception_handler(ExpectMismatch)
    async def expect_err(request, e):
        return JSONResponse({"error": str(e), "actual": e.actual}, 400)

    @app.exception_handler(RuleError)
    async def rule_err(request, e):
        return JSONResponse({"error": str(e)}, 400)

    class NewGame(BaseModel):
        red_name: str
        black_name: str
        base_seconds: float = 900
        step_seconds: float = 180
        increment: float = 20
        draw_halfmoves: int = 120
        step_timeout: str = "pass"
        assist: bool = False

    @app.post("/api/games")
    async def new_game(body: NewGame):
        if body.step_timeout not in ("pass", "lose"):
            raise HTTPException(400, "step_timeout 只能是 pass 或 lose")
        g = store.create(body.red_name, body.black_name,
                         base_seconds=body.base_seconds,
                         step_seconds=body.step_seconds,
                         increment=body.increment,
                         draw_halfmoves=body.draw_halfmoves,
                         step_timeout=body.step_timeout,
                         assist=body.assist)
        return {"game_id": g.id, "red_token": g.tokens["r"],
                "black_token": g.tokens["b"], "assist": g.assist,
                "watch_url": f"/api/games/{g.id}/spectate"}

    @app.post("/api/games/{gid}/join")
    async def join_game(gid: str, x_seat_token: str | None = Header(None)):
        g = get_game(gid)
        color = seat_color(g, x_seat_token)
        if g.join(color):            # 本调用凑齐双方 → 开赛，广播给观战页
            broadcast(gid, "start", {"names": g.names, "fen": g.board.to_fen(),
                                     "clocks": g.clocks()})
        opp = "b" if color == "r" else "r"
        return {"ok": True, "started": g.started,
                "waiting_for": None if g.started else g.names[opp]}

    @app.get("/api/games/{gid}/state")
    async def state(gid: str, x_seat_token: str | None = Header(None)):
        g = get_game(gid)
        color = seat_color(g, x_seat_token)
        st = g.state_for(color)
        flush_events(g)                # 查询结算出的停着/终局也推给观战者
        if st["your_turn"]:
            st["legal_moves"] = legal_moves(g.board, color)
        return st

    @app.get("/api/games/{gid}/board.png")
    async def board_png(gid: str, token: str | None = None,
                        x_seat_token: str | None = Header(None)):
        """当前棋盘 PNG（400x444，约 240 tokens）。带座位令牌时按该方视角（黑方翻转）。"""
        g = get_game(gid)
        tok = token or x_seat_token
        perspective = seat_color(g, tok) if tok else "r"
        last = (g.history[-1]["iccs"]
                if g.history and g.history[-1]["iccs"] != "pass" else None)
        return Response(render_board_png(g.board, perspective, last),
                        media_type="image/png",
                        headers={"Cache-Control": "no-store"})

    @app.get("/api/games/{gid}/log")
    async def game_log(gid: str, x_seat_token: str | None = Header(None)):
        g = get_game(gid)
        color = seat_color(g, x_seat_token)
        def entry(e):
            d = {k: e[k] for k in ("ply", "color", "iccs", "chinese", "elapsed")}
            d["captured"] = e.get("captured")
            if e.get("self_check") is not None:      # 仅辅助模式对局有此字段
                d["self_check"] = e["self_check"]
            return d
        return {"moves": [entry(e) for e in g.history],
                "time_status": g.time_status(color)}

    @app.get("/api/games/{gid}/moves")
    async def moves_endpoint(gid: str, x_seat_token: str | None = Header(None)):
        g = get_game(gid)
        color = seat_color(g, x_seat_token)
        return {"moves": g.moves_detail(color),
                "time_status": g.time_status(color)}

    @app.get("/api/games/{gid}/inspect/{square}")
    async def inspect_square(gid: str, square: str, token: str | None = None,
                             x_seat_token: str | None = Header(None)):
        """辅助模式：落点攻防清点（仅 assist 对局可用）。"""
        g = get_game(gid)
        seat_color(g, token or x_seat_token)            # 仅对局双方可查
        return {"square": square, "report": g.inspect_square(square)}

    class MoveIn(BaseModel):
        move: str
        note: str | None = None

    @app.post("/api/games/{gid}/move")
    async def move(gid: str, body: MoveIn, x_seat_token: str | None = Header(None)):
        g = get_game(gid)
        try:
            return g.propose_move(seat_color(g, x_seat_token), body.move, body.note)
        finally:
            flush_events(g)

    class PlayIn(BaseModel):
        move: str
        expect: str | None = None
        note: str | None = None

    @app.post("/api/games/{gid}/play")
    async def play(gid: str, body: PlayIn, x_seat_token: str | None = Header(None)):
        g = get_game(gid)
        try:
            entry = g.play(seat_color(g, x_seat_token), body.move,
                           expect=body.expect, note=body.note)
        finally:
            flush_events(g)
        done = notes_persisted.get(gid, 0)
        for n in g.notes[done:]:         # 只落盘尚未持久化的 note（含 play 附带的）
            persist_note(g, n)
        return entry | {"result": g.result, "result_reason": g.result_reason}

    class PendingIn(BaseModel):
        pending_id: str

    @app.post("/api/games/{gid}/commit")
    async def commit(gid: str, body: PendingIn, x_seat_token: str | None = Header(None)):
        g = get_game(gid)
        try:
            entry = g.commit(seat_color(g, x_seat_token), body.pending_id)
        finally:
            flush_events(g)
        done = notes_persisted.get(gid, 0)
        for n in g.notes[done:]:         # 只落盘尚未持久化的 note（含 move 附带的）
            persist_note(g, n)
        return entry | {"result": g.result, "result_reason": g.result_reason}

    @app.post("/api/games/{gid}/cancel")
    async def cancel(gid: str, body: PendingIn, x_seat_token: str | None = Header(None)):
        g = get_game(gid)
        color = seat_color(g, x_seat_token)
        g.cancel(color, body.pending_id)
        return {"ok": True, "time_status": g.time_status(color)}

    class NoteIn(BaseModel):
        text: str

    @app.post("/api/games/{gid}/note")
    async def note(gid: str, body: NoteIn, x_seat_token: str | None = Header(None)):
        g = get_game(gid)
        color = seat_color(g, x_seat_token)
        g.add_note(color, body.text)
        n = g.notes[-1]
        persist_note(g, n)
        broadcast(gid, "note", n)   # 只去观战 SSE；Agent 无任何接口能读到
        return {"ok": True, "time_status": g.time_status(color)}

    @app.post("/api/games/{gid}/resign")
    async def resign(gid: str, x_seat_token: str | None = Header(None)):
        g = get_game(gid)
        g.resign(seat_color(g, x_seat_token))
        flush_events(g)
        return {"result": g.result}

    @app.post("/api/games/{gid}/offer-draw")
    async def offer_draw(gid: str, x_seat_token: str | None = Header(None)):
        g = get_game(gid)
        color = seat_color(g, x_seat_token)
        g.offer_draw(color)
        return {"ok": True, "time_status": g.time_status(color)}

    @app.post("/api/games/{gid}/accept-draw")
    async def accept_draw(gid: str, x_seat_token: str | None = Header(None)):
        g = get_game(gid)
        g.accept_draw(seat_color(g, x_seat_token))
        flush_events(g)
        return {"result": g.result}

    @app.get("/api/games/{gid}/wait")
    async def wait(gid: str, ply: int = 0, x_seat_token: str | None = Header(None)):
        g = get_game(gid)
        color = seat_color(g, x_seat_token)
        for _ in range(300):        # 最长 30s
            g.settle_timeout()       # 轮询中结算超时（停着或终局）并广播
            flush_events(g)
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
                "result_reason": g.result_reason, "started": g.started,
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

    @app.get("/watch/{gid}")
    async def watch_page(gid: str):
        return FileResponse(Path(__file__).parent / "static" / "watch.html",
                            media_type="text/html")

    @app.get("/play/{gid}")
    async def play_page(gid: str):
        return FileResponse(Path(__file__).parent / "static" / "play.html",
                            media_type="text/html")

    @app.get("/api/rules")
    async def rules():
        return {"rules": RULES_TEXT}

    # ---- 网页控制台（人类管理入口，无需命令行）----
    @app.get("/admin")
    async def admin_page():
        return FileResponse(Path(__file__).parent / "static" / "admin.html",
                            media_type="text/html")

    @app.get("/api/admin/status")
    async def admin_status(key: str | None = None,
                           x_admin_key: str | None = Header(None)):
        check_admin(key or x_admin_key)
        idle_in = (None if idle_minutes <= 0 else
                   max(0.0, idle_minutes * 60 - (time.monotonic() - activity["last"])))
        return {
            "pid": os.getpid(),
            "uptime_s": round(time.monotonic() - started_at, 1),
            "idle_minutes": idle_minutes,
            "idle_in_s": None if idle_in is None else round(idle_in, 1),
            "games": [{
                "game_id": g.id, "red": g.names["r"], "black": g.names["b"],
                "started": g.started, "result": g.result,
                "result_reason": g.result_reason, "ply": len(g.history),
            } for g in store.games.values()],
        }

    @app.post("/api/admin/games/{gid}/abort")
    async def admin_abort(gid: str, key: str | None = None,
                          x_admin_key: str | None = Header(None)):
        check_admin(key or x_admin_key)
        g = get_game(gid)
        g.abort()
        flush_events(g)
        return {"result": g.result, "result_reason": g.result_reason}

    @app.post("/api/admin/shutdown")
    async def admin_shutdown(key: str | None = None,
                             x_admin_key: str | None = Header(None)):
        check_admin(key or x_admin_key)

        async def later():
            await asyncio.sleep(0.5)         # 先把响应发出去
            shutdown()
        asyncio.create_task(later())
        return {"ok": True, "message": "裁判服务器即将停止"}

    return app
