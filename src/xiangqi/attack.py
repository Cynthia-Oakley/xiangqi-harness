"""攻击图引擎（辅助模式专用）：清点格子的攻防关系、走后自检。

与 moves.pseudo_destinations 的区别：这里是**攻击/吃子语义**——
炮必须恰好隔一个炮架才算攻击；车的攻击止于第一个遇到的子（无论敌我，
打到我方子叫"防守/守护"）。每条攻击记录经由点（炮架/马腿/象眼）供展示。

仅用于 assist 模式对局，不进入走法合法性裁决路径。
"""

from xiangqi.board import Board, RED, BLACK, iccs_to_rc, rc_to_iccs, side_of

ORTH = ((-1, 0), (1, 0), (0, -1), (0, 1))
PALACE = {RED: range(7, 10), BLACK: range(0, 3)}

NAME = {
    "K": "帅", "A": "仕", "B": "相", "R": "车", "N": "马", "C": "炮", "P": "兵",
    "k": "將", "a": "士", "b": "象", "r": "車", "n": "馬", "c": "砲", "p": "卒",
}

# 子力价值（仅用于自检报告里按价值排序提示，不是形势评估）
VALUE = {"R": 9, "N": 4, "C": 4.5, "B": 2, "A": 2, "P": 1, "K": 99}


def _in(r, c):
    return 0 <= r <= 9 and 0 <= c <= 8


