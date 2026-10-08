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
