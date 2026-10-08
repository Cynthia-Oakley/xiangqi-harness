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
