# Xiangqi Arena 计划 1：规则核心 + 对局服务 + CLI 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付一个纯命令行可用的中国象棋对弈系统：自研规则核心（FEN/走法/将军/记谱/文本棋盘）+ FastAPI 裁判服务（座位令牌、两阶段提交、双钟计时、和棋/认输、思考隔离）+ `xq` CLI + Agent 提示词模板，使两个本地 Agent Harness 能合规打完一整局。

**Architecture:** 单仓库 Python 包，三层结构：`xiangqi`（纯规则核心，零依赖）、`xqserver`（FastAPI 裁判，持有全部对局状态，是唯一真相源）、`xqcli`（typer CLI，HTTP 客户端薄封装）。Agent 只能拿到公开棋局，思考 note 只进棋谱文件与观战 SSE，无任何接口回传给对手。

**Tech Stack:** Python 3.11+ / FastAPI / uvicorn / typer / httpx / pytest。不引入数据库（JSONL 棋谱 + 内存对局注册表）。

**Spec:** `docs/superpowers/specs/2026-10-08-xiangqi-arena-design.md`

## Global Constraints

- 内部坐标一律 `(row, col)`：row 0 = 黑方底线（FEN 第一行），row 9 = 红方底线；col 0 = ICCS `a` 路。
- ICCS 坐标：file = `a`–`i`（红方视角从左到右），rank = `0`–`9`（0 = 红方底线）。映射：`row = 9 - rank`，`col = ord(file) - ord('a')`。
- 棋子字符：红方大写 `R N B A K C P`（车马相仕帅炮兵），黑方小写 `r n b a k c p`（车马象士将炮卒）。
- 初始 FEN：`rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR w - - 0 1`。
- FEN 的 turn 字段用 `"w"/"b"`（FEN 惯例，w = 红先行）；内部 `board.turn` 用 `"r"/"b"`；转换只在 from_fen/to_fen 边界发生。
- 默认时限：局时 900s、步时 180s、加秒 10s、自然限着 120 半回合（60 回合），建局时均可配置。
- 自然限着计数只在吃子时清零（兵走子不清零——象棋自然限着只数吃子）。
- pending 预览有效期：min(60s, 剩余步时)，过期自动作废。
- seat 文件权限 600，服务器数据目录权限 700。
- 服务端错误返回 JSON `{"error": "..."}`：400 非法输入 / 403 token 无效 / 409 状态冲突（未轮到你、已超时、对局已结束）。

## Review Focus

1. **畸形 ICCS 输入**（`""`、`h2`、`z9z9`、`h2h2`）：必须 400 拒绝且不改变棋局 —— 测试在 Task 7（`test_malformed_move_400`）。
2. **非轮次方 propose / 替对方 commit**：必须 409 —— 测试在 Task 6（`test_turn_enforcement`）与 Task 7（`test_wrong_turn_409`）。
3. **时钟边界**：剩余步时耗尽瞬间的操作判超时负；propose/commit 入口先结算超时 —— 测试在 Task 6（`test_step_timeout_loss`、`test_base_time_exhaustion`）。
4. **Agent 侧信息泄漏**：`state` 响应不含 notes，且无其他对 token 开放的 notes 接口 —— 测试在 Task 6（`test_state_hides_notes`）、Task 7（`test_notes_not_in_state`）、Task 9（e2e 泄漏断言）。
5. **SSE 断线重连**：观战端连接后先收 `snapshot` 事件（当前局面 + 历史 + notes）再收增量 —— 测试在 Task 7（`test_spectate_snapshot`）。

---

### Task 1: 项目脚手架 + Board/FEN

**Files:**
- Create: `pyproject.toml`
- Create: `src/xiangqi/__init__.py`（空文件）
- Create: `src/xiangqi/board.py`
- Test: `tests/test_board.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `Board(grid, turn="r", halfmove=0, fullmove=1)`，`Board.initial()`，`Board.from_fen(fen: str) -> Board`，`board.to_fen() -> str`，`board.piece_at(row, col) -> str | None`，`board.copy() -> Board`
  - `iccs_to_rc(s: str) -> tuple[int, int]`，`rc_to_iccs(row, col) -> str`，`side_of(piece) -> str`
  - 常量 `RED="r"`、`BLACK="b"`、`INITIAL_FEN`、`FILES="abcdefghi"`

- [ ] **Step 1: 写脚手架与失败测试**

`pyproject.toml`：

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "xiangqi-arena"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["fastapi>=0.110", "uvicorn>=0.29", "typer>=0.12", "httpx>=0.27"]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-asyncio>=0.23"]

[project.scripts]
xq = "xqcli.main:app"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
```

`tests/test_board.py`：

```python
from xiangqi.board import Board, INITIAL_FEN, iccs_to_rc, rc_to_iccs


def test_initial_fen_roundtrip():
    b = Board.initial()
    assert b.to_fen() == INITIAL_FEN
    assert b.turn == "r"
    assert b.halfmove == 0 and b.fullmove == 1


def test_piece_placement():
    b = Board.initial()
    assert b.piece_at(0, 0) == "r"   # 黑车
    assert b.piece_at(0, 4) == "k"   # 黑将
    assert b.piece_at(9, 4) == "K"   # 红帅
    assert b.piece_at(7, 1) == "C"   # 红炮
    assert b.piece_at(6, 0) == "P"   # 红兵
    assert b.piece_at(4, 0) is None


def test_iccs_conversion():
    assert iccs_to_rc("h2") == (7, 7)
    assert iccs_to_rc("e2") == (7, 4)
    assert rc_to_iccs(7, 7) == "h2"
    assert rc_to_iccs(0, 0) == "a9"


def test_iccs_roundtrip_all():
    for r in range(10):
        for c in range(9):
            assert iccs_to_rc(rc_to_iccs(r, c)) == (r, c)
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /Users/wushuyue/Documents/自学/Coding/xiangqi-arena && python3.11 -m venv .venv && .venv/bin/pip install -e '.[dev]' && .venv/bin/pytest tests/test_board.py -v`
（若 `python3.11` 不存在，用 `python3`，前提是 `python3 --version` ≥ 3.11）
Expected: FAIL，ModuleNotFoundError: xiangqi

- [ ] **Step 3: 实现 `src/xiangqi/board.py`**

