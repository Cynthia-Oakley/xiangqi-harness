from xiangqi.board import Board
from xiangqi.notation import from_chinese, is_valid_iccs, to_chinese


def test_is_valid_iccs():
    assert is_valid_iccs("h2e2")
    assert not is_valid_iccs("")
    assert not is_valid_iccs("h2")
    assert not is_valid_iccs("z9z9")
    assert not is_valid_iccs("h2h2")
    assert not is_valid_iccs("h10e2")


def test_opening_cannon():
    b = Board.initial()
    assert to_chinese(b, "h2e2") == "炮二平五"


def test_opening_horse():
    b = Board.initial()
    assert to_chinese(b, "b0c2") == "马八进七"
    assert to_chinese(b, "b0a2") == "马八进九"


def test_black_notation():
    b = Board.initial()
    assert to_chinese(b, "b9c7") == "马2进3"   # 黑方路数从黑方右侧起算
    assert to_chinese(b, "h9g7") == "马8进7"


def test_pawn_advance():
    b = Board.initial()
    assert to_chinese(b, "a3a4") == "兵九进一"   # 红兵 (6,0) 前进一步


def test_cannon_steps():
    b = Board.from_fen("4k4/9/9/9/9/9/9/9/4C4/4K4 w - - 0 1")
    assert to_chinese(b, "e1e4") == "炮五进三"   # (8,4) → (5,4)，三步


def test_front_back_disambiguation():
    # 红双炮同在 e 路：(4,4) 为前炮、(9,4) 为后炮
    b = Board.from_fen("4k4/9/9/9/4C4/9/9/9/9/3KC4 w - - 0 1")
    assert to_chinese(b, "e5e6") == "前炮进一"   # (4,4) → (3,4)
    assert to_chinese(b, "e0f0") == "后炮平四"   # (9,4) → (9,5)


def test_from_chinese_basic():
    b = Board.initial()
    assert from_chinese(b, "炮二平五") == ["h2e2"]
    assert from_chinese(b, "马八进七") == ["b0c2"]
    assert from_chinese(b, "马2进3") == ["b9c7"]      # 黑方视角路数
    assert from_chinese(b, "兵七进一") == ["c3c4"]
    assert from_chinese(b, "不存在着") == []


def test_from_chinese_front_back_disambiguation():
    """同列双炮：前炮/后炮 各归各位。"""
    b = Board.from_fen("4k4/9/9/9/9/4C4/9/9/4C4/4K4 w - - 0 1")
    assert from_chinese(b, "前炮进一") == ["e4e5"]
    assert from_chinese(b, "后炮平四") == ["e1f1"]  # d=六路 f=四路


def test_roundtrip_all_legal_moves():
    """初始局面所有合法走法：ICCS→中文→ICCS 必须回到自己。"""
    from xiangqi.rules import legal_moves
    b = Board.initial()
    for mv in legal_moves(b, "r"):
        assert from_chinese(b, to_chinese(b, mv)) == [mv]
