from xiangqi.board import Board
from xiangqi.rules import apply_move
from xiangqi.textboard import piece_listing, render_ascii


def test_render_initial():
    s = render_ascii(Board.initial())
    lines = s.splitlines()
    assert lines[0].startswith("视角: 红方")
    assert lines[1].strip().startswith("a") and lines[1].strip().endswith("i")
    rows = lines[2:12]
    assert rows[0].startswith("9") and "車" in rows[0] and "將" in rows[0]
    assert rows[-1].startswith("0") and "帅" in rows[-1]
    assert rows[0].endswith("9")                      # 右侧行号
    assert lines[-1].strip().startswith("a")          # 底部坐标尺
    assert "炮" in s and "砲" in s   # 双色炮都在


def test_render_move():
    nb = apply_move(Board.initial(), "h2e2")
    rank2 = [l for l in render_ascii(nb).splitlines() if l.startswith("2")][0]
    assert rank2.count("炮") == 2    # 两门红炮都还在第 2 排


def test_render_black_perspective():
    """P0-2：黑方视角旋转 180°，黑方底线（將在 e9）在倒数第一行，坐标尺为 i→a。"""
    s = render_ascii(Board.initial(), perspective="b")
    lines = s.splitlines()
    assert lines[0].startswith("视角: 黑方")
    assert lines[1].strip().startswith("i") and lines[1].strip().endswith("a")
    rows = lines[2:12]
    assert rows[0].startswith("0") and "帅" in rows[0]
    assert rows[-1].startswith("9") and "將" in rows[-1]
    # 同一格子两种视角下glyph一致：e9 的將在 i..a 第 5 列（行标占 split 后第 1 位）
    row_black = rows[-1].split()
    assert row_black[5] == "將"


def test_piece_listing():
    """棋子坐标清单：双方分组、按兵种排序、坐标正确、走子后更新。"""
    red, black = piece_listing(Board.initial()).splitlines()
    assert red.startswith("红: ") and "帅e0" in red
    assert "车a0,i0" in red and "马b0,h0" in red and "炮b2,h2" in red
    assert "兵a3,c3,e3,g3,i3" in red
    assert black.startswith("黑: ") and "將e9" in black
    assert "車a9,i9" in black and "馬b9,h9" in black and "砲b7,h7" in black
    nb = apply_move(Board.initial(), "h2e2")
    assert "炮b2,e2" in piece_listing(nb).splitlines()[0]