```python
"""棋盘与 FEN。内部坐标 (row, col)：row 0 = 黑方底线，col 0 = a 路。"""

FILES = "abcdefghi"
RED, BLACK = "r", "b"
INITIAL_FEN = "rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR w - - 0 1"


def iccs_to_rc(s: str) -> tuple[int, int]:
    return (9 - int(s[1]), FILES.index(s[0]))


def rc_to_iccs(row: int, col: int) -> str:
    return FILES[col] + str(9 - row)


def side_of(piece: str) -> str:
    return RED if piece.isupper() else BLACK


class Board:
    def __init__(self, grid, turn=RED, halfmove=0, fullmove=1):
        self.grid = grid            # list[list[str | None]]，10 行 9 列
        self.turn = turn            # "r" 或 "b"
        self.halfmove = halfmove    # 距上次吃子的半回合数（自然限着用）
        self.fullmove = fullmove

    @classmethod
    def initial(cls) -> "Board":
        return cls.from_fen(INITIAL_FEN)

    @classmethod
    def from_fen(cls, fen: str) -> "Board":
        parts = fen.split()
        rows = parts[0].split("/")
        assert len(rows) == 10, f"FEN 应有 10 行: {fen}"
        grid = []
        for row in rows:
            cells = []
            for ch in row:
                if ch.isdigit():
                    cells += [None] * int(ch)
                else:
                    cells.append(ch)
            assert len(cells) == 9, f"FEN 行应有 9 格: {row}"
            grid.append(cells)
        turn = RED if parts[1] == "w" else BLACK
        return cls(grid, turn, int(parts[4]), int(parts[5]))

    def to_fen(self) -> str:
        rows = []
        for r in self.grid:
            out, empty = "", 0
            for cell in r:
                if cell is None:
                    empty += 1
                else:
                    if empty:
                        out += str(empty)
                        empty = 0
                    out += cell
            if empty:
                out += str(empty)
            rows.append(out)
        turn = "w" if self.turn == RED else "b"
        return f"{'/'.join(rows)} {turn} - - {self.halfmove} {self.fullmove}"

    def piece_at(self, row: int, col: int) -> str | None:
        return self.grid[row][col]

    def copy(self) -> "Board":
        return Board([row[:] for row in self.grid], self.turn, self.halfmove, self.fullmove)
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/pytest tests/test_board.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/xiangqi tests/test_board.py
git commit -m "feat(core): Board 与 FEN 读写、ICCS 坐标转换"
```

---

### Task 2: 伪合法走法生成（七种棋子）

**Files:**
- Create: `src/xiangqi/moves.py`
- Test: `tests/test_moves.py`

**Interfaces:**
- Consumes: `Board`, `RED`, `BLACK`（Task 1）
- Produces: `pseudo_destinations(board: Board, row: int, col: int) -> list[tuple[int, int]]`——只按棋子走法生成目标点（不剔除送将，那是 Task 3 的职责）。

- [ ] **Step 1: 写失败测试**

`tests/test_moves.py`：

```python
from xiangqi.board import Board, iccs_to_rc
from xiangqi.moves import pseudo_destinations


def dests(board, iccs):
    r, c = iccs_to_rc(iccs)
    return set(pseudo_destinations(board, r, c))


def test_rook_blocked_by_own_pawn():
    b = Board.initial()
    d = dests(b, "a0")           # 红车 (9,0)
    assert (8, 0) in d and (7, 0) in d
    assert (6, 0) not in d       # 己方兵 a3 挡路
    assert (9, 1) not in d       # b0 是己方马


def test_rook_capture():
    b = Board.from_fen("k8/9/9/9/9/9/9/p8/9/R3K4 w - - 0 1")
    d = dests(b, "a0")           # 红车 (9,0)，黑卒在 (7,0)
    assert (8, 0) in d
    assert (7, 0) in d           # 可吃卒
    assert (6, 0) not in d       # 不能越过


def test_horse_initial():
    b = Board.initial()
    d = dests(b, "b0")           # 红马 (9,1)
    assert (7, 0) in d and (7, 2) in d
    assert (8, 3) not in d       # 马腿 (9,2) 被己方相占住


def test_horse_leg_blocked():
    b = Board.from_fen("k8/9/9/9/9/9/9/9/1p6/1N2K4 w - - 0 1")
    d = dests(b, "b0")           # 红马 (9,1)，马腿 (8,1) 被黑卒蹩住
    assert (7, 0) not in d and (7, 2) not in d
    assert (8, 3) in d           # 侧向马腿 (9,2) 空，不受影响


def test_cannon_screen():
    b = Board.from_fen("k8/9/9/9/4p4/9/9/4p4/9/3KC4 w - - 0 1")
    d = dests(b, "e0")           # 红炮 (9,4)，(7,4) 黑卒是炮架，(4,4) 黑卒是目标
    assert (8, 4) in d           # 空格平走
    assert (7, 4) not in d       # 炮架本身不能吃
    assert (4, 4) in d           # 隔一子吃
    assert (0, 4) not in d       # 不能隔两子


def test_advisor_palace():
    b = Board.initial()
    d = dests(b, "d0")           # 红仕 (9,3)
    assert d == {(8, 4)}         # 底排仕只有九宫中心一格


def test_elephant_initial():
    b = Board.initial()
    d = dests(b, "c0")           # 红相 (9,2)，象眼 (8,1)/(8,3) 初始为空
    assert (7, 0) in d and (7, 4) in d


def test_elephant_cannot_cross_river():
    b = Board.from_fen("k8/9/9/9/9/2B6/9/9/9/4K4 w - - 0 1")
    d = dests(b, "c4")           # 红相在河边 (5,2)
    assert (3, 0) not in d and (3, 4) not in d   # 过河禁止
    assert (7, 0) in d and (7, 4) in d


def test_king_palace():
    b = Board.initial()
    d = dests(b, "e0")           # 红帅 (9,4)，横竖被己方子堵住
    assert d == {(8, 4)}


def test_pawn_before_and_after_river():
    b = Board.from_fen("4k4/9/9/9/9/9/9/9/4P4/4K4 w - - 0 1")
    d = dests(b, "e1")           # 红兵 (8,4) 未过河
    assert d == {(7, 4)}         # 只能向前
    b2 = Board.from_fen("4k4/9/9/9/9/4P4/9/9/9/4K4 w - - 0 1")
    d2 = dests(b2, "e5")         # 红兵 (4,4) 已过河
    assert (3, 4) in d2 and (4, 3) in d2 and (4, 5) in d2
    assert (5, 4) not in d2      # 兵不后退


def test_black_pawn_direction():
    b = Board.from_fen("k6p1/9/9/9/9/9/9/9/9/4K4 b - - 0 1")
    d = dests(b, "h9")           # 黑卒 (0,7) 未过河，向前 = row 增大
    assert d == {(1, 7)}
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/pytest tests/test_moves.py -v`
Expected: FAIL，ModuleNotFoundError: xiangqi.moves

- [ ] **Step 3: 实现 `src/xiangqi/moves.py`**

