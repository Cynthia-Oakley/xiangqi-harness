"""单局对弈状态机：座位令牌、两阶段提交、双钟计时、终局、思考隔离。"""

import secrets
import threading
import time

from xiangqi.board import Board, RED, BLACK, iccs_to_rc
from xiangqi.notation import is_valid_iccs, to_chinese
from xiangqi.rules import apply_move, game_result, in_check, legal_moves
from xiangqi.textboard import render_ascii


class AuthError(Exception):
    pass


class RuleError(Exception):
    pass


class TurnError(RuleError):
    pass


class GameOver(RuleError):
    pass


class Game:
    def __init__(self, gid, red_name, black_name,
                 base_seconds=900, step_seconds=180, increment=10,
                 draw_halfmoves=120, step_timeout="pass"):
        if step_timeout not in ("pass", "lose"):
            raise ValueError(f"step_timeout 只能是 pass 或 lose: {step_timeout!r}")
        self.id = gid
        self.board = Board.initial()
        self.names = {RED: red_name, BLACK: black_name}
        self.tokens = {RED: secrets.token_urlsafe(16), BLACK: secrets.token_urlsafe(16)}
        self.step_seconds = float(step_seconds)
        self.step_timeout = step_timeout
        self.increment = float(increment)
        self.draw_halfmoves = draw_halfmoves
        self.remaining = {RED: float(base_seconds), BLACK: float(base_seconds)}
        self.joined = {RED: False, BLACK: False}
        self.started = False
        self.turn_started = None       # 双方入座后才置为 time.monotonic()
        self.pending = None          # {"id","color","move","board_after","expires"}
        self.result = None
        self.result_reason = None
        self.notes = []              # {"ply","color","text","ts"}
        self.history = []            # {"ply","color","iccs","chinese","elapsed","fen"}
        self.draw_offered_by = None
        self.events = []             # 待广播/落盘事件：{"event": "move"|"pass"|"result", "data": ...}
        self.lock = threading.RLock()

    # ---- 鉴权 ----
    def check_token(self, color, token):
        if not token or not secrets.compare_digest(token, self.tokens.get(color, "")):
            raise AuthError("令牌无效")

    # ---- 入座与开赛 ----
    def join(self, color):
        """入座；双方到齐即开赛（计时从此刻开始）。返回 True 表示本调用触发了开赛。"""
        with self.lock:
            if self.result:
                raise GameOver("对局已结束")
            self.joined[color] = True
            if self.started or not all(self.joined.values()):
                return False
            self.started = True
            self.turn_started = time.monotonic()
            return True

    # ---- 计时 ----
    def _elapsed(self):
        if not self.started:
            return 0.0
        return time.monotonic() - self.turn_started

    def clocks(self):
        side = self.board.turn
        out = {s: round(t, 1) for s, t in self.remaining.items()}
        if not self.result and self.started:
            out[side] = round(self.remaining[side] - self._elapsed(), 1)
        return {
            "remaining": out,
            "step_remaining": None if (self.result or not self.started)
            else round(self.step_seconds - self._elapsed(), 1),
        }

    def settle_timeout(self):
        """非抛出版超时结算：任何查询入口都可调用，让对局不再因双方不操作而悬置。
        返回 True 表示局面或结果发生了变化（停着或终局），事件进入 self.events。"""
        with self.lock:
            if self.result or not self.started:
                return False
            side = self.board.turn
            elapsed = self._elapsed()
            if self.remaining[side] - elapsed <= 0:        # 局时耗尽：两种模式都判负
                self._finish("black_win" if side == RED else "red_win", "timeout")
                return True
            if elapsed <= self.step_seconds:
                return False
            if self.step_timeout == "lose" or in_check(self.board, side):
                # 正式规则直接判负；被将军时不能停着，超时同样判负
                self._finish("black_win" if side == RED else "red_win", "timeout")
                return True
            # 宽松模式：步时超时记「停着」一手，耗时照扣、不加秒
            self.pending = None                            # 作废未确认的预览
            self.remaining[side] -= elapsed
            nb = self.board.copy()
            nb.turn = BLACK if side == RED else RED
            nb.halfmove += 1
            if side == BLACK:
                nb.fullmove += 1
            self.board = nb
            self.turn_started = time.monotonic()
            self.draw_offered_by = None
            entry = {
                "ply": len(self.history) + 1,
                "color": side,
                "iccs": "pass",
                "chinese": "停着",
                "elapsed": round(elapsed, 2),
                "fen": self.board.to_fen(),
            }
            self.history.append(entry)
            self.events.append({"event": "pass",
                                "data": entry | {"clocks": self.clocks()}})
            res = game_result(self.board, self.draw_halfmoves)
            if res:                                        # 停着也推进自然限着
                self._finish(res, "halfmove_limit" if res == "draw" else "checkmate")
            return True

    def _finish(self, result, reason):
        self.pending = None
        self.result = result
        self.result_reason = reason
        self.events.append({"event": "result",
                            "data": {"result": result, "reason": reason}})

    def pop_events(self):
        """取出并清空待广播事件（由服务层负责广播与落盘）。"""
        with self.lock:
            evs, self.events = self.events, []
            return evs

    def _expire_if_timeout(self):
        """在走棋方操作入口结算超时；超时即判负并抛 GameOver。"""
        self.settle_timeout()
        if self.result:
            raise GameOver(f"对局已结束: {self.result}（{self.names[self.board.turn]} 超时判负）")

    # ---- 走棋 ----
    def propose_move(self, color, iccs, note=None):
        with self.lock:
            self._expire_if_timeout()
            if not self.started:
                opp = BLACK if color == RED else RED
                raise RuleError(f"对局尚未开始：等待 {self.names[opp]} 入座后计时才开始")
            if color != self.board.turn:
                raise TurnError(f"还没轮到{self.names[color]}走棋")
            if not is_valid_iccs(iccs):
                raise RuleError(f"走法格式错误: {iccs!r}（应为 4 字符 ICCS，如 h2e2）")
            if iccs not in legal_moves(self.board, color):
                raise RuleError(f"非法走法: {iccs}")
            nb = apply_move(self.board, iccs)
            (fr, fc), (tr, tc) = iccs_to_rc(iccs[:2]), iccs_to_rc(iccs[2:])
            opp = BLACK if color == RED else RED
            expires_in = max(0.0, min(60.0, self.step_seconds - self._elapsed()))
            self.pending = {
                "id": secrets.token_urlsafe(8),
                "color": color,
                "move": iccs,
                "board_after": nb,
                "expires": time.monotonic() + expires_in,
            }
            if note:
                self._append_note(color, note)
            return {
                "pending_id": self.pending["id"],
                "move": iccs,
                "chinese": to_chinese(self.board, iccs),
                "piece": self.board.grid[fr][fc],
                "captured": self.board.grid[tr][tc],
                "gives_check": in_check(nb, opp),
                "fen_after": nb.to_fen(),
                "opp_replies": len(legal_moves(nb, opp)),
                "expires_in": round(expires_in, 1),
            }

    def commit(self, color, pending_id):
        with self.lock:
            if not self.pending or self.pending["id"] != pending_id:
                raise RuleError("没有匹配的待确认走法，请先 move")
            if self.pending["color"] != color:
                raise TurnError("不能确认对方的走法")
            if time.monotonic() > self.pending["expires"]:
                self.pending = None
                raise RuleError("预览已过期，请重新 move")
            self._expire_if_timeout()
            elapsed = self._elapsed()
            iccs = self.pending["move"]
            entry = {
                "ply": len(self.history) + 1,
                "color": color,
                "iccs": iccs,
                "chinese": to_chinese(self.board, iccs),
                "elapsed": round(elapsed, 2),
            }
            self.board = self.pending["board_after"]
            self.pending = None
            self.remaining[color] = self.remaining[color] - elapsed + self.increment
            self.turn_started = time.monotonic()
            self.draw_offered_by = None
            entry["fen"] = self.board.to_fen()
            self.history.append(entry)
            self.events.append({"event": "move",
                                "data": entry | {"clocks": self.clocks()}})
            if self.remaining[color] <= 0:
                self._finish("black_win" if color == RED else "red_win", "timeout")
            else:
                res = game_result(self.board, self.draw_halfmoves)
                if res:
                    self._finish(res, "halfmove_limit" if res == "draw" else "checkmate")
            return entry

    def cancel(self, color, pending_id):
        with self.lock:
            if not self.pending or self.pending["id"] != pending_id:
                raise RuleError("没有匹配的待确认走法")
            if self.pending["color"] != color:
                raise TurnError("不能取消对方的走法")
            self.pending = None

    # ---- 思考流（只对观战者可见）----
    def _append_note(self, color, text):
        self.notes.append({
            "ply": len(self.history) + 1,
            "color": color,
            "text": str(text)[:2000],
            "ts": round(time.time(), 3),
        })

    def add_note(self, color, text):
        with self.lock:
            if self.result:
                raise GameOver("对局已结束")
            self._append_note(color, text)

    # ---- 终局操作 ----
    def resign(self, color):
        with self.lock:
            if self.result:
                raise GameOver("对局已结束")
            self._finish("black_win" if color == RED else "red_win", "resign")

    def offer_draw(self, color):
        with self.lock:
            if self.result:
                raise GameOver("对局已结束")
            self.draw_offered_by = color

    def accept_draw(self, color):
        with self.lock:
            if self.result:
                raise GameOver("对局已结束")
            if self.draw_offered_by and self.draw_offered_by != color:
                self._finish("draw", "draw_agreement")
            else:
                raise RuleError("没有可接受的和棋提议")

    # ---- 对 Agent 的状态视图（不含 notes！）----
    def state_for(self, color):
        with self.lock:
            self.settle_timeout()        # 查询也结算超时，避免对局悬置
            return {
                "game_id": self.id,
                "your_color": color,
                "turn": self.board.turn,
                "started": self.started,
                "joined": dict(self.joined),
                "your_turn": self.started and self.board.turn == color and self.result is None,
                "fen": self.board.to_fen(),
                "ascii": render_ascii(self.board),
                "ply": len(self.history),
                "result": self.result,
                "result_reason": self.result_reason,
                "in_check": self.result is None and in_check(self.board, color),
                "clocks": self.clocks(),
                "draw_offered": self.draw_offered_by is not None and self.draw_offered_by != color,
            }
