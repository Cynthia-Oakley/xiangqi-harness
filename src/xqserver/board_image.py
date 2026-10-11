"""棋盘 PNG 渲染：给支持看图的模型一个低 token 的视觉校准入口。

设计目标是小图（400x444，Claude 估算约 240 tokens），线条清晰、无装饰。
perspective="b" 时棋子按黑方视角 180° 翻转（与 ASCII 棋盘的翻转约定一致）。
"""

import io
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

from xiangqi.board import RED, iccs_to_rc

M, S = 24, 44                      # 边距 / 格距（像素）
W, H = 2 * M + 8 * S, 2 * M + 9 * S   # 400 x 444
PIECE_R = 19

C_BG = "#f0d8a8"                   # 棋盘底色
C_LINE = "#5b3a1e"                 # 线/黑方
C_RED = "#b03028"
C_PIECE = "#faf0dc"                # 棋子面
C_MARK = "#2e7d32"                 # 上一步高亮

GLYPH = {
    "K": "帅", "A": "仕", "B": "相", "R": "车", "N": "马", "C": "炮", "P": "兵",
    "k": "將", "a": "士", "b": "象", "r": "車", "n": "馬", "c": "砲", "p": "卒",
}

_FONT_CANDIDATES = [
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Songti.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
]


@lru_cache(maxsize=4)
def _font(size: int) -> ImageFont.FreeTypeFont:
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _xy(row: int, col: int, flip: bool) -> tuple[int, int]:
    """逻辑格点 → 屏幕像素。row 0 = 黑方底线（红方视角的顶部）。"""
    if flip:
        row, col = 9 - row, 8 - col
    return M + col * S, M + row * S


def render_board(board, perspective: str = RED,
                 last_move: str | None = None) -> Image.Image:
    flip = perspective != RED
    img = Image.new("RGB", (W, H), C_BG)
    d = ImageDraw.Draw(img)

    def pt(r, c):  # 本函数内统一走翻转映射；棋盘线/九宫/河界 180° 对称，直接画
        return _xy(r, c, flip)

    # 外框（加粗）
    d.rectangle([M - 4, M - 4, M + 8 * S + 4, M + 9 * S + 4], outline=C_LINE, width=3)
    # 横线
    for r in range(10):
        d.line([M, M + r * S, M + 8 * S, M + r * S], fill=C_LINE, width=1)
    # 竖线：边线贯通，中间线在河界断开
    d.line([M, M, M, M + 9 * S], fill=C_LINE, width=1)
    d.line([M + 8 * S, M, M + 8 * S, M + 9 * S], fill=C_LINE, width=1)
    for c in range(1, 8):
        d.line([M + c * S, M, M + c * S, M + 4 * S], fill=C_LINE, width=1)
        d.line([M + c * S, M + 5 * S, M + c * S, M + 9 * S], fill=C_LINE, width=1)
    # 九宫斜线
    for r0 in (0, 7):
        d.line([M + 3 * S, M + r0 * S, M + 5 * S, M + (r0 + 2) * S], fill=C_LINE, width=1)
        d.line([M + 5 * S, M + r0 * S, M + 3 * S, M + (r0 + 2) * S], fill=C_LINE, width=1)
    # 河界
    river = _font(20)
    d.text((M + 1.5 * S, M + 4.5 * S), "楚 河", font=river, fill=C_LINE, anchor="mm")
    d.text((M + 6.5 * S, M + 4.5 * S), "漢 界", font=river, fill=C_LINE, anchor="mm")

    # 上一步起止点高亮（绿环，压在棋子下面）
    if last_move and last_move != "pass":
        for sq in (last_move[:2], last_move[2:]):
            x, y = pt(*iccs_to_rc(sq))
            d.ellipse([x - PIECE_R - 3, y - PIECE_R - 3,
                       x + PIECE_R + 3, y + PIECE_R + 3], outline=C_MARK, width=3)

    # 棋子
    piece_font = _font(26)
    for r in range(10):
        for c in range(9):
            p = board.grid[r][c]
            if not p:
                continue
            x, y = pt(r, c)
            color = C_RED if p.isupper() else C_LINE
            d.ellipse([x - PIECE_R, y - PIECE_R, x + PIECE_R, y + PIECE_R],
                      fill=C_PIECE, outline=color, width=2)
            d.text((x, y), GLYPH[p], font=piece_font, fill=color, anchor="mm")
    return img


def render_board_png(board, perspective: str = RED,
                     last_move: str | None = None) -> bytes:
    img = render_board(board, perspective, last_move)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
