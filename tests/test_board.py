from xiangqi.board import Board, INITIAL_FEN, iccs_to_rc, rc_to_iccs


def test_initial_fen_roundtrip():
    b = Board.initial()
    assert b.to_fen() == INITIAL_FEN
    assert b.turn == "r"
    assert b.halfmove == 0 and b.fullmove == 1


def test_piece_placement():
    b = Board.initial()
    assert b.piece_at(0, 0) == "r"   # 黑车
    assert b.piece_at(0, 4) == "k"   # 黑将
    assert b.piece_at(9, 4) == "K"   # 红帅
    assert b.piece_at(7, 1) == "C"   # 红炮
    assert b.piece_at(6, 0) == "P"   # 红兵
    assert b.piece_at(4, 0) is None


def test_iccs_conversion():
    assert iccs_to_rc("h2") == (7, 7)
    assert iccs_to_rc("e2") == (7, 4)
    assert rc_to_iccs(7, 7) == "h2"
    assert rc_to_iccs(0, 0) == "a9"


def test_iccs_roundtrip_all():
    for r in range(10):
        for c in range(9):
            assert iccs_to_rc(rc_to_iccs(r, c)) == (r, c)
