"""ASCII 棋盘渲染：统一红方标准视角——列 a-i 从左到右，行 9→0 自上而下，红方在下方。

全局只保留这一个空间参照系，与 ICCS 坐标、棋盘 PNG 严格一致（曾有 Agent 因
黑方翻转视角与 ICCS 字母顺序相反而反复算错坐标）。中文记谱的路数方向差异
由 CLI 的「路数对照」行与 --expect 校验兜底。

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
    """perspective 参数保留兼容但不再生效：一律红方标准视角。"""
    view = "视角: 红方标准视角（列 a→i 从左到右，红方在下方；与 ICCS 坐标一致）"
    ruler = "   " + " ".join("abcdefghi")
    lines = [view, ruler]
    for row in range(10):
        rank = 9 - row
        cells = [GLYPHS[p] if p else "＋" for p in board.grid[row]]
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
