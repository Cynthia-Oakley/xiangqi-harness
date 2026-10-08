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
                 draw_halfmoves=120):
        self.id = gid
        self.board = Board.initial()
        self.names = {RED: red_name, BLACK: black_name}
        self.tokens = {RED: secrets.token_urlsafe(16), BLACK: secrets.token_urlsafe(16)}
        self.step_seconds = float(step_seconds)
        self.increment = float(increment)
        self.draw_halfmoves = draw_halfmoves
        self.remaining = {RED: float(base_seconds), BLACK: float(base_seconds)}
        self.turn_started = time.monotonic()
        self.pending = None          # {"id","color","move","board_after","expires"}
        self.result = None
        self.result_reason = None
        self.notes = []              # {"ply","color","text","ts"}
        self.history = []            # {"ply","color","iccs","chinese","elapsed","fen"}
        self.draw_offered_by = None
        self.lock = threading.RLock()

    # ---- 鉴权 ----
    def check_token(self, color, token):
        if not token or not secrets.compare_digest(token, self.tokens.get(color, "")):
            raise AuthError("令牌无效")

    # ---- 计时 ----
    def _elapsed(self):
        return time.monotonic() - self.turn_started

    def clocks(self):
        side = self.board.turn
        out = {s: round(t, 1) for s, t in self.remaining.items()}
        if not self.result:
            out[side] = round(self.remaining[side] - self._elapsed(), 1)
        return {
            "remaining": out,
            "step_remaining": None if self.result else round(self.step_seconds - self._elapsed(), 1),
        }

    def _expire_if_timeout(self):
        """在走棋方操作入口结算超时；超时即判负并抛 GameOver。"""
        if self.result:
            raise GameOver(f"对局已结束: {self.result}")
        side = self.board.turn
        if self._elapsed() > self.step_seconds or self.remaining[side] - self._elapsed() <= 0:
            self.result = "black_win" if side == RED else "red_win"
            self.result_reason = "timeout"
            raise GameOver(f"{self.names[side]} 超时，判负")

    # ---- 走棋 ----
    def propose_move(self, color, iccs, note=None):
        with self.lock:
            self._expire_if_timeout()
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
            if self.remaining[color] <= 0:
                self.result = "black_win" if color == RED else "red_win"
                self.result_reason = "timeout"
            else:
                res = game_result(self.board, self.draw_halfmoves)
                if res:
                    self.result = res
                    self.result_reason = "halfmove_limit" if res == "draw" else "checkmate"
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
            self.result = "black_win" if color == RED else "red_win"
            self.result_reason = "resign"

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
                self.result = "draw"
                self.result_reason = "draw_agreement"
            else:
                raise RuleError("没有可接受的和棋提议")

    # ---- 对 Agent 的状态视图（不含 notes！）----
    def state_for(self, color):
        with self.lock:
            return {
                "game_id": self.id,
                "your_color": color,
                "turn": self.board.turn,
                "your_turn": self.board.turn == color and self.result is None,
                "fen": self.board.to_fen(),
                "ascii": render_ascii(self.board),
                "ply": len(self.history),
                "result": self.result,
                "result_reason": self.result_reason,
                "clocks": self.clocks(),
                "draw_offered": self.draw_offered_by is not None and self.draw_offered_by != color,
            }
