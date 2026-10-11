import os
import stat

from xqcli.prompt_template import render_agent_prompt


def test_prompt_template_content():
    p = render_agent_prompt("g-1", "r", "tok123", "http://127.0.0.1:8000")
    assert "红方" in p and "tok123" in p and "g-1" in p
    assert "xq wait" in p and "xq move" in p and "xq commit" in p
    assert "蹩马腿" in p and "炮架" in p          # 规则速查
    assert "h2e2" in p                           # ICCS 示例
    assert "双方入座" in p                        # 计时从双方都 join 才开始
    assert "停着" in p                            # 默认宽松模式的超时说明
    p_lose = render_agent_prompt("g-1", "r", "tok123", "http://127.0.0.1:8000",
                                 step_timeout="lose")
    assert "停着" not in p_lose and "超时即判负" in p_lose
    p2 = render_agent_prompt("g-1", "b", "tok456", "http://127.0.0.1:8000")
    assert "黑方" in p2 and "tok456" in p2


def test_prompt_time_management_section():
    p = render_agent_prompt("g-1", "r", "tok123", "http://127.0.0.1:8000",
                            time_limit="15 分钟", increment=10)
    assert "时间管理" in p
    assert "15 分钟" in p and "+10" in p        # 局时与加秒
    assert "时间状态" in p                       # 告知每条命令都带时间行
    p2 = render_agent_prompt("g-1", "r", "tok123", "http://127.0.0.1:8000")
    assert "时间管理" in p2                      # 默认值也能渲染


def test_seat_file_permissions(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    import importlib
    import xqcli.main as m
    importlib.reload(m)               # 让 SEAT_FILE 落到 tmp_path
    m.save_seat({"server": "http://x", "game_id": "g", "color": "r", "token": "t"})
    mode = stat.S_IMODE(os.stat(".xqseat.json").st_mode)
    assert mode == 0o600
    assert m.load_seat()["token"] == "t"


def test_prompt_join_command_actually_runs(tmp_path, monkeypatch):
    """C1 回归：提示词里的 join 命令必须能被 CLI 真实执行。
    服务器不可达时降级为仅本地入座（警告提示），exit 仍为 0。"""
    monkeypatch.chdir(tmp_path)
    from typer.testing import CliRunner
    from xqcli.main import app
    p = render_agent_prompt("g-1", "r", "tok123", "http://127.0.0.1:8000")
    assert "xq join g-1 red tok123" in p   # 模板与 CLI 位置参数签名一致
    r = CliRunner().invoke(app, ["join", "g-1", "red", "tok123",
                                 "--server", "http://127.0.0.1:9"])
    assert r.exit_code == 0, r.output
    assert "警告" in r.output              # 明确告知未向服务器注册


def test_parse_duration():
    from xqcli.main import parse_duration
    assert parse_duration("15m") == 900
    assert parse_duration("3m") == 180
    assert parse_duration("900") == 900    # I3 回归：纯数字不再被截位
    assert parse_duration("45s") == 45
