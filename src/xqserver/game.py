"""单局对弈状态机：座位令牌、两阶段提交、双钟计时、终局、思考隔离。"""

import secrets
import threading
import time

from xiangqi.attack import self_check, square_report
from xiangqi.board import Board, RED, BLACK, iccs_to_rc
from xiangqi.notation import to_chinese
from xiangqi.rules import apply_move, explain_illegal, game_result, in_check, legal_moves
from xiangqi.textboard import GLYPHS, piece_listing, render_ascii


class AuthError(Exception):
    pass


class RuleError(Exception):
    pass


class TurnError(RuleError):
    pass


class GameOver(RuleError):
    pass


class ExpectMismatch(RuleError):
    """原子走子的 expect 核对失败：不落子，携带实际中文着法。"""

    def __init__(self, message, actual):
        super().__init__(message)
        self.actual = actual


def _fmt_secs(x):
    m, s = divmod(int(round(x)), 60)
    return f"{m}m{s}s" if m else f"{s}s"


class Game:
    def __init__(self, gid, red_name, black_name,
                 base_seconds=900, step_seconds=180, increment=10,
                 draw_halfmoves=120, step_timeout="pass", assist=False,
                 sandbox_budget=0):
        if step_timeout not in ("pass", "lose"):
            raise ValueError(f"step_timeout 只能是 pass 或 lose: {step_timeout!r}")
        self.id = gid
        self.assist = assist           # 辅助模式：攻防清点 + 走后自检（棋谱会标记）
        self.sandbox_budget = int(sandbox_budget)   # 每回合沙盒推演节点配额；0=未开启
        self.sim_remaining = {RED: self.sandbox_budget, BLACK: self.sandbox_budget}
        self.sims = {RED: {}, BLACK: {}}            # color → {sim_id: [Board, ...]}（栈底=分支点）
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

    def time_status(self, color):
        """给 Agent 的人话时间状态：本步已用 + 局时剩余 + 对方耗时 + 紧迫度。"""
        if self.result:
            return "对局已结束。"
        if not self.started:
            return "等待双方入座，计时未开始。"
        my_turn = self.board.turn == color
        elapsed = self._elapsed()
        rem = self.remaining[color] - (elapsed if my_turn else 0.0)
        parts = []
        if my_turn:
            parts.append(f"本步已用 {_fmt_secs(elapsed)}（上限 {_fmt_secs(self.step_seconds)}）")
        else:
            parts.append("对方思考中")
        parts.append(f"局时还剩 {_fmt_secs(max(0.0, rem))}（每步 +{_fmt_secs(self.increment)}）")
        if self.history and self.history[-1]["color"] != color:
            parts.append(f"对方上一步用时 {_fmt_secs(self.history[-1]['elapsed'])}")
        msg = "｜".join(parts)
        if my_turn and elapsed >= 0.8 * self.step_seconds:
            msg += "　⚠️ 本步时间即将耗尽，立即提交当前最佳走法！"
        elif rem <= 2 * self.step_seconds:
            msg += "　⚠️ 局时紧张，请加快节奏。"
        return msg

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
            self._reset_sandbox()                       # 停着也推进局面：分支失效、配额恢复
            entry = {
                "ply": len(self.history) + 1,
                "color": side,
                "iccs": "pass",
                "chinese": "停着",
                "captured": None,
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

    # ---- 多步推演沙盒（只给事实，不影响正式对局）----
    MAX_SIMS_PER_SIDE = 2

    def _reset_sandbox(self):
        """正式局面推进后：配额恢复满，所有沙盒分支失效（它们分叉自旧局面）。"""
        if not self.sandbox_budget:
            return
        self.sim_remaining = {RED: self.sandbox_budget, BLACK: self.sandbox_budget}
        self.sims = {RED: {}, BLACK: {}}

    def _require_sandbox(self, color):
        if not self.sandbox_budget:
            raise RuleError("本局未开启沙盒推演（创建对局时加 --sandbox）")
        self.settle_timeout()
        if self.result:
            raise GameOver("对局已结束")
        if not self.started:
            raise RuleError("对局尚未开始：双方入座后才能使用沙盒")

    def _charge(self, color, n):
        left = self.sim_remaining[color]
        if left < n:
            raise RuleError(
                f"沙盒配额不足：本回合还剩 {left} 个节点，本次需要 {n} "
                f"（每回合共 {self.sandbox_budget} 个，正式落子后恢复）")
        self.sim_remaining[color] -= n

    def _terminal_fact(self, board):
        """沙盘局面的终局事实（将死/困毙/自然限着）；未终局返回 None。"""
        res = game_result(board, self.draw_halfmoves)
        if not res:
            return None
        if res == "draw":
            return "和棋（自然限着）"
        kind = "将死" if in_check(board, board.turn) else "困毙"
        return f"{'红' if res == 'red_win' else '黑'}方胜（{kind}）"

    def _run_line(self, board, moves):
        """在 board 副本上依次假设双方走子，返回 (每步事实, 最终棋盘, 是否全部合法)。
        遇非法走法立即中止并附原因。只给事实：合法性/中文着法/吃子/将军/终局。"""
        steps, cur = [], board
        for i, iccs in enumerate(moves, 1):
            side = cur.turn
            reason = explain_illegal(cur, side, iccs)
            if reason:
                steps.append({"ply": i, "iccs": iccs, "color": side,
                              "legal": False, "reason": reason})
                return steps, cur, False
            chinese = to_chinese(cur, iccs)
            tr, tc = iccs_to_rc(iccs[2:])
            captured = cur.grid[tr][tc]
            cur = apply_move(cur, iccs)
            step = {"ply": i, "iccs": iccs, "color": side, "legal": True,
                    "chinese": chinese, "captured": captured,
                    "gives_check": in_check(cur, cur.turn)}
            fact = self._terminal_fact(cur)
            if fact:
                step["terminal"] = fact
                steps.append(step)
                return steps, cur, True
            steps.append(step)
        return steps, cur, True

    def _sandbox_status(self, color):
        return {"budget_total": self.sandbox_budget,
                "budget_left": self.sim_remaining[color],
                "sims": {sid: len(stack) - 1 for sid, stack in self.sims[color].items()}}

    def _line_view(self, color, steps, final):
        """一条推演线的完整返回：每步事实 + 终局视图（按请求方视角渲染）。"""
        out = {"steps": steps,
               "ascii": render_ascii(final, perspective=color),
               "pieces": piece_listing(final),
               "fen": final.to_fen(),
               "turn": final.turn,
               "sandbox": self._sandbox_status(color),
               "time_status": self.time_status(color)}
        fact = self._terminal_fact(final)
        if fact:
            out["terminal"] = fact
        return out

    def probe(self, color, moves):
        """无状态推演：从当前正式局面提交一整条变化线，一次返回。"""
        with self.lock:
            self._require_sandbox(color)
            moves = [m.strip() for m in moves if m and m.strip()]
            if not moves:
                raise RuleError("probe 需要至少一步走法")
            self._charge(color, len(moves))
            steps, final, _ = self._run_line(self.board, moves)
            self.sim_remaining[color] += len(moves) - len(steps)  # 提前中止的节点退回
            return self._line_view(color, steps, final)

    def _sim(self, color, sid):
        try:
            return self.sims[color][sid]
        except KeyError:
            raise RuleError(f"沙盒分支不存在或已随正式落子失效: {sid}")

    def _sim_view(self, color, sid, last=None, note=None):
        stack = self.sims[color][sid]
        b = stack[-1]
        out = {"sim_id": sid, "depth": len(stack) - 1,
               "turn": b.turn,
               "ascii": render_ascii(b, perspective=color),
               "pieces": piece_listing(b),
               "fen": b.to_fen(),
               "sandbox": self._sandbox_status(color),
               "time_status": self.time_status(color)}
        fact = self._terminal_fact(b)
        if fact:
            out["terminal"] = fact
        if last:
            out["last"] = last
        if note:
            out["note"] = note
        return out

    def sim_start(self, color):
        """从当前正式局面分叉出一个有状态沙盒分支（本身不占节点）。"""
        with self.lock:
            self._require_sandbox(color)
            if len(self.sims[color]) >= self.MAX_SIMS_PER_SIDE:
                raise RuleError(
                    f"沙盒分支最多同时 {self.MAX_SIMS_PER_SIDE} 个，先 sim end 一个再开新的")
            sid = "s" + secrets.token_urlsafe(6)
            self.sims[color][sid] = [self.board.copy()]
            return self._sim_view(
                color, sid,
                note=f"已从当前局面分支，轮到{'红' if self.board.turn == RED else '黑'}方假设走子")

    def sim_push(self, color, sid, iccs):
        with self.lock:
            self._require_sandbox(color)
            stack = self._sim(color, sid)
            self._charge(color, 1)
            steps, final, ok = self._run_line(stack[-1], [iccs])
            if not ok:
                raise RuleError(f"非法走法: {iccs} —— {steps[0]['reason']}"
                                "（配额已计 1 节点，分支未变）")
            stack.append(final)
            return self._sim_view(color, sid, last=steps[0])

    def sim_pop(self, color, sid):
        with self.lock:
            self._require_sandbox(color)
            stack = self._sim(color, sid)
            if len(stack) == 1:
                raise RuleError("已在分支起点，无法再撤回")
            stack.pop()
            return self._sim_view(color, sid,
                                  note=f"已撤回一步假设（当前深度 {len(stack) - 1}）")

    def sim_board(self, color, sid):
        with self.lock:
            self._require_sandbox(color)
            self._sim(color, sid)
            return self._sim_view(color, sid)

    def sim_end(self, color, sid):
        with self.lock:
            self._require_sandbox(color)
            self._sim(color, sid)
            del self.sims[color][sid]
            return {"ok": True, "ended": sid,
                    "sandbox": self._sandbox_status(color),
                    "time_status": self.time_status(color)}

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
    def _check_can_move(self, color):
        self._expire_if_timeout()
        if not self.started:
            opp = BLACK if color == RED else RED
            raise RuleError(f"对局尚未开始：等待 {self.names[opp]} 入座后计时才开始")
        if color != self.board.turn:
            raise TurnError(f"还没轮到{self.names[color]}走棋")

    def propose_move(self, color, iccs, note=None):
        with self.lock:
            self._check_can_move(color)
            reason = explain_illegal(self.board, color, iccs)
            if reason:
                raise RuleError(f"非法走法: {iccs} —— {reason}")
            nb = apply_move(self.board, iccs)
            (fr, fc), (tr, tc) = iccs_to_rc(iccs[:2]), iccs_to_rc(iccs[2:])
            opp = BLACK if color == RED else RED
            expires_in = max(0.0, min(60.0, self.step_seconds - self._elapsed()))
            self.pending = {
                "id": "p" + secrets.token_urlsafe(8),
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
                "ascii": render_ascii(nb, perspective=color),
                "pieces": piece_listing(nb),
                "time_status": self.time_status(color),
            }

    def _settle_commit(self):
        """真正把 pending 落子：扣本步耗时、加秒、记谱、广播、终局判定。返回 history entry。"""
        elapsed = self._elapsed()
        color = self.pending["color"]
        iccs = self.pending["move"]
        captured = self.board.grid[iccs_to_rc(iccs[2:])[0]][iccs_to_rc(iccs[2:])[1]]
        board_before = self.board
        entry = {
            "ply": len(self.history) + 1,
            "color": color,
            "iccs": iccs,
            "chinese": to_chinese(self.board, iccs),
            "captured": captured,
            "elapsed": round(elapsed, 2),
        }
        self.board = self.pending["board_after"]
        self.pending = None
        self.remaining[color] = self.remaining[color] - elapsed + self.increment
        self.turn_started = time.monotonic()
        self.draw_offered_by = None
        self._reset_sandbox()
        entry["fen"] = self.board.to_fen()
        if self.assist:                # 走后自检（辅助模式）：对比走前/走后攻击图
            entry["self_check"] = self_check(board_before, self.board, color,
                                             iccs[:2], iccs[2:])
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
            entry = self._settle_commit()
            return entry | {"time_status": self.time_status(color),
                            "ascii": render_ascii(self.board, perspective=color),
                            "pieces": piece_listing(self.board)}

    def play(self, color, iccs, expect=None, note=None):
        """原子走子：校验 + 可选 expect 核对后直接提交，不经过挂单。"""
        with self.lock:
            self._check_can_move(color)
            reason = explain_illegal(self.board, color, iccs)
            if reason:
                raise RuleError(f"非法走法: {iccs} —— {reason}")
            chinese = to_chinese(self.board, iccs)
            if expect and expect != chinese:
                raise ExpectMismatch(
                    f"预期着法不符：你预期的是 {expect}，实际将是 {chinese}"
                    "——未落子，请修正后重试", actual=chinese)
            nb = apply_move(self.board, iccs)
            if note:
                self._append_note(color, note)
            self.pending = {
                "id": "p" + secrets.token_urlsafe(8),
                "color": color,
                "move": iccs,
                "board_after": nb,
                "expires": time.monotonic() + 60.0,
            }
            entry = self._settle_commit()
            return entry | {"time_status": self.time_status(color),
                            "ascii": render_ascii(self.board, perspective=color),
                            "pieces": piece_listing(self.board)}

    def cancel(self, color, pending_id):
        with self.lock:
            if not self.pending or self.pending["id"] != pending_id:
                raise RuleError("没有匹配的待确认走法")
            if self.pending["color"] != color:
                raise TurnError("不能取消对方的走法")
            self.pending = None

    def moves_detail(self, color):
        """轮到 color 时给出结构化合法走法（中文/吃子/将军）；否则空列表。"""
        with self.lock:
            self.settle_timeout()
            if self.result or not self.started or self.board.turn != color:
                return []
            out = []
            for iccs in legal_moves(self.board, color):
                (fr, fc), (tr, tc) = iccs_to_rc(iccs[:2]), iccs_to_rc(iccs[2:])
                piece = self.board.grid[fr][fc]
                nb = apply_move(self.board, iccs)
                opp = BLACK if color == RED else RED
                out.append({
                    "iccs": iccs,
                    "chinese": to_chinese(self.board, iccs),
                    "capture": self.board.grid[tr][tc] is not None,
                    "gives_check": in_check(nb, opp),
                    "piece": piece,
                })
            out.sort(key=lambda m: (m["piece"].upper(), m["iccs"][:2]))
            return out

    # ---- 辅助模式：落点攻防清点 ----
    def inspect_square(self, square):
        with self.lock:
            if not self.assist:
                raise RuleError("本局未开启辅助模式（创建对局时加 --assist）")
            if (len(square) != 2 or square[0] not in "abcdefghi"
                    or square[1] not in "0123456789"):
                raise RuleError(f"格子坐标格式应为列+行（如 e4）: {square!r}")
            return square_report(self.board, square)

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

    def abort(self):
        """控制台强制终止：不计胜负，记为和棋 + aborted。"""
        with self.lock:
            if self.result:
                raise GameOver("对局已结束")
            self._finish("draw", "aborted")

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
            out = {
                "game_id": self.id,
                "your_color": color,
                "assist": self.assist,
                "names": dict(self.names),
                "turn": self.board.turn,
                "started": self.started,
                "joined": dict(self.joined),
                "your_turn": self.started and self.board.turn == color and self.result is None,
                "fen": self.board.to_fen(),
                "ascii": render_ascii(self.board, perspective=color),
                "pieces": piece_listing(self.board),
                "ply": len(self.history),
                "result": self.result,
                "result_reason": self.result_reason,
                "in_check": self.result is None and in_check(self.board, color),
                "step_elapsed": (None if self.result or not self.started
                                 else round(self._elapsed(), 1)),
                "last_move": (None if not self.history else {
                    k: self.history[-1][k] for k in ("color", "iccs", "chinese", "elapsed")}
                    | {"captured_glyph": (GLYPHS[self.history[-1]["captured"]]
                                          if self.history[-1].get("captured") else None)}),
                "time_status": self.time_status(color),
                "clocks": self.clocks(),
                "draw_offered": self.draw_offered_by is not None and self.draw_offered_by != color,
            }
            if self.sandbox_budget:
                out["sandbox"] = self._sandbox_status(color)
            return out