def attack_targets(board: Board, row: int, col: int) -> list[dict]:
    """(row,col) 上的棋子的全部攻击目标（吃子语义）。

    返回 [{"square": (r,c), "target": 子或None, "via": (r,c)|None, "via_kind": str|None}]
    via_kind ∈ {"炮架", "马腿", "象眼", "白脸将"}。
    """
    p = board.grid[row][col]
    if p is None:
        return []
    t = p.upper()
    side = side_of(p)
    out = []

    def hit(r, c, via=None, via_kind=None):
        out.append({"square": (r, c), "target": board.grid[r][c],
                    "via": via, "via_kind": via_kind})

    if t == "R":
        for dr, dc in ORTH:
            r, c = row + dr, col + dc
            while _in(r, c):
                if board.grid[r][c] is not None:
                    hit(r, c)
                    break
                r += dr
                c += dc
    elif t == "C":
        for dr, dc in ORTH:
            r, c = row + dr, col + dc
            screen = None
            while _in(r, c):
                q = board.grid[r][c]
                if screen is None:
                    if q is not None:
                        screen = (r, c)
                elif q is not None:
                    hit(r, c, via=screen, via_kind="炮架")
                    break
                r += dr
                c += dc
    elif t == "N":
        for dr, dc, lr, lc in (
            (-2, -1, -1, 0), (-2, 1, -1, 0), (2, -1, 1, 0), (2, 1, 1, 0),
            (-1, -2, 0, -1), (1, -2, 0, -1), (-1, 2, 0, 1), (1, 2, 0, 1),
        ):
            tr, tc = row + dr, col + dc
            if _in(tr, tc) and board.grid[row + lr][col + lc] is None:
                hit(tr, tc, via=(row + lr, col + lc), via_kind="马腿")
    elif t == "B":
        rows = range(5, 10) if side == RED else range(0, 5)     # 相/象不过河
        for dr, dc in ((-2, -2), (-2, 2), (2, -2), (2, 2)):
            tr, tc = row + dr, col + dc
            if _in(tr, tc) and tr in rows:
                eye = (row + dr // 2, col + dc // 2)
                if board.grid[eye[0]][eye[1]] is None:
                    hit(tr, tc, via=eye, via_kind="象眼")
    elif t == "A":
        for dr, dc in ((-1, -1), (-1, 1), (1, -1), (1, 1)):
            tr, tc = row + dr, col + dc
            if tr in PALACE[side] and 3 <= tc <= 5:
                hit(tr, tc)
    elif t == "K":
        for dr, dc in ORTH:
            tr, tc = row + dr, col + dc
            if tr in PALACE[side] and 3 <= tc <= 5:
                hit(tr, tc)
        # 白脸将：同列无阻隔则"攻击"对方将帅
        step = -1 if side == RED else 1
        r = row + step
        while _in(r, col):
            q = board.grid[r][col]
            if q is not None:
                if q.upper() == "K":
                    hit(r, col, via_kind="白脸将")
                break
            r += step
    elif t == "P":
        fwd = -1 if side == RED else 1
        if _in(row + fwd, col):
            hit(row + fwd, col)
        crossed = row <= 4 if side == RED else row >= 5
        if crossed:
            for dc in (-1, 1):
                if _in(row, col + dc):
                    hit(row, col + dc)
    return out


def _fmt_attack(board, r, c, a) -> str:
    """一条攻击记录 → 人读文本，如 '車a9 → 马b0（途经无）' / '砲b7 → 兵c4（炮架 b4）'。"""
    p = board.grid[r][c]
    src = f"{NAME[p]}{rc_to_iccs(r, c)}"
    tr, tc = a["square"]
    tgt = a["target"]
    dst = (f"{NAME[tgt]}{rc_to_iccs(tr, tc)}" if tgt
           else f"空位{rc_to_iccs(tr, tc)}")
    via = ""
    if a["via_kind"] == "白脸将":
        via = "（白脸将）"
    elif a["via_kind"]:
        via = f"（{a['via_kind']} {rc_to_iccs(*a['via'])}）"
    return f"{src} → {dst}{via}"


def square_report(board: Board, square: str) -> str:
    """落点攻防清点：谁攻击/防守此格；若此格有子，它攻击/防守哪些格子。"""
    qr, qc = iccs_to_rc(square)
    occupant = board.grid[qr][qc]
    red_hits, black_hits = [], []
    for r in range(10):
        for c in range(9):
            p = board.grid[r][c]
            if p is None or (r, c) == (qr, qc):
                continue
            for a in attack_targets(board, r, c):
                if a["square"] == (qr, qc):
                    line = _fmt_attack(board, r, c, a)
                    (red_hits if side_of(p) == RED else black_hits).append(line)
    lines = [f"格 {square}: " + (f"{NAME[occupant]}（{'红' if occupant.isupper() else '黑'}）"
                                 if occupant else "空")]
    if occupant:
        own = red_hits if occupant.isupper() else black_hits
        foe = black_hits if occupant.isupper() else red_hits
        lines.append("攻击它的（对方）: " + ("、".join(foe) if foe else "无"))
        lines.append("守护它的（己方）: " + ("、".join(own) if own else "无"))
        mine = [_fmt_attack(board, qr, qc, a) for a in attack_targets(board, qr, qc)]
        lines.append("它能攻击/守护: " + ("、".join(mine) if mine else "无"))
    else:
        lines.append("红方能打到: " + ("、".join(red_hits) if red_hits else "无"))
        lines.append("黑方能打到: " + ("、".join(black_hits) if black_hits else "无"))
    return "\n".join(lines)


def hanging_pieces(board: Board, side: str) -> dict[str, str]:
    """side 方当前"被攻击且无守护"的子：{格子: 描述}。"""
    out = {}
    for r in range(10):
        for c in range(9):
            p = board.grid[r][c]
            if p is None or side_of(p) != side:
                continue
            attacked = defended = False
            for rr in range(10):
                for cc in range(9):
                    q = board.grid[rr][cc]
                    if q is None or (rr, cc) == (r, c):
                        continue
                    for a in attack_targets(board, rr, cc):
                        if a["square"] == (r, c):
                            if side_of(q) == side:
                                defended = True
                            else:
                                attacked = True
            if attacked and not defended:
                out[rc_to_iccs(r, c)] = NAME[p]
    return out


def _attacks_on(board: Board, target_side: str) -> set[tuple[str, str]]:
    """攻击 target_side 方棋子的全部 (攻击者格子, 被攻格子) 对。"""
    out = set()
    for r in range(10):
        for c in range(9):
            p = board.grid[r][c]
            if p is None or side_of(p) == target_side:
                continue
            for a in attack_targets(board, r, c):
                tr, tc = a["square"]
                q = board.grid[tr][tc]
                if q is not None and side_of(q) == target_side:
                    out.add((rc_to_iccs(r, c), rc_to_iccs(tr, tc)))
    return out


def pinned_pieces(board: Board, side: str) -> dict[str, str]:
    """side 方被牵制的子：移除它后，己方将帅或更高价值的子暴露在攻击下。"""
    out = {}
    base_attacks = _attacks_on(board, side)
    for r in range(10):
        for c in range(9):
            p = board.grid[r][c]
            if p is None or side_of(p) != side or p.upper() == "K":
                continue
            nb = board.copy()
            nb.grid[r][c] = None
            new = _attacks_on(nb, side) - base_attacks
            for _, tgt_sq in new:
                tr, tc = iccs_to_rc(tgt_sq)
                q = nb.grid[tr][tc]
                if q and (q.upper() == "K" or VALUE[q.upper()] > VALUE[p.upper()]):
                    out[rc_to_iccs(r, c)] = (
                        f"{NAME[p]}被牵制（走后 {NAME[q]}{tgt_sq} 暴露）")
                    break
    return out


def self_check(board_before: Board, board_after: Board, side: str,
               moved_from: str, moved_to: str) -> str:
    """走后自检：对比走前/走后，报告新挂的子、被打通的线、新产生的牵制。"""
    hang_before = hanging_pieces(board_before, side)
    hang_after = hanging_pieces(board_after, side)
    new_hanging = {k: v for k, v in hang_after.items() if k not in hang_before}

    atk_before = _attacks_on(board_before, side)
    atk_after = _attacks_on(board_after, side)
    new_lines = atk_after - atk_before

    pins_before = pinned_pieces(board_before, side)
    pins_after = pinned_pieces(board_after, side)
    new_pins = {k: v for k, v in pins_after.items() if k not in pins_before}

    lines = ["走后自检:"]
    if new_hanging:
        items = "、".join(f"{v}{k}" for k, v in
                          sorted(new_hanging.items(),
                                 key=lambda kv: -VALUE[_inv(kv[0], board_after)]))
        lines.append(f"⚠️ 新送入口的子: {items}")
    else:
        lines.append("✓ 没有新送入口的子")
    if new_lines:
        desc = "、".join(f"{src}→{tgt}" for src, tgt in sorted(new_lines))
        lines.append(f"⚠️ 对方新打通的攻击线: {desc}")
    if new_pins:
        lines.append("⚠️ 新产生的牵制: " + "、".join(new_pins.values()))
    if not new_hanging and not new_lines and not new_pins:
        lines.append("✓ 未发现新暴露的战术漏洞")
    return "\n".join(lines)


def _inv(square: str, board: Board) -> str:
    p = board.grid[iccs_to_rc(square)[0]][iccs_to_rc(square)[1]]
    return p.upper() if p else "P"
