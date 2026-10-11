"""将军判定、合法走法过滤（剔除送将/白脸将）、终局判定。"""

from xiangqi.board import Board, RED, BLACK, iccs_to_rc, rc_to_iccs
from xiangqi.moves import pseudo_destinations
from xiangqi.notation import is_valid_iccs


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


_PSEUDO_HINTS = {"N": "蹩马腿或走位不符", "B": "塞象眼、过河或走位不符",
                 "C": "移动需无阻隔、吃子需恰一个炮架"}


def explain_illegal(board: Board, side: str, iccs) -> str | None:
    """走法不合法时给出中文原因；合法返回 None。"""
    if not is_valid_iccs(iccs):
        return "格式不是 4 字符 ICCS（如 h2e2）"
    (fr, fc), (tr, tc) = iccs_to_rc(iccs[:2]), iccs_to_rc(iccs[2:])
    p = board.grid[fr][fc]
    if p is None:
        return "起点格没有棋子"
    if p.isupper() != (side == RED):
        return "不能走对方的棋子"
    t = board.grid[tr][tc]
    if t is not None and t.isupper() == p.isupper():
        return "目标格是己方棋子"
    if t in ("K", "k"):
        return "将帅不可被吃（已将军时应走其他着法）"
    if (tr, tc) not in pseudo_destinations(board, fr, fc):
        return _PSEUDO_HINTS.get(p.upper(), "走位不合该子规则")
    nb = board.copy()
    nb.grid[tr][tc] = p
    nb.grid[fr][fc] = None
    if in_check(nb, side):
        return "走后己方被将军（送将/该子正被钉住）"
    if generals_face(nb):
        return "走后两帅将照面（白脸将）"
    return None


def legal_moves(board: Board, side: str) -> list[str]:
    want_red = side == RED
    res = []
    for r in range(10):
        for c in range(9):
            p = board.grid[r][c]
            if p is None or p.isupper() != want_red:
                continue
            for (tr, tc) in pseudo_destinations(board, r, c):
                target = board.grid[tr][tc]
                if target is not None and target in ("K", "k"):
                    continue  # 将帅不可被吃；能攻击到即已将军，不会实际落子
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
