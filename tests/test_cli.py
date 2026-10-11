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


def test_print_state_shows_last_move(capsys):
    """P0-1：state 输出包含对方上一步行（颜色/中文/ICCS/取整用时）。"""
    from xqcli.main import print_state
    st = {
        "ascii": "  a b c d e f g h i", "fen": "fen", "your_turn": False,
        "clocks": {"remaining": {"r": 900, "b": 900}, "step_remaining": 180},
        "last_move": {"color": "r", "iccs": "h2e2",
                      "chinese": "炮二平五", "elapsed": 14.2},
        "result": None, "result_reason": None,
    }
    print_state(st)
    out = capsys.readouterr().out
    assert "上一步: 红 炮二平五 (h2e2) 用时 14s" in out


def test_prompt_pending_lifecycle_section():
    """P0-4：PROMPT 写明预览与挂单的生命周期。"""
    p = render_agent_prompt("g-1", "r", "tok123", "http://127.0.0.1:8000")
    assert "预览与挂单" in p and "挂单" in p and "60" in p


async def test_cli_log_command(tmp_path, monkeypatch):
    """P0-1：xq log 逐行打印全部着法并带 ⏱ 时间行。"""
    import asyncio
    import socket

    import httpx
    import uvicorn
    from typer.testing import CliRunner
    from xqserver.app import create_app

    monkeypatch.chdir(tmp_path)
    app = create_app(data_dir=str(tmp_path / "data"))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                           log_level="error"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    try:
        url = f"http://127.0.0.1:{port}"
        async with httpx.AsyncClient(base_url=url) as c:
            g = (await c.post("/api/games",
                              json={"red_name": "A", "black_name": "B"})).json()
            gid = g["game_id"]
            for t in (g["red_token"], g["black_token"]):
                await c.post(f"/api/games/{gid}/join", headers={"X-Seat-Token": t})
            h = {"X-Seat-Token": g["red_token"]}
            pv = (await c.post(f"/api/games/{gid}/move",
                               json={"move": "h2e2"}, headers=h)).json()
            await c.post(f"/api/games/{gid}/commit",
                         json={"pending_id": pv["pending_id"]}, headers=h)
        from xqcli.main import app as cli_app
        runner = CliRunner()
        # CLI 是同步阻塞调用，必须放线程里跑，才不会卡死同事件循环上的裁判服务器
        r = await asyncio.to_thread(
            runner.invoke, cli_app,
            ["join", gid, "red", g["red_token"], "--server", url])
        assert r.exit_code == 0, r.output
        r = await asyncio.to_thread(runner.invoke, cli_app, ["log"])
        assert r.exit_code == 0, r.output
        assert "1. 红 炮二平五 (h2e2)" in r.output
        assert "⏱" in r.output
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def _serve_cli(client):
    """P1-7/P1-8：真实服务器 + 已入座红方的 CLI 测试环境。"""
    import asyncio
    import socket

    import httpx
    import uvicorn
    from typer.testing import CliRunner
    from xqcli.main import app as cli_app
    from xqserver.app import create_app

    app = create_app(data_dir=str(client / "data"))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                           log_level="error"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    url = f"http://127.0.0.1:{port}"
    try:
        async with httpx.AsyncClient(base_url=url) as c:
            g = (await c.post("/api/games",
                              json={"red_name": "A", "black_name": "B"})).json()
            for t in (g["red_token"], g["black_token"]):
                await c.post(f"/api/games/{g['game_id']}/join",
                             headers={"X-Seat-Token": t})
        runner = CliRunner()
        join = await asyncio.to_thread(
            runner.invoke, cli_app,
            ["join", g["game_id"], "red", g["red_token"], "--server", url])
        assert join.exit_code == 0, join.output
        yield runner, cli_app
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def test_cli_json_flags(tmp_path, monkeypatch):
    """P1-7：state/wait/log/legal-moves 的 --json 输出可被 json.loads 解析。"""
    import json

    monkeypatch.chdir(tmp_path)
    async for runner, cli_app in _serve_cli(tmp_path):
        r = await __import__("asyncio").to_thread(runner.invoke, cli_app,
                                                  ["state", "--json"])
        assert r.exit_code == 0, r.output
        st = json.loads(r.output)
        assert st["your_color"] == "r" and "ascii" in st and "fen" in st

        r = await __import__("asyncio").to_thread(runner.invoke, cli_app,
                                                  ["wait", "--json"])
        assert json.loads(r.output)["your_turn"] is True

        r = await __import__("asyncio").to_thread(runner.invoke, cli_app,
                                                  ["legal-moves", "--json"])
        moves = json.loads(r.output)
        assert any(m["iccs"] == "h2e2" for m in moves)

        r = await __import__("asyncio").to_thread(
            runner.invoke, cli_app, ["play", "h2e2", "--expect", "炮二平五"])
        assert r.exit_code == 0, r.output
        r = await __import__("asyncio").to_thread(runner.invoke, cli_app,
                                                  ["log", "--json"])
        log_moves = json.loads(r.output)
        assert log_moves[0]["iccs"] == "h2e2"


async def test_cli_legal_moves_grouped_and_from(tmp_path, monkeypatch):
    """P1-8：legal-moves 默认分组输出，--from 过滤指定起点。"""
    monkeypatch.chdir(tmp_path)
    import asyncio
    async for runner, cli_app in _serve_cli(tmp_path):
        r = await asyncio.to_thread(runner.invoke, cli_app, ["legal-moves"])
        assert r.exit_code == 0, r.output
        assert "炮(h2):" in r.output and "炮二平五 h2e2" in r.output
        assert "马(b0):" in r.output
        r = await asyncio.to_thread(runner.invoke, cli_app,
                                    ["legal-moves", "--from", "b0"])
        assert "马(b0):" in r.output and "炮(h2):" not in r.output


async def test_cli_play_expect_mismatch(tmp_path, monkeypatch):
    """P1-5：CLI play --expect 不符时 exit 1，并提示实际着法、未落子。"""
    monkeypatch.chdir(tmp_path)
    import asyncio
    async for runner, cli_app in _serve_cli(tmp_path):
        r = await asyncio.to_thread(
            runner.invoke, cli_app, ["play", "h2e2", "--expect", "炮八平五"])
        assert r.exit_code == 1
        assert "实际将是 炮二平五" in r.output and "未落子" in r.output
        r = await asyncio.to_thread(runner.invoke, cli_app, ["log"])
        assert "炮二平五" not in r.output      # 确实没落子


def test_prompt_note_counts_against_clock():
    """P1-9：PROMPT 写明 xq note 计入自己的时钟。"""
    p = render_agent_prompt("g-1", "r", "tok123", "http://127.0.0.1:8000")
    assert "xq note" in p and "计入你的时钟" in p


def test_prompt_engine_and_source_ban():
    """P1-9：PROMPT 写明引擎禁令与平台源代码禁令。"""
    p = render_agent_prompt("g-1", "r", "tok123", "http://127.0.0.1:8000")
    assert "象棋引擎" in p
    assert "源代码" in p and "缺陷" in p
    assert "自身推理" in p
