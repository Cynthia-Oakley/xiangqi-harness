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
