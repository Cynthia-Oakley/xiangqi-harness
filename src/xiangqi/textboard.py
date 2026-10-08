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