```python
"""各棋子的伪合法走法（只按走法规则，不过滤送将）。"""

from xiangqi.board import Board, RED, BLACK

ORTH = ((1, 0), (-1, 0), (0, 1), (0, -1))


def _in(r, c):
    return 0 <= r < 10 and 0 <= c < 9


def _palace_rows(side):
    return (7, 8, 9) if side == RED else (0, 1, 2)


def pseudo_destinations(board: Board, row: int, col: int) -> list[tuple[int, int]]:
    p = board.grid[row][col]
    if p is None:
        return []
    red = p.isupper()
    side = RED if red else BLACK
    t = p.upper()
    dests: list[tuple[int, int]] = []

    def add(r, c):
        if _in(r, c):
            q = board.grid[r][c]
            if q is None or q.isupper() != red:
                dests.append((r, c))

    if t in ("R", "C"):
        for dr, dc in ORTH:
            r, c = row + dr, col + dc
            jumped = False
            while _in(r, c):
                q = board.grid[r][c]
                if t == "R":
                    if q is None:
                        dests.append((r, c))
                    else:
                        if q.isupper() != red:
                            dests.append((r, c))
                        break
                else:  # 炮：不吃子不可翻山；吃子须恰好隔一子
                    if not jumped:
                        if q is None:
                            dests.append((r, c))
                        else:
                            jumped = True
                    elif q is not None:
                        if q.isupper() != red:
                            dests.append((r, c))
                        break
                r += dr
                c += dc
    elif t == "N":
        for dr, dc, lr, lc in (
            (-2, -1, -1, 0), (-2, 1, -1, 0), (2, -1, 1, 0), (2, 1, 1, 0),
            (-1, -2, 0, -1), (1, -2, 0, -1), (-1, 2, 0, 1), (1, 2, 0, 1),
        ):
            if _in(row + dr, col + dc) and board.grid[row + lr][col + lc] is None:
                add(row + dr, col + dc)
    elif t == "B":
        for dr, dc in ((-2, -2), (-2, 2), (2, -2), (2, 2)):
            r, c = row + dr, col + dc
            if not _in(r, c):
                continue
            if side == RED and r < 5:
                continue            # 相不过河
            if side == BLACK and r > 4:
                continue
            if board.grid[row + dr // 2][col + dc // 2] is None:
                add(r, c)
    elif t == "A":
        rows = _palace_rows(side)
        for dr, dc in ((-1, -1), (-1, 1), (1, -1), (1, 1)):
            r, c = row + dr, col + dc
            if r in rows and 3 <= c <= 5:
                add(r, c)
    elif t == "K":
        rows = _palace_rows(side)
        for dr, dc in ORTH:
            r, c = row + dr, col + dc
            if r in rows and 3 <= c <= 5:
                add(r, c)
    elif t == "P":
        fwd = -1 if side == RED else 1
        add(row + fwd, col)
        crossed = row <= 4 if side == RED else row >= 5
        if crossed:
            add(row, col - 1)
            add(row, col + 1)
    return dests
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/pytest tests/test_moves.py -v`
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add src/xiangqi/moves.py tests/test_moves.py
git commit -m "feat(core): 七种棋子伪合法走法（含蹩马腿/塞象眼/九宫/过河/炮架）"
```

---

### Task 3: 将军判定、合法走法过滤、终局判定

**Files:**
- Create: `src/xiangqi/rules.py`
- Test: `tests/test_rules.py`

**Interfaces:**
- Consumes: `Board`, `pseudo_destinations`, `iccs_to_rc`, `rc_to_iccs`（Task 1–2）
- Produces:
  - `class IllegalMove(Exception)`
  - `find_general(board, side) -> tuple[int, int]`，`generals_face(board) -> bool`，`in_check(board, side) -> bool`
  - `legal_moves(board, side) -> list[str]`（ICCS 列表）
  - `apply_move(board, iccs) -> Board`（非法抛 `IllegalMove`；吃子 halfmove 清零否则 +1；黑方走后 fullmove+1；turn 翻转）
  - `game_result(board, draw_halfmoves=120) -> str | None`（`"red_win"` / `"black_win"` / `"draw"` / `None`；将死与困毙都判负）

- [ ] **Step 1: 写失败测试**

`tests/test_rules.py`：

```python
import pytest
from xiangqi.board import Board
from xiangqi.rules import (
    IllegalMove, apply_move, game_result, generals_face, in_check, legal_moves,
)


def test_generals_face():
    b = Board.from_fen("4k4/9/9/9/9/9/9/9/9/4K4 w - - 0 1")
    assert generals_face(b)
    b2 = Board.from_fen("4k4/9/9/9/9/4P4/9/9/9/4K4 w - - 0 1")
    assert not generals_face(b2)     # 中间有兵隔开


def test_pinned_by_flying_general():
    # 红兵在 e 线挡着帅将照面；兵横走离开 e 线即白脸将 → 非法
    b = Board.from_fen("4k4/9/9/9/4P4/9/9/9/9/4K4 w - - 0 1")
    moves = legal_moves(b, "r")
    assert "e5d5" not in moves and "e5f5" not in moves
    assert "e5e6" in moves           # 前进仍挡在 e 线，合法
    assert "e0d0" in moves           # 帅横走离开 e 线，合法


def test_in_check_by_rook():
    b = Board.from_fen("4k4/9/9/9/9/9/9/9/4r4/4K4 b - - 0 1")
    assert in_check(b, "r")          # 黑车沉底将军
    assert not in_check(b, "b")


def test_must_resolve_check():
    # 黑车 e1 将军：红帅只能横走；e0e1 吃车会导致白脸将，非法
    b = Board.from_fen("4k4/9/9/9/9/9/9/9/4r4/4K4 w - - 0 1")
    moves = legal_moves(b, "r")
    assert moves == ["e0d0", "e0f0"] or set(moves) == {"e0d0", "e0f0"}
    assert "e0e1" not in moves


def test_checkmate():
    # 车 a9 锁第 9 排，车 e7 锁 e 路并在将军：黑将无处可逃
    b = Board.from_fen("r3k4/9/4r3/9/9/9/9/9/9/3K5 b - - 0 1")
    assert in_check(b, "b")
    assert legal_moves(b, "b") == []
    assert game_result(b) == "red_win"


def test_stalemate_is_loss():
    # 困毙：黑将未被将军，但 d9/f9 被车 d8 封、e8 被车 f8 所在排封，无路可走
    b = Board.from_fen("4k4/3r1r3/9/9/9/9/9/9/9/3K5 b - - 0 1")
    assert not in_check(b, "b")
    assert legal_moves(b, "b") == []
    assert game_result(b) == "red_win"


def test_apply_move_updates_clocks_and_turn():
    b = Board.initial()
    nb = apply_move(b, "h2e2")           # 炮二平五，未吃子
    assert nb.turn == "b" and nb.halfmove == 1 and nb.fullmove == 1
    nb2 = apply_move(nb, "b9c7")         # 黑马 2 进 3
    assert nb2.turn == "r" and nb2.halfmove == 2 and nb2.fullmove == 2


def test_capture_resets_halfmove():
    b = Board.from_fen("k8/9/9/9/p8/9/9/9/9/R3K4 w - - 5 1")
    nb = apply_move(b, "a0a5")           # 红车直上吃黑卒
    assert nb.halfmove == 0


