"""ICCS 校验与中文记谱生成；from_chinese 用"生成-匹配"法反解中文记谱。"""

from xiangqi.board import Board, RED, iccs_to_rc, rc_to_iccs

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


def from_chinese(board: Board, text: str) -> list[str]:
    """中文记谱 → 当前局面下所有匹配的 ICCS 走法（0 个、1 个或多个）。

    生成-匹配法：枚举全盘每个棋子的每个几何可达落点，用 to_chinese 生成记谱，
    与输入完全相等的即为候选。天然处理 前/后炮 等消歧，且与 to_chinese 严格互逆。
    """
    from xiangqi.moves import pseudo_destinations
    text = text.strip()
    out = []
    for r in range(10):
        for c in range(9):
            if board.grid[r][c] is None:
                continue
            for (tr, tc) in pseudo_destinations(board, r, c):
                iccs = rc_to_iccs(r, c) + rc_to_iccs(tr, tc)
                if to_chinese(board, iccs) == text:
                    out.append(iccs)
    return out
