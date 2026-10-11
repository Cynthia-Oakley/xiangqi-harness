"""ASCII 棋盘渲染：按行棋方视角渲染（自己的棋子始终在下方），坐标尺印的是 ICCS 坐标。

黑方视角 = 棋盘旋转 180°：列从左到右为 i→a，行从上而下为 0→9。这不是另一套坐标系，
而是同一套 ICCS 坐标在该视角下的自然排列（与 Lichess/Chess.com 黑方视角同惯例）——
读棋盘时直接照图上印的坐标读，不要做翻转心算。

输出含：视角行、上下坐标尺、左右行号。piece_listing 把盘面复述为坐标清单，
让 Agent 不必逐格扫盘定位棋子。
"""

from xiangqi.board import Board, RED, rc_to_iccs, side_of

GLYPHS = {
    "R": "车", "N": "马", "B": "相", "A": "仕", "K": "帅", "C": "炮", "P": "兵",
    "r": "車", "n": "馬", "b": "象", "a": "士", "k": "將", "c": "砲", "p": "卒",
}

PIECE_ORDER = "KABRNCP"           # 清单按 帅/仕/相/车/马/炮/兵 分组


def render_ascii(board: Board, perspective: str = RED) -> str:
    """perspective="b" 时旋转 180°：黑方底线在底部，列从左到右 i..a（即 ICCS 列标的视角排列）。"""
    black_view = perspective == "b"
    grid = ([list(reversed(r)) for r in reversed(board.grid)]
            if black_view else board.grid)
    header = "ihgfedcba" if black_view else "abcdefghi"
    ruler = "   " + " ".join(header)
    if black_view:
        view = ("视角: 黑方（你的棋子在下方；坐标尺=ICCS 坐标，列左→右为 i→a，行上→下为 0→9，"
                "直接照图读坐标）")
    else:
        view = "视角: 红方（你的棋子在下方；坐标尺=ICCS 坐标，列左→右为 a→i，行上→下为 9→0）"
    lines = [view, ruler]
    for row in range(10):
        rank = row if black_view else 9 - row
        cells = [GLYPHS[p] if p else "＋" for p in grid[row]]
        lines.append(f"{rank}  " + " ".join(cells) + f"  {rank}")
    lines.append(ruler)
    return "\n".join(lines)


def piece_listing(board: Board) -> str:
    """双方棋子坐标清单：红: 帅e0 仕d0,f0 … / 黑: 將e9 …"""
    lines = []
    for side, label in ((RED, "红"), ("b", "黑")):
        groups: dict[str, list[str]] = {}
        for r in range(10):
            for c in range(9):
                p = board.grid[r][c]
                if p and side_of(p) == side:
                    groups.setdefault(p.upper(), []).append(rc_to_iccs(r, c))
        parts = []
        for t in PIECE_ORDER:
            if t in groups:
                name = GLYPHS[t if side == RED else t.lower()]
                squares = sorted(groups[t])
                parts.append(name + ",".join(squares))
        lines.append(f"{label}: " + " ".join(parts))
    return "\n".join(lines)