def test_illegal_move_raises():
    b = Board.initial()
    with pytest.raises(IllegalMove):
        apply_move(b, "h2e3")            # 炮斜走非法
    with pytest.raises(IllegalMove):
        apply_move(b, "b9c7")            # 轮红走，黑子不能动


def test_draw_by_halfmove_clock():
    b = Board.from_fen("4k4/9/9/9/9/9/9/9/9/9/4K4 w - - 120 61")
    assert game_result(b) == "draw"
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/pytest tests/test_rules.py -v`
Expected: FAIL，ModuleNotFoundError

- [ ] **Step 3: 实现 `src/xiangqi/rules.py`**

```python
"""将军判定、合法走法过滤（剔除送将/白脸将）、终局判定。"""

from xiangqi.board import Board, RED, BLACK, iccs_to_rc, rc_to_iccs
from xiangqi.moves import pseudo_destinations


class IllegalMove(Exception):
    pass


def find_general(board: Board, side: str) -> tuple[int, int]:
    k = "K" if side == RED else "k"
    for r in range(10):
        for c in range(9):
            if board.grid[r][c] == k:
                return (r, c)
    raise ValueError(f"棋盘上找不到{'红帅' if side == RED else '黑将'}")


def generals_face(board: Board) -> bool:
    (rr, rc_), (br, bc_) = find_general(board, RED), find_general(board, BLACK)
    if rc_ != bc_:
        return False
    return all(board.grid[r][rc_] is None for r in range(br + 1, rr))


def in_check(board: Board, side: str) -> bool:
    gr, gc = find_general(board, side)
    opp_red = side != RED
    for r in range(10):
        for c in range(9):
            p = board.grid[r][c]
            if p is not None and p.isupper() == opp_red:
                if (gr, gc) in pseudo_destinations(board, r, c):
                    return True
    return False


def legal_moves(board: Board, side: str) -> list[str]:
    want_red = side == RED
    res = []
    for r in range(10):
        for c in range(9):
            p = board.grid[r][c]
            if p is None or p.isupper() != want_red:
                continue
            for (tr, tc) in pseudo_destinations(board, r, c):
                nb = board.copy()
                nb.grid[tr][tc] = p
                nb.grid[r][c] = None
                if generals_face(nb) or in_check(nb, side):
                    continue
                res.append(rc_to_iccs(r, c) + rc_to_iccs(tr, tc))
    return res


def apply_move(board: Board, iccs: str) -> Board:
    if iccs not in legal_moves(board, board.turn):
        raise IllegalMove(iccs)
    (fr, fc), (tr, tc) = iccs_to_rc(iccs[:2]), iccs_to_rc(iccs[2:])
    nb = board.copy()
    captured = nb.grid[tr][tc] is not None
    nb.grid[tr][tc] = nb.grid[fr][fc]
    nb.grid[fr][fc] = None
    nb.halfmove = 0 if captured else nb.halfmove + 1
    if board.turn == BLACK:
        nb.fullmove += 1
    nb.turn = BLACK if board.turn == RED else RED
    return nb


def game_result(board: Board, draw_halfmoves: int = 120) -> str | None:
    """将死与困毙均判负；halfmove 达到自然限着判和。"""
    if board.halfmove >= draw_halfmoves:
        return "draw"
    if not legal_moves(board, board.turn):
        return "black_win" if board.turn == RED else "red_win"
    return None
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/pytest tests/test_rules.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add src/xiangqi/rules.py tests/test_rules.py
git commit -m "feat(core): 将军/将死/困毙/白脸将判定与合法走法过滤"
```

---

### Task 4: ICCS 校验 + 中文记谱

**Files:**
- Create: `src/xiangqi/notation.py`
- Test: `tests/test_notation.py`

**Interfaces:**
- Consumes: `Board`, `RED`, `iccs_to_rc`（Task 1）
- Produces:
  - `is_valid_iccs(s) -> bool`（4 字符、file∈a–i、rank∈0–9、起终不同）
  - `to_chinese(board: Board, iccs: str) -> str`（如 `"炮二平五"`、`"马八进七"`、`"前炮进一"`）

记谱规则（本实现采用的标准子集）：
- 红方路数：从红方视角右侧起一至九，即 col c 的路名 = `"九八七六五四三二一"[c]`；黑方路数：从黑方视角右侧起 1–9，即 col c 的路名 = `str(c + 1)`。
- 同一路恰好两个同名同方子时，前缀用 `前`/`后`（离对方底线近者为前）；否则前缀为 `子名 + 所在路名`。
- 动作：同排为 `平 + 目标路名`；朝对方方向为 `进`、反之为 `退`。直线子（车马炮帅兵）进/退的参数是步数（红方用中文数字、黑方用阿拉伯数字）；斜线子（马相仕）的参数是目标路名。

- [ ] **Step 1: 写失败测试**

`tests/test_notation.py`：

```python
from xiangqi.board import Board
from xiangqi.notation import is_valid_iccs, to_chinese


def test_is_valid_iccs():
    assert is_valid_iccs("h2e2")
    assert not is_valid_iccs("")
    assert not is_valid_iccs("h2")
    assert not is_valid_iccs("z9z9")
    assert not is_valid_iccs("h2h2")
    assert not is_valid_iccs("h10e2")


def test_opening_cannon():
    b = Board.initial()
    assert to_chinese(b, "h2e2") == "炮二平五"


def test_opening_horse():
    b = Board.initial()
    assert to_chinese(b, "b0c2") == "马八进七"
    assert to_chinese(b, "b0a2") == "马八进九"


def test_black_notation():
    b = Board.initial()
    assert to_chinese(b, "b9c7") == "马2进3"   # 黑方路数从黑方右侧起算
    assert to_chinese(b, "h9g7") == "马8进7"


def test_pawn_advance():
    b = Board.initial()
    assert to_chinese(b, "a3a4") == "兵九进一"   # 红兵 (6,0) 前进一步


def test_cannon_steps():
    b = Board.from_fen("4k4/9/9/9/9/9/9/4C4/9/4K4 w - - 0 1")
    assert to_chinese(b, "e1e4") == "炮五进三"   # (8,4) → (5,4)，三步


def test_front_back_disambiguation():
    # 红双炮同在 e 路：(4,4) 为前炮、(9,4) 为后炮
    b = Board.from_fen("4k4/9/9/9/4C4/9/9/9/9/4KC3 w - - 0 1")
    assert to_chinese(b, "e5e6") == "前炮进一"   # (4,4) → (3,4)
    assert to_chinese(b, "e0f0") == "后炮平四"   # (9,4) → (9,5)
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/pytest tests/test_notation.py -v`
Expected: FAIL，ModuleNotFoundError

- [ ] **Step 3: 实现 `src/xiangqi/notation.py`**

```python
"""ICCS 校验与中文记谱生成。"""

from xiangqi.board import Board, RED, iccs_to_rc

