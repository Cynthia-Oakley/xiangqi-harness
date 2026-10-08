import os
import stat

from xqcli.prompt_template import render_agent_prompt


def test_prompt_template_content():
    p = render_agent_prompt("g-1", "r", "tok123", "http://127.0.0.1:8000")
    assert "红方" in p and "tok123" in p and "g-1" in p
    assert "xq wait" in p and "xq move" in p and "xq commit" in p
    assert "蹩马腿" in p and "炮架" in p          # 规则速查
    assert "h2e2" in p                           # ICCS 示例
    p2 = render_agent_prompt("g-1", "b", "tok456", "http://127.0.0.1:8000")
    assert "黑方" in p2 and "tok456" in p2


def test_seat_file_permissions(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    import importlib
    import xqcli.main as m
    importlib.reload(m)               # 让 SEAT_FILE 落到 tmp_path
    m.save_seat({"server": "http://x", "game_id": "g", "color": "r", "token": "t"})
    mode = stat.S_IMODE(os.stat(".xqseat.json").st_mode)
    assert mode == 0o600
    assert m.load_seat()["token"] == "t"
