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