RED_NAMES = {"K": "帅", "A": "仕", "B": "相", "N": "马", "R": "车", "C": "炮", "P": "兵"}
BLACK_NAMES = {"k": "将", "a": "士", "b": "象", "n": "马", "r": "车", "c": "炮", "p": "卒"}
RED_FILE_LABELS = "九八七六五四三二一"     # col 0..8 → 红方路名
CN_NUM = "一二三四五六七八九"               # 步数（红方用）
FILES = "abcdefghi"


def is_valid_iccs(s) -> bool:
    return (
        isinstance(s, str)
        and len(s) == 4
        and s[0] in FILES and s[2] in FILES
        and s[1] in "0123456789" and s[3] in "0123456789"
        and s[:2] != s[2:]
    )


def _file_label(side: str, col: int) -> str:
    return RED_FILE_LABELS[col] if side == RED else str(col + 1)


def to_chinese(board: Board, iccs: str) -> str:
    (fr, fc), (tr, tc) = iccs_to_rc(iccs[:2]), iccs_to_rc(iccs[2:])
    p = board.grid[fr][fc]
    red = p.isupper()
    side = RED if red else "b"
    name = RED_NAMES[p] if red else BLACK_NAMES[p]

    same_file = [r for r in range(10) if board.grid[r][fc] == p]
    if len(same_file) == 2:
        front = min(same_file) if red else max(same_file)  # row 小 = 离对方底线近 = 前（红方视角）
        prefix = ("前" if fr == front else "后") + name
    else:
        prefix = name + _file_label(side, fc)

    if tr == fr:
        return prefix + "平" + _file_label(side, tc)
    forward = (tr < fr) if red else (tr > fr)
    action = "进" if forward else "退"
    if p.upper() in ("R", "K", "C", "P"):
        steps = abs(tr - fr)
        param = CN_NUM[steps - 1] if red else str(steps)
        return prefix + action + param
    return prefix + action + _file_label(side, tc)
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/pytest tests/test_notation.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/xiangqi/notation.py tests/test_notation.py
git commit -m "feat(core): ICCS 校验与中文记谱（含前/后歧义）"
```

---

### Task 5: ASCII 文本棋盘

**Files:**
- Create: `src/xiangqi/textboard.py`
- Test: `tests/test_textboard.py`

**Interfaces:**
- Consumes: `Board`
- Produces: `render_ascii(board) -> str`：列头 `a`–`i`，行首标 ICCS rank（9→0）；红方用 `车马相仕帅炮兵`，黑方用 `車馬象士將砲卒`（繁体区分颜色），空位 `＋`。

- [ ] **Step 1: 写失败测试**

`tests/test_textboard.py`：

```python
from xiangqi.board import Board
from xiangqi.rules import apply_move
from xiangqi.textboard import render_ascii


def test_render_initial():
    s = render_ascii(Board.initial())
    lines = s.splitlines()
    assert lines[0].strip().startswith("a")
    assert lines[1].startswith("9") and "車" in lines[1] and "將" in lines[1]
    assert lines[-1].startswith("0") and "帅" in lines[-1]
    assert "炮" in s and "砲" in s   # 双色炮都在


def test_render_move():
    nb = apply_move(Board.initial(), "h2e2")
    rank2 = [l for l in render_ascii(nb).splitlines() if l.startswith("2")][0]
    assert rank2.count("炮") == 2    # 两门红炮都还在第 2 排
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/pytest tests/test_textboard.py -v`
Expected: FAIL，ModuleNotFoundError

- [ ] **Step 3: 实现 `src/xiangqi/textboard.py`**

```python
"""ASCII 棋盘渲染：列 a-i，行 9→0（ICCS rank），红简体黑繁体。"""

from xiangqi.board import Board

GLYPHS = {
    "R": "车", "N": "马", "B": "相", "A": "仕", "K": "帅", "C": "炮", "P": "兵",
    "r": "車", "n": "馬", "b": "象", "a": "士", "k": "將", "c": "砲", "p": "卒",
}


def render_ascii(board: Board) -> str:
    lines = ["  " + " ".join("abcdefghi")]
    for row in range(10):
        rank = 9 - row
        cells = [GLYPHS[p] if p else "＋" for p in board.grid[row]]
        lines.append(f"{rank} " + " ".join(cells))
    return "\n".join(lines)
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/pytest tests/test_textboard.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add src/xiangqi/textboard.py tests/test_textboard.py
git commit -m "feat(core): ASCII 文本棋盘渲染"
```

---

### Task 6: Game 状态机（纯 Python，无网络）

**Files:**
- Create: `src/xqserver/__init__.py`（空文件）
- Create: `src/xqserver/game.py`
- Test: `tests/test_game.py`

**Interfaces:**
- Consumes: `Board`, `apply_move`, `legal_moves`, `game_result`, `in_check`, `to_chinese`, `is_valid_iccs`, `render_ascii`
- Produces（Task 7 routes 与 Task 9 e2e 依赖这些签名）：
  - `Game(gid, red_name, black_name, base_seconds=900, step_seconds=180, increment=10, draw_halfmoves=120)`
  - 属性：`id, board, names, tokens, remaining, result, result_reason, pending, notes, history, lock`
  - `game.check_token(color, token) -> None`（不符抛 `AuthError`）
  - `game.propose_move(color, iccs, note=None) -> dict`（含 `pending_id, move, chinese, piece, captured, gives_check, fen_after, opp_replies, expires_in`）
  - `game.commit(color, pending_id) -> dict`（history 条目：`ply, color, iccs, chinese, elapsed, fen`）
  - `game.cancel(color, pending_id) -> None`
  - `game.add_note(color, text) -> None`
  - `game.resign(color) / game.offer_draw(color) / game.accept_draw(color)`
  - `game.state_for(color) -> dict`（**不含 notes**——防泄漏）
  - `game.clocks() -> dict`
  - `game.result` ∈ `None | "red_win" | "black_win" | "draw"`；`result_reason` ∈ `None | "checkmate" | "timeout" | "resign" | "draw_agreement" | "halfmove_limit"`
  - 异常：`AuthError`、`RuleError`、`TurnError(RuleError)`、`GameOver(RuleError)`

- [ ] **Step 1: 写失败测试**

`tests/test_game.py`：

```python
import pytest
from xqserver.game import AuthError, Game, GameOver, RuleError, TurnError


def make_game(**kw):
    return Game("g1", "Kimi", "Claude", **kw)


def test_tokens_and_auth():
    g = make_game()
    assert g.tokens["r"] != g.tokens["b"]
    g.check_token("r", g.tokens["r"])
    with pytest.raises(AuthError):
        g.check_token("r", g.tokens["b"])
    with pytest.raises(AuthError):
        g.check_token("r", None)


