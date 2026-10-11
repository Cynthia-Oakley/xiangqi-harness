from xiangqi.board import Board
from xiangqi.rules import apply_move
from xiangqi.textboard import render_ascii


def test_render_initial():
    s = render_ascii(Board.initial())
    lines = s.splitlines()
    assert lines[0].strip().startswith("a")
    assert lines[1].startswith("9") and "車" in lines[1] and "將" in lines[1]
    assert lines[-1].startswith("0") and "帅" in lines[-1]
    assert "炮" in s and "砲" in s   # 双色炮都在


def test_render_move():
    nb = apply_move(Board.initial(), "h2e2")
    rank2 = [l for l in render_ascii(nb).splitlines() if l.startswith("2")][0]
    assert rank2.count("炮") == 2    # 两门红炮都还在第 2 排


def test_render_black_perspective():
    """P0-2：黑方视角旋转 180°，黑方底线（將在 e9）在倒数第一行。"""
    s = render_ascii(Board.initial(), perspective="b")
    lines = s.splitlines()
    assert lines[0].strip().startswith("i") and lines[0].strip().endswith("a")
    assert lines[1].startswith("0") and "帅" in lines[1]
    assert lines[-1].startswith("9") and "將" in lines[-1]
    # 同一格子两种视角下glyph一致：e9 的將在 i..a 第 5 列（行标占 split 后第 1 位）
    row_black = lines[-1].split()
    assert row_black[5] == "將"