def test_two_phase_commit():
    g = make_game()
    pv = g.propose_move("r", "h2e2", note="架中炮")
    assert pv["chinese"] == "炮二平五"
    assert pv["captured"] is None
    assert pv["gives_check"] is False
    assert g.board.turn == "r"           # 预览不改变棋局
    entry = g.commit("r", pv["pending_id"])
    assert entry["iccs"] == "h2e2" and entry["chinese"] == "炮二平五"
    assert g.board.turn == "b"           # commit 后轮到黑方
    assert g.notes[0]["text"] == "架中炮" and g.notes[0]["color"] == "r"


def test_cancel_then_repropose():
    g = make_game()
    pv = g.propose_move("r", "h2e2")
    g.cancel("r", pv["pending_id"])
    with pytest.raises(RuleError):
        g.commit("r", pv["pending_id"])  # 已作废
    pv2 = g.propose_move("r", "b0c2")
    g.commit("r", pv2["pending_id"])
    assert g.board.turn == "b"


def test_turn_enforcement():
    g = make_game()
    with pytest.raises(TurnError):
        g.propose_move("b", "b9c7")      # 红方先走
    pv = g.propose_move("r", "h2e2")
    with pytest.raises(TurnError):
        g.commit("b", pv["pending_id"])  # 黑方不能替红方 commit
    with pytest.raises(TurnError):
        g.cancel("b", pv["pending_id"])


def test_illegal_and_malformed():
    g = make_game()
    with pytest.raises(RuleError):
        g.propose_move("r", "h2e3")      # 炮斜走
    with pytest.raises(RuleError):
        g.propose_move("r", "z9z9")      # 畸形 ICCS
    assert g.pending is None             # 失败不留下 pending


def test_no_commit_without_pending():
    g = make_game()
    with pytest.raises(RuleError):
        g.commit("r", "nonexistent")


def test_step_timeout_loss():
    g = make_game(step_seconds=0)        # 步时 0：下一步操作即超时
    with pytest.raises(GameOver):
        g.propose_move("r", "h2e2")
    assert g.result == "black_win" and g.result_reason == "timeout"


def test_base_time_exhaustion():
    g = make_game(base_seconds=-1)       # 局时已空
    with pytest.raises(GameOver):
        g.propose_move("r", "h2e2")
    assert g.result == "black_win"


def test_increment_added():
    g = make_game(increment=10)
    before = g.remaining["r"]
    pv = g.propose_move("r", "h2e2")
    g.commit("r", pv["pending_id"])
    assert g.remaining["r"] > before - 1  # 扣掉耗时但加回 10s


def test_resign_and_draw_agreement():
    g = make_game()
    g.resign("r")
    assert g.result == "black_win" and g.result_reason == "resign"
    with pytest.raises(GameOver):
        g.propose_move("b", "b9c7")

    g2 = make_game()
    g2.offer_draw("r")
    g2.accept_draw("b")
    assert g2.result == "draw" and g2.result_reason == "draw_agreement"

    g3 = make_game()
    with pytest.raises(RuleError):
        g3.accept_draw("b")              # 没有提议可接受


def test_halfmove_draw():
    g = make_game(draw_halfmoves=2)
    pv = g.propose_move("r", "h2e2"); g.commit("r", pv["pending_id"])
    pv = g.propose_move("b", "b9c7"); g.commit("b", pv["pending_id"])
    assert g.result == "draw" and g.result_reason == "halfmove_limit"


def test_state_hides_notes():
    g = make_game()
    g.add_note("r", "秘密计划")
    st = g.state_for("b")
    assert "notes" not in st and "秘密计划" not in str(st)
    st_r = g.state_for("r")
    assert "notes" not in st_r           # 自己的 note 也不经 state 回传
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/pytest tests/test_game.py -v`
Expected: FAIL，ModuleNotFoundError

- [ ] **Step 3: 实现 `src/xqserver/game.py`**

```python
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
            if color != self.board.turn:
                raise TurnError(f"还没轮到{self.names[color]}走棋")
            self._expire_if_timeout()
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
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/pytest tests/test_game.py -v`
Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add src/xqserver tests/test_game.py
git commit -m "feat(server): 对局状态机——令牌/两阶段提交/双钟/和棋认输/思考隔离"
```

---

### Task 7: FastAPI 服务（routes + SSE 广播 + JSONL 持久化）

**Files:**
- Create: `src/xqserver/store.py`
- Create: `src/xqserver/app.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `Game`（Task 6）
- Produces：
  - `create_app(data_dir="data") -> FastAPI`
  - `Store(data_dir)`：`store.create(red_name, black_name, **clock_cfg) -> Game`，`store.get(gid) -> Game`（不存在抛 `KeyError`），`store.append_record(game, record)`（追加到 `data/games/<gid>.jsonl`，文件权限 600）
  - HTTP 端点（对局操作经 `X-Seat-Token` 头鉴权，颜色由 token 反查）：
    - `POST /api/games` `{red_name, black_name, base_seconds?, step_seconds?, increment?, draw_halfmoves?}` → `{game_id, red_token, black_token, watch_url}`
    - `GET /api/games/{gid}/state` → `game.state_for(color)`；当 `your_turn` 时附带 `legal_moves`
    - `POST /api/games/{gid}/move` `{move, note?}` → preview
    - `POST /api/games/{gid}/commit` `{pending_id}` → history 条目 + `result`/`result_reason`
    - `POST /api/games/{gid}/cancel` `{pending_id}`
    - `POST /api/games/{gid}/note` `{text}`
    - `POST /api/games/{gid}/resign` / `offer-draw` / `accept-draw`
    - `GET /api/games/{gid}/wait?ply=N` → 长轮询（最长 30s），ply 超过 N 或对局结束时返回 state
    - `GET /api/games/{gid}/spectate` → SSE：连接先发 `snapshot`（fen+history+notes+clocks+names+result），随后 `move` / `note` / `result` 事件
    - `GET /api/rules` → 纯文本规则摘要

- [ ] **Step 1: 写失败测试**

`tests/test_api.py`：

```python
import pytest
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


async def test_spectate_snapshot(client):
    g = await new_game(client)
    async with client.stream("GET", f"/api/games/{g['game_id']}/spectate") as r:
        assert r.status_code == 200
        first = await r.aiter_lines().__anext__()
    assert "snapshot" in first


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
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/pytest tests/test_api.py -v`
Expected: FAIL，ModuleNotFoundError

- [ ] **Step 3: 实现 `store.py` 与 `app.py`**

`src/xqserver/store.py`：

```python
"""对局注册表 + JSONL 棋谱持久化。"""

import json
import os
import secrets
import time
from pathlib import Path

from xqserver.game import Game


class Store:
    def __init__(self, data_dir="data"):
        self.data_dir = Path(data_dir)
        (self.data_dir / "games").mkdir(parents=True, exist_ok=True)
        os.chmod(self.data_dir, 0o700)
        self.games: dict[str, Game] = {}

    def create(self, red_name, black_name, **clock_cfg) -> Game:
        gid = "g-" + time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
        g = Game(gid, red_name, black_name, **clock_cfg)
        self.games[gid] = g
        return g

    def get(self, gid) -> Game:
        if gid not in self.games:
            raise KeyError(gid)
        return self.games[gid]

    def append_record(self, game: Game, record: dict):
        path = self.data_dir / "games" / f"{game.id}.jsonl"
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        os.chmod(path, 0o600)
```

`src/xqserver/app.py`：

```python
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
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/pytest tests/test_api.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/xqserver/app.py src/xqserver/store.py tests/test_api.py
git commit -m "feat(server): FastAPI 裁判服务——HTTP API、SSE 观战广播、JSONL 棋谱"
```

---

### Task 8: `xq` CLI + seat 文件 + Agent 提示词模板

**Files:**
- Create: `src/xqcli/__init__.py`（空文件）
- Create: `src/xqcli/prompt_template.py`
- Create: `src/xqcli/main.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: Task 7 的全部 HTTP 端点
- Produces：
  - typer 命令：`serve / new / join / state / legal-moves / move / commit / cancel / note / wait / resign / offer-draw / accept-draw / rules / replay`
  - `save_seat(seat: dict)` / `load_seat() -> dict`：seat 文件 `.xqseat.json`（当前工作目录，权限 600），字段 `{server, game_id, color, token}`
  - `render_agent_prompt(game_id, color, token, server, step_time="3 分钟") -> str`
  - `xq new` 把两份提示词写入 `seats/<gid>-red.md` / `<gid>-black.md`

- [ ] **Step 1: 写失败测试**

`tests/test_cli.py`：

```python
import os
import stat

from xqcli.prompt_template import render_agent_prompt


def test_prompt_template_content():
    p = render_agent_prompt("g-1", "r", "tok123", "http://127.0.0.1:8000")
    assert "红方" in p and "tok123" in p and "g-1" in p
    assert "xq wait" in p and "xq move" in p and "xq commit" in p
    assert "蹩马腿" in p and "炮架" in p          # 规则速查
    assert "h2e2" in p                           # ICCS 示例
    p2 = render_agent_prompt("g-1", "b", "tok456", "http://127.0.0.1:8000")
    assert "黑方" in p2 and "tok456" in p2


def test_seat_file_permissions(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    import importlib
    import xqcli.main as m
    importlib.reload(m)               # 让 SEAT_FILE 落到 tmp_path
    m.save_seat({"server": "http://x", "game_id": "g", "color": "r", "token": "t"})
    mode = stat.S_IMODE(os.stat(".xqseat.json").st_mode)
    assert mode == 0o600
    assert m.load_seat()["token"] == "t"
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: FAIL，ModuleNotFoundError

- [ ] **Step 3: 实现 `prompt_template.py` 与 `main.py`**

`src/xqcli/prompt_template.py`：

```python
"""生成喂给 Agent Harness 的提示词。"""

RED_GUIDE = "你是红方，先行。你的棋子是大写（R N B A K C P = 车马相仕帅炮兵）。"
BLACK_GUIDE = "你是黑方，后行。你的棋子是小写（r n b a k c p = 车马象士将炮卒）。"

TEMPLATE = """# 中国象棋对弈任务

{side_guide}

## 你的身份
- 对局 ID: {game_id}
- 座位令牌: {token}
- 裁判服务器: {server}

## 第一步：入座
```bash
xq join {game_id} --color {color} --token {token} --server {server}
```

## 对弈循环（严格遵守）
1. `xq wait` —— 阻塞等待轮到你。输出里 `轮到你: True` 才轮到你。
2. `xq state` —— 查看局面（ASCII 棋盘 + FEN + 剩余时间）。
3. 思考，并用 `xq note "你的思考"` 记录（只有观众能看到，对手看不到）。
4. `xq move <走法>` —— 预览。仔细阅读返回的中文描述、是否吃子、走后 FEN。
5. **核对预览是否符合你的预期**：符合 → `xq commit <pending_id>`；不符合 → `xq cancel <pending_id>` 后重新 `xq move`。
6. 回到第 1 步。

## 走法格式（ICCS 坐标）
4 个字符：起点列+起点行+终点列+终点行。列 a-i（红方视角从左到右），行 0-9（0 是红方底线，9 是黑方底线）。
例：红方开局 `h2e2` 就是"炮二平五"。拿不准时先运行 `xq legal-moves` 查看当前全部合法走法。

## 规则速查
车：直线任意远，不可越子。马：走日字，马腿（正方向相邻点）被占则不可走（蹩马腿）。
相/象：走田字，田心有子（塞象眼）不可走，不可过河。仕/士：九宫内斜走一格。
帅/将：九宫内直走一格；两帅将不能在同一直线上照面（白脸将）。
炮：直走；吃子必须恰好隔一个子（炮架）。兵/卒：只进不退，过河后可横走。
将死或困毙对方获胜；超时判负。每步限时 {step_time}，请留意 `xq state` 里的剩余时间。

## 纪律
- 只能在自己回合走棋；非法走法会被服务器拒绝并说明原因。
- 不得尝试获取对手的任何非公开信息；不得读取其他 Agent 的文件或进程。
- 认输：`xq resign`；提和：`xq offer-draw`；接受和棋：`xq accept-draw`。
"""


def render_agent_prompt(game_id, color, token, server, step_time="3 分钟"):
    return TEMPLATE.format(
        side_guide=RED_GUIDE if color == "r" else BLACK_GUIDE,
        game_id=game_id, color="red" if color == "r" else "black",
        token=token, server=server, step_time=step_time,
    )
```

`src/xqcli/main.py`：

```python
"""xq CLI：Agent Harness 与裁判服务器交互的命令行。"""

import json
import os
from pathlib import Path

import httpx
import typer

app = typer.Typer(help="Xiangqi Arena CLI")
SEAT_FILE = Path(".xqseat.json")
DEFAULT_SERVER = "http://127.0.0.1:8000"


def save_seat(seat: dict):
    SEAT_FILE.write_text(json.dumps(seat, ensure_ascii=False, indent=2))
    os.chmod(SEAT_FILE, 0o600)


def load_seat() -> dict:
    if not SEAT_FILE.exists():
        typer.echo("未入座：先运行 xq join", err=True)
        raise typer.Exit(1)
    return json.loads(SEAT_FILE.read_text())


def client(seat) -> httpx.Client:
    return httpx.Client(base_url=seat["server"],
                        headers={"X-Seat-Token": seat["token"]}, timeout=60)


def show(resp: httpx.Response):
    if resp.status_code >= 400:
        try:
            msg = resp.json().get("error", resp.text)
        except Exception:
            msg = resp.text
        typer.echo(f"错误({resp.status_code}): {msg}", err=True)
        raise typer.Exit(1)
    return resp.json()


def print_state(st: dict):
    typer.echo(st["ascii"])
    typer.echo(f"FEN: {st['fen']}")
    typer.echo(f"轮到你: {st['your_turn']}  时钟: {st['clocks']}")
    if st["result"]:
        typer.echo(f"对局结束: {st['result']} ({st['result_reason']})")


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000, data_dir: str = "data"):
    import uvicorn
    from xqserver.app import create_app
    uvicorn.run(create_app(data_dir), host=host, port=port)


@app.command()
def new(red_name: str, black_name: str, server: str = DEFAULT_SERVER,
        time_limit: str = "15m", step_time: str = "3m", increment: int = 10):
    def secs(s: str) -> float:
        return float(s[:-1]) * (60 if s.endswith("m") else 1)
    r = httpx.post(f"{server}/api/games", json={
        "red_name": red_name, "black_name": black_name,
        "base_seconds": secs(time_limit), "step_seconds": secs(step_time),
        "increment": increment,
    })
    g = show(r)
    from xqcli.prompt_template import render_agent_prompt
    seats = Path("seats")
    seats.mkdir(exist_ok=True)
    for color, cn in (("r", "red"), ("b", "black")):
        p = render_agent_prompt(g["game_id"], color, g[f"{cn}_token"], server, step_time)
        (seats / f"{g['game_id']}-{cn}.md").write_text(p, encoding="utf-8")
    typer.echo(json.dumps(g, ensure_ascii=False, indent=2))
    typer.echo(f"提示词已写入 seats/ 目录；观战 SSE: {server}/api/games/{g['game_id']}/spectate")


@app.command()
def join(game_id: str, color: str, token: str, server: str = DEFAULT_SERVER):
    c = "r" if color.startswith("r") else "b"
    save_seat({"server": server, "game_id": game_id, "color": c, "token": token})
    typer.echo(f"已入座 {color}，对局 {game_id}")


@app.command()
def state():
    seat = load_seat()
    with client(seat) as c:
        print_state(show(c.get(f"/api/games/{seat['game_id']}/state")))


@app.command(name="legal-moves")
def legal_moves_cmd():
    seat = load_seat()
    with client(seat) as c:
        st = show(c.get(f"/api/games/{seat['game_id']}/state"))
    typer.echo(" ".join(st.get("legal_moves", [])) or "（未轮到你或对局结束）")


@app.command()
def move(iccs: str, note: str | None = None):
    seat = load_seat()
    with client(seat) as c:
        pv = show(c.post(f"/api/games/{seat['game_id']}/move",
                         json={"move": iccs, "note": note}))
    typer.echo(json.dumps(pv, ensure_ascii=False, indent=2))
    typer.echo("核对无误后: xq commit " + pv["pending_id"])


@app.command()
def commit(pending_id: str):
    seat = load_seat()
    with client(seat) as c:
        typer.echo(json.dumps(show(c.post(
            f"/api/games/{seat['game_id']}/commit",
            json={"pending_id": pending_id})), ensure_ascii=False, indent=2))


@app.command()
def cancel(pending_id: str):
    seat = load_seat()
    with client(seat) as c:
        show(c.post(f"/api/games/{seat['game_id']}/cancel",
                    json={"pending_id": pending_id}))
    typer.echo("已取消")


@app.command()
def note(text: str):
    seat = load_seat()
    with client(seat) as c:
        show(c.post(f"/api/games/{seat['game_id']}/note", json={"text": text}))
    typer.echo("已记录（仅观众可见）")


@app.command()
def wait():
    seat = load_seat()
    with client(seat) as c:
        st = show(c.get(f"/api/games/{seat['game_id']}/state"))
        while not st["your_turn"] and not st["result"]:
            st = show(c.get(f"/api/games/{seat['game_id']}/wait",
                            params={"ply": st["ply"]}))
        print_state(st)


@app.command()
def resign():
    seat = load_seat()
    with client(seat) as c:
        typer.echo(json.dumps(show(c.post(f"/api/games/{seat['game_id']}/resign"))))


@app.command(name="offer-draw")
def offer_draw():
    seat = load_seat()
    with client(seat) as c:
        show(c.post(f"/api/games/{seat['game_id']}/offer-draw"))
    typer.echo("已提出和棋")


@app.command(name="accept-draw")
def accept_draw():
    seat = load_seat()
    with client(seat) as c:
        typer.echo(json.dumps(show(c.post(f"/api/games/{seat['game_id']}/accept-draw"))))


@app.command()
def rules(server: str = DEFAULT_SERVER):
    r = httpx.get(f"{server}/api/rules")
    typer.echo(r.json()["rules"])


@app.command()
def replay(game_id: str, data_dir: str = "data"):
    path = Path(data_dir) / "games" / f"{game_id}.jsonl"
    for line in path.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if rec.get("type") == "move":
            typer.echo(f"{rec['ply']:>3}. {'红' if rec['color'] == 'r' else '黑'} "
                       f"{rec['chinese']} ({rec['iccs']}) {rec['elapsed']}s")
        elif rec.get("type") == "note":
            typer.echo(f"      [思考·{'红' if rec['color'] == 'r' else '黑'}] {rec['text']}")
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add src/xqcli tests/test_cli.py
git commit -m "feat(cli): xq 命令全集、seat 文件(600)、Agent 提示词模板"
```

---

### Task 9: 端到端——两个模拟 Agent 打完一局

**Files:**
- Test: `tests/test_e2e.py`

**Interfaces:**
- Consumes: 全部前述接口
- Produces: 证明"两个 Agent 走 state→move→commit 循环直到终局"的 e2e 测试，以及思考隔离的端到端断言

- [ ] **Step 1: 写测试**

`tests/test_e2e.py`：

```python
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
```

- [ ] **Step 2: 运行确认通过**

Run: `.venv/bin/pytest tests/test_e2e.py -v`
Expected: 1 passed

- [ ] **Step 3: 全量回归 + Commit**

Run: `.venv/bin/pytest -v`
Expected: 全部通过（约 44 个测试）

```bash
git add tests/test_e2e.py
git commit -m "test(e2e): 两个模拟 Agent 经两阶段提交打完一局 + 思考隔离断言"
```

---

## 交付验证（人工）

```bash
cd /Users/wushuyue/Documents/自学/Coding/xiangqi-arena
.venv/bin/xq serve &                          # 启动裁判服务器
.venv/bin/xq new --red-name Kimi --black-name Claude
cat seats/<gid>-red.md                        # 喂给一个 Harness（红方）
cat seats/<gid>-black.md                      # 喂给另一个 Harness（黑方）
curl -N http://127.0.0.1:8000/api/games/<gid>/spectate   # 文本观战流
```

## 后续计划

- 计划 2（spec 里程碑 4–5）：Web 界面——SVG 棋盘、双朝向、观战页双侧栏思考流、对弈页点击走子、棋子资源层、复盘页。
- 计划 3（spec 里程碑 6–7）：Pikafish UCI 集成（AI 对手/局面分析）、sandbox-exec 加固脚本、赛后 trajectory 审计工具。
