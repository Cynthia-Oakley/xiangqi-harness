"""xq CLI：Agent Harness 与裁判服务器交互的命令行。"""

import json
import os
from pathlib import Path

import httpx
import typer

from xiangqi.textboard import GLYPHS

app = typer.Typer(help="Xiangqi Harness CLI")
SEAT_FILE = Path(".xqseat.json")
DEFAULT_SERVER = "http://127.0.0.1:8000"


def save_seat(seat: dict):
    SEAT_FILE.write_text(json.dumps(seat, ensure_ascii=False, indent=2))
    os.chmod(SEAT_FILE, 0o600)


def load_seat() -> dict:
    if not SEAT_FILE.exists():
        typer.echo("未入座：先运行 xq join", err=True)
        raise typer.Exit(1)
    return json.loads(SEAT_FILE.read_text())


def client(seat) -> httpx.Client:
    return httpx.Client(base_url=seat["server"],
                        headers={"X-Seat-Token": seat["token"]}, timeout=60,
                        trust_env=False)     # 裁判服务器在本机，绕开系统代理


def bare_client(server) -> httpx.Client:
    return httpx.Client(base_url=server, timeout=60, trust_env=False)


def show(resp: httpx.Response):
    if resp.status_code >= 400:
        try:
            msg = resp.json().get("error", resp.text)
        except Exception:
            msg = resp.text
        typer.echo(f"错误({resp.status_code}): {msg}", err=True)
        raise typer.Exit(1)
    return resp.json()


def echo_time(d: dict):
    if d.get("time_status"):
        typer.echo("⏱ " + d["time_status"])


def echo_sandbox(d: dict):
    sb = d.get("sandbox")
    if not sb:
        return
    n_sims = len(sb.get("sims") or {})
    sim_txt = f"，存活分支 {n_sims}" if n_sims else ""
    typer.echo(f"◆ 沙盒配额: 剩 {sb['budget_left']}/{sb['budget_total']} 节点{sim_txt}")


def _print_line_steps(steps):
    for s in steps:
        who = "红" if s["color"] == "r" else "黑"
        if not s["legal"]:
            typer.echo(f"  {s['ply']}. {who} {s['iccs']} ✗ 非法: {s['reason']}（推演中止）")
            continue
        extra = []
        if s.get("captured"):
            extra.append(f"吃{GLYPHS[s['captured']]}")
        if s.get("gives_check"):
            extra.append("将军")
        if s.get("terminal"):
            extra.append(s["terminal"])
        typer.echo(f"  {s['ply']}. {who} {s['chinese']} ({s['iccs']})"
                   + ("　" + "，".join(extra) if extra else ""))


def print_state(st: dict):
    typer.echo(st["ascii"])
    if st.get("pieces"):
        typer.echo(st["pieces"])
    typer.echo(f"轮到你: {st['your_turn']}  时钟: {st['clocks']}")
    if st.get("time_status"):
        typer.echo("⏱ " + st["time_status"])
    lm = st.get("last_move")
    if lm:
        who = "红" if lm["color"] == "r" else "黑"
        cap = f" 吃: {lm['captured_glyph']}" if lm.get("captured_glyph") else ""
        typer.echo(f"上一步: {who} {lm['chinese']} ({lm['iccs']}) 用时 {int(lm['elapsed'])}s{cap}")
    echo_sandbox(st)
    if st.get("your_color") == "b":
        typer.echo("路数对照(中文记谱): 你的黑方 N路 = 第N列字母（a=1路 b=2路 … i=9路）；"
                   "红方相反（i=一路 … a=九路）")
    if st.get("in_check"):
        typer.echo("!! 将军：你正被将军，本步必须应将 !!")
    if st["result"]:
        typer.echo(f"对局结束: {st['result']} ({st['result_reason']})")
    typer.echo(f"FEN: {st['fen']}")


def print_board_after(d: dict, headline: str):
    """move/play/commit 返回后的增强视图：棋盘 + 棋子清单 + 一行结论。"""
    if d.get("ascii"):
        typer.echo(d["ascii"])
    if d.get("pieces"):
        typer.echo(d["pieces"])
    typer.echo(headline)
    if d.get("self_check"):            # 辅助模式：走后自检
        typer.echo("── 走后自检 ──")
        typer.echo(d["self_check"])


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000, data_dir: str = "data",
          idle_minutes: int = 60):
    """启动裁判服务器。--idle-minutes：无活动多少分钟后自动关机（0=不自动关机）。"""
    try:    # 单实例防护：端口上已有健康的裁判服务器则直接复用，不再起重复进程
        httpx.get(f"http://{host}:{port}/api/rules", timeout=1,
                  trust_env=False).raise_for_status()
        typer.echo(f"裁判服务器已在 {host}:{port} 运行，无需重复启动。")
        raise typer.Exit(0)
    except httpx.HTTPError:
        pass
    import uvicorn
    from xqserver.app import create_app
    app = create_app(data_dir, idle_minutes=idle_minutes)
    key = (Path(data_dir) / "admin_key.txt").read_text().strip()
    typer.echo(f"控制台: http://{host}:{port}/admin?key={key}"
               "（收藏此地址——可查看/终止对局、停止服务器，无需命令行）")
    uvicorn.run(app, host=host, port=port)


@app.command()
def arena(red_name: str, black_name: str, port: int = 8000,
          base_dir: str = "arena", data_dir: str = "data",
          time_limit: str = "15m", step_time: str = "3m", increment: int = 20,
          step_timeout: str = "pass",
          human: str = typer.Option(None, "--human",
                                    help="人类座位: red 或 black；该方在浏览器对弈，不建 Agent 目录"),
          assist: bool = typer.Option(False, "--assist",
                                      help="辅助模式：攻防清点 xq inspect + 走后自检 + xq translate（人类网页端同样有）"),
          sandbox: bool = typer.Option(False, "--sandbox",
                                       help="多步推演沙盒：xq probe / xq sim，每回合 30 节点配额，双方对等")):
    """一条命令搭好一场 Agent 对局：自动拉起服务器、建对局、建目录、预入座。
    --step-timeout pass=步时超时记停着一手（默认）；lose=超时即判负（正式规则）。
    --human red/black=该方由人类在浏览器 /play 页面入座对弈。
    --assist=开启辅助模式（攻防清点/走后自检/记谱互译），双方对等。
    --sandbox=开启多步推演沙盒（probe/sim 假设推演，只给事实不给评估），双方对等。"""
    import sys
    from xqcli.arena import ensure_server, setup_arena
    if human is not None:
        human = human.lower()
        if human not in ("red", "black"):
            typer.echo(f"--human 只能是 red 或 black: {human!r}", err=True)
            raise typer.Exit(1)
    xq_cmd = str(Path(sys.argv[0]).resolve())
    server = f"http://127.0.0.1:{port}"
    reused = ensure_server(server, xq_cmd, port, data_dir)
    typer.echo("裁判服务器：" + ("已在运行，直接复用" if reused else f"已后台拉起（日志 {data_dir}/serve.log）"))
    info = setup_arena(red_name, black_name, server, base_dir, xq_cmd,
                       time_limit, step_time, increment, step_timeout,
                       human=human, assist=assist, sandbox=sandbox)
    typer.echo(f"对局 {info['game_id']} 已创建，双方已预入座：")
    for cn, name in (("red", red_name), ("black", black_name)):
        if info["dirs"].get(cn):
            typer.echo(f"  {cn}方({name}) 工作目录: {info['dirs'][cn]}")
        else:
            typer.echo(f"  {cn}方({name}) 浏览器打开: {info['play_url']}")
    if human:
        typer.echo("你入座（打开链接）且对方 Agent join 后计时开始。")
    else:
        typer.echo("把各目录里的 PROMPT.md 喂给对应 harness（工作目录设为该目录）即可开赛。")
    if assist:
        typer.echo("辅助模式已开启：xq inspect <格> 攻防清点、play/commit 自动走后自检、"
                   "xq translate 记谱互译（人类网页端同样可用）。")
    if sandbox:
        typer.echo("沙盒推演已开启：xq probe <走法…> 无状态推演、xq sim start/push/pop/end "
                   "有状态分支，每回合 30 节点配额（双方对等）。")
    typer.echo(f"浏览器观战: {server}/watch/{info['game_id']}")
    typer.echo(f"文本观战: curl -N {info['spectate']}")
    key_file = Path(data_dir) / "admin_key.txt"
    if key_file.exists():
        typer.echo(f"控制台: {server}/admin?key={key_file.read_text().strip()}"
                   "（网页上即可终止对局/停止服务器）")


@app.command()
def new(red_name: str, black_name: str, server: str = DEFAULT_SERVER,
        time_limit: str = "15m", step_time: str = "3m", increment: int = 20,
        step_timeout: str = "pass", assist: bool = typer.Option(False, "--assist"),
        sandbox: bool = typer.Option(False, "--sandbox")):
    with bare_client(server) as c:
        r = c.post("/api/games", json={
            "red_name": red_name, "black_name": black_name,
            "base_seconds": parse_duration(time_limit),
            "step_seconds": parse_duration(step_time),
            "increment": increment,
            "step_timeout": step_timeout,
            "assist": assist,
            "sandbox_budget": 30 if sandbox else 0,
        })
    g = show(r)
    from xqcli.prompt_template import render_agent_prompt
    seats = Path("seats")
    seats.mkdir(exist_ok=True)
    for color, cn in (("r", "red"), ("b", "black")):
        p = render_agent_prompt(g["game_id"], color, g[f"{cn}_token"], server,
                                step_time, step_timeout=step_timeout,
                                time_limit=time_limit, increment=increment,
                                assist=assist, sandbox=sandbox)
        md = seats / f"{g['game_id']}-{cn}.md"
        md.write_text(p, encoding="utf-8")
        os.chmod(md, 0o600)               # 提示词文件含座位令牌
    typer.echo(json.dumps(g, ensure_ascii=False, indent=2))
    typer.echo(f"提示词已写入 seats/ 目录；浏览器观战: {server}/watch/{g['game_id']}")
    typer.echo(f"文本观战: curl -N {server}/api/games/{g['game_id']}/spectate")


def parse_duration(s: str) -> float:
    """'15m' → 900，'45s'/'45' → 45。"""
    if s.endswith("m"):
        return float(s[:-1]) * 60
    return float(s.rstrip("s"))


@app.command()
def join(game_id: str, color: str, token: str, server: str = DEFAULT_SERVER):
    if color.lower().startswith("r"):
        c = "r"
    elif color.lower().startswith("b"):
        c = "b"
    else:
        typer.echo(f"颜色只能是 red 或 black: {color!r}", err=True)
        raise typer.Exit(1)
    save_seat({"server": server, "game_id": game_id, "color": c, "token": token})
    try:
        with bare_client(server) as bc:
            r = bc.post(f"/api/games/{game_id}/join",
                        headers={"X-Seat-Token": token})
        resp = show(r)
    except httpx.TransportError:
        typer.echo(f"已入座 {color}（仅本地）。警告：无法连接裁判服务器 {server}，"
                   "对局计时不会开始；服务器可用后请重新运行本命令。")
        return
    if resp["started"]:
        typer.echo(f"已入座 {color}，双方到齐——对局开始，计时启动。")
    else:
        typer.echo(f"已入座 {color}，等待 {resp['waiting_for']} 入座；"
                   "双方入座后计时才开始。")


def refresh_board_image(seat) -> bool:
    """静默把当前棋盘截图刷新为 ./board.png；失败不致命。"""
    try:
        with client(seat) as c:
            r = c.get(f"/api/games/{seat['game_id']}/board.png")
        if r.status_code < 400:
            Path("board.png").write_bytes(r.content)
            return True
    except Exception:
        pass
    return False


@app.command()
def state(json_out: bool = typer.Option(False, "--json"),
          no_image: bool = typer.Option(False, "--no-image",
                                        help="不自动刷新 board.png")):
    seat = load_seat()
    with client(seat) as c:
        st = show(c.get(f"/api/games/{seat['game_id']}/state"))
    if json_out:
        typer.echo(json.dumps(st, ensure_ascii=False))
        return
    print_state(st)
    if not no_image and refresh_board_image(seat):
        typer.echo("♟ 棋盘截图已刷新: board.png（先打开它核对局面，再决定走法）")


@app.command(name="legal-moves")
def legal_moves_cmd(from_square: str | None = typer.Option(None, "--from"),
                    json_out: bool = typer.Option(False, "--json")):
    seat = load_seat()
    with client(seat) as c:
        d = show(c.get(f"/api/games/{seat['game_id']}/moves"))
    moves = d["moves"]
    if from_square:
        moves = [m for m in moves if m["iccs"].startswith(from_square)]
    if json_out:
        typer.echo(json.dumps(moves, ensure_ascii=False))
        return
    if not moves:
        typer.echo("（未轮到你或对局结束）")
    else:
        groups: dict[str, list[str]] = {}
        for m in moves:
            label = f"{m['chinese'][0]}({m['iccs'][:2]})"
            suffix = ("x" if m["capture"] else "") + ("+" if m["gives_check"] else "")
            groups.setdefault(label, []).append(f"{m['chinese']} {m['iccs']}{suffix}")
        for label, items in groups.items():
            typer.echo(f"{label}: " + " / ".join(items))
    echo_time(d)


@app.command()
def log(json_out: bool = typer.Option(False, "--json")):
    seat = load_seat()
    with client(seat) as c:
        d = show(c.get(f"/api/games/{seat['game_id']}/log"))
    if json_out:
        typer.echo(json.dumps(d["moves"], ensure_ascii=False))
        return
    for m in d["moves"]:
        who = "红" if m["color"] == "r" else "黑"
        cap = ""
        if m.get("captured"):
            cap = f" 吃{GLYPHS[m['captured']]}"
        typer.echo(f"{m['ply']}. {who} {m['chinese']} ({m['iccs']}) {int(m['elapsed'])}s{cap}")
    echo_time(d)


@app.command(name="board-image")
def board_image(out: str = "board.png"):
    """可选：把当前棋盘保存为 PNG 截图，支持看图的模型可用它校准局面。"""
    seat = load_seat()
    with client(seat) as c:
        r = c.get(f"/api/games/{seat['game_id']}/board.png")
    if r.status_code >= 400:
        try:
            msg = r.json().get("error", r.text)
        except Exception:
            msg = r.text
        typer.echo(f"错误({r.status_code}): {msg}", err=True)
        raise typer.Exit(1)
    Path(out).write_bytes(r.content)
    typer.echo(f"已保存 {out}（400x444 PNG，{len(r.content) / 1024:.0f}KB，"
               f"视觉预算约 240 tokens；按你的视角渲染，你的棋子在下方）。"
               "用你 harness 的看图能力打开它核对局面。")


@app.command(name="history")
def history_cmd(json_out: bool = typer.Option(False, "--json")):
    """log 的别名：整局着法记录（ICCS + 中文 + 用时）。"""
    log(json_out=json_out)


@app.command()
def inspect(square: str):
    """辅助模式：落点攻防清点。如 xq inspect e4 —— 列出双方谁能攻击/守护该格。"""
    seat = load_seat()
    with client(seat) as c:
        d = show(c.get(f"/api/games/{seat['game_id']}/inspect/{square}"))
    typer.echo(d["report"])


def print_probe_result(d: dict):
    """probe 返回：变化线每步事实 + 终局棋盘视图（非正式局面）。"""
    _print_line_steps(d["steps"])
    typer.echo(d["ascii"])
    if d.get("pieces"):
        typer.echo(d["pieces"])
    who = "红" if d["turn"] == "r" else "黑"
    typer.echo(f"── 推演线终局（假设局面，非正式对局）：轮到{who}方 ──")
    if d.get("terminal"):
        typer.echo(f"终端事实: {d['terminal']}")
    echo_sandbox(d)
    echo_time(d)
    typer.echo(f"FEN: {d['fen']}")


def print_sim_view(d: dict, headline: str):
    """sim 分支视图：分支信息 + 当前假设局面。"""
    if d.get("note"):
        typer.echo(d["note"])
    if d.get("last"):
        _print_line_steps([{**d["last"], "ply": d["depth"]}])
    typer.echo(d["ascii"])
    if d.get("pieces"):
        typer.echo(d["pieces"])
    who = "红" if d["turn"] == "r" else "黑"
    typer.echo(f"{headline}（深度 {d['depth']}，轮到{who}方假设走子）")
    if d.get("terminal"):
        typer.echo(f"终端事实: {d['terminal']}（正式对局不受影响）")
    echo_sandbox(d)
    echo_time(d)
    typer.echo(f"FEN: {d['fen']}")


@app.command()
def probe(moves: list[str],
          json_out: bool = typer.Option(False, "--json")):
    """无状态多步推演：从当前局面依次假设双方走子，一次返回每步事实与终局。
    如 xq probe h2e2 h9g7 —— 红炮二平五、黑马8进7 之后的局面。每步占 1 个沙盒节点。"""
    seat = load_seat()
    with client(seat) as c:
        d = show(c.post(f"/api/games/{seat['game_id']}/probe",
                        json={"moves": moves}))
    if json_out:
        typer.echo(json.dumps(d, ensure_ascii=False))
        return
    print_probe_result(d)


sim_app = typer.Typer(help="沙盒推演分支：从当前局面分叉，逐子假设（不影响正式对局）")
app.add_typer(sim_app, name="sim")


def _active_sim(seat) -> str:
    sid = seat.get("sim_id")
    if not sid:
        typer.echo("没有活动分支：先运行 xq sim start", err=True)
        raise typer.Exit(1)
    return sid


@sim_app.command("start")
def sim_start(json_out: bool = typer.Option(False, "--json")):
    """从当前正式局面分叉一个沙盒分支（最多 2 个；本身不占节点）。"""
    seat = load_seat()
    with client(seat) as c:
        d = show(c.post(f"/api/games/{seat['game_id']}/sim/start"))
    seat["sim_id"] = d["sim_id"]
    save_seat(seat)
    if json_out:
        typer.echo(json.dumps(d, ensure_ascii=False))
        return
    print_sim_view(d, f"沙盒分支 {d['sim_id']}")
    typer.echo("接着：xq sim push h2e2 假设一步 / xq sim pop 撤回一步 / xq sim end 关闭分支")


@sim_app.command("push")
def sim_push(iccs: str, json_out: bool = typer.Option(False, "--json")):
    """在当前分支假设走一步（占 1 个沙盒节点；走子方随分支自动轮换）。"""
    seat = load_seat()
    with client(seat) as c:
        d = show(c.post(
            f"/api/games/{seat['game_id']}/sim/{_active_sim(seat)}/push",
            json={"move": iccs}))
    if json_out:
        typer.echo(json.dumps(d, ensure_ascii=False))
        return
    print_sim_view(d, f"分支 {d['sim_id']}")


@sim_app.command("pop")
def sim_pop(json_out: bool = typer.Option(False, "--json")):
    """撤回当前分支最近一步假设，换一条变化线。"""
    seat = load_seat()
    with client(seat) as c:
        d = show(c.post(
            f"/api/games/{seat['game_id']}/sim/{_active_sim(seat)}/pop"))
    if json_out:
        typer.echo(json.dumps(d, ensure_ascii=False))
        return
    print_sim_view(d, f"分支 {d['sim_id']}")


@sim_app.command("board")
def sim_board(json_out: bool = typer.Option(False, "--json")):
    """查看当前分支的假设局面。"""
    seat = load_seat()
    with client(seat) as c:
        d = show(c.get(f"/api/games/{seat['game_id']}/sim/{_active_sim(seat)}"))
    if json_out:
        typer.echo(json.dumps(d, ensure_ascii=False))
        return
    print_sim_view(d, f"分支 {d['sim_id']}")


@sim_app.command("end")
def sim_end():
    """关闭当前沙盒分支（正式落子后所有分支也会自动失效）。"""
    seat = load_seat()
    with client(seat) as c:
        d = show(c.post(
            f"/api/games/{seat['game_id']}/sim/{_active_sim(seat)}/end"))
    seat.pop("sim_id", None)
    save_seat(seat)
    typer.echo(f"已关闭分支 {d['ended']}")
    echo_sandbox(d)
    echo_time(d)


@app.command()
def translate(expr: str):
    """ICCS ↔ 中文记谱互译。如 xq translate h2e2 / xq translate 炮二平五。"""
    from xiangqi.board import Board
    from xiangqi.notation import from_chinese, is_valid_iccs, to_chinese
    seat = load_seat()
    with client(seat) as c:
        st = show(c.get(f"/api/games/{seat['game_id']}/state"))
    board = Board.from_fen(st["fen"])
    expr = expr.strip()
    if is_valid_iccs(expr):
        from xiangqi.board import iccs_to_rc
        fr, fc = iccs_to_rc(expr[:2])
        if board.grid[fr][fc] is None:
            typer.echo(f"错误: {expr[:2]} 格没有棋子", err=True)
            raise typer.Exit(1)
        typer.echo(f"{expr} = {to_chinese(board, expr)}"
                   f"（{'红方' if board.grid[fr][fc].isupper() else '黑方'}记谱）")
        return
    cands = from_chinese(board, expr)
    if not cands:
        typer.echo(f"「{expr}」在当前局面下没有匹配的走法（检查记谱与路数视角）", err=True)
        raise typer.Exit(1)
    if len(cands) == 1:
        typer.echo(f"{expr} = {cands[0]}")
    else:
        typer.echo(f"「{expr}」有多个匹配（记谱有歧义，需前/后等修饰区分）: "
                   + " ".join(cands))


@app.command()
def move(iccs: str, note: str | None = None,
         json_out: bool = typer.Option(False, "--json")):
    seat = load_seat()
    with client(seat) as c:
        pv = show(c.post(f"/api/games/{seat['game_id']}/move",
                         json={"move": iccs, "note": note}))
    if json_out:
        typer.echo(json.dumps(pv, ensure_ascii=False))
        return
    extra = []
    if pv.get("captured"):
        extra.append(f"吃 {pv['captured']}")
    if pv.get("gives_check"):
        extra.append("将军")
    print_board_after(pv, f"预览（未落子）: {pv['chinese']} ({pv['move']})"
                          + ("　" + "，".join(extra) if extra else "")
                          + f"　挂单 {pv['pending_id']}，{int(pv['expires_in'])}s 内有效")
    typer.echo(f"核对无误后: xq commit {pv['pending_id']}；反悔: xq cancel {pv['pending_id']}")
    echo_time(pv)
    typer.echo(f"FEN: {pv['fen_after']}")


@app.command()
def play(iccs: str, expect: str | None = typer.Option(None, "--expect"),
         note: str | None = typer.Option(None, "--note"),
         json_out: bool = typer.Option(False, "--json")):
    """原子走子：--expect 先填你自己推出的中文着法，服务器核对一致才真正落子。"""
    seat = load_seat()
    with client(seat) as c:
        resp = c.post(f"/api/games/{seat['game_id']}/play",
                      json={"move": iccs, "expect": expect, "note": note})
    if resp.status_code >= 400:
        try:
            body = resp.json()
        except Exception:
            body = {}
        typer.echo(f"错误({resp.status_code}): {body.get('error', resp.text)}", err=True)
        raise typer.Exit(1)
    d = resp.json()
    if json_out:
        typer.echo(json.dumps(d, ensure_ascii=False))
        return
    print_board_after(d, f"✔ 已落子: {d['chinese']} ({d['iccs']}) 用时 {int(d['elapsed'])}s")
    if d.get("result"):
        typer.echo(f"对局结束: {d['result']} ({d.get('result_reason')})")
    echo_time(d)
    typer.echo(f"FEN: {d['fen']}")


@app.command()
def commit(pending_id: str, json_out: bool = typer.Option(False, "--json")):
    seat = load_seat()
    with client(seat) as c:
        resp = show(c.post(f"/api/games/{seat['game_id']}/commit",
                           json={"pending_id": pending_id}))
    if json_out:
        typer.echo(json.dumps(resp, ensure_ascii=False))
        return
    print_board_after(resp, f"✔ 已落子: {resp['chinese']} ({resp['iccs']}) "
                            f"用时 {int(resp['elapsed'])}s")
    if resp.get("result"):
        typer.echo(f"对局结束: {resp['result']} ({resp.get('result_reason')})")
    echo_time(resp)
    typer.echo(f"FEN: {resp['fen']}")


@app.command()
def cancel(pending_id: str):
    seat = load_seat()
    with client(seat) as c:
        resp = show(c.post(f"/api/games/{seat['game_id']}/cancel",
                           json={"pending_id": pending_id}))
    typer.echo("已取消")
    echo_time(resp)


@app.command()
def note(text: str):
    seat = load_seat()
    with client(seat) as c:
        resp = show(c.post(f"/api/games/{seat['game_id']}/note", json={"text": text}))
    typer.echo("已记录（仅观众可见）")
    echo_time(resp)


@app.command()
def wait(json_out: bool = typer.Option(False, "--json"),
         no_image: bool = typer.Option(False, "--no-image",
                                       help="不自动刷新 board.png")):
    seat = load_seat()
    with client(seat) as c:
        st = show(c.get(f"/api/games/{seat['game_id']}/state"))
        while not st["your_turn"] and not st["result"]:
            st = show(c.get(f"/api/games/{seat['game_id']}/wait",
                            params={"ply": st["ply"]}))
    if json_out:
        typer.echo(json.dumps(st, ensure_ascii=False))
        return
    print_state(st)
    if not no_image and refresh_board_image(seat):
        typer.echo("♟ 棋盘截图已刷新: board.png（先打开它核对局面，再决定走法）")


@app.command()
def resign():
    seat = load_seat()
    with client(seat) as c:
        typer.echo(json.dumps(show(c.post(f"/api/games/{seat['game_id']}/resign"))))


@app.command(name="offer-draw")
def offer_draw():
    seat = load_seat()
    with client(seat) as c:
        resp = show(c.post(f"/api/games/{seat['game_id']}/offer-draw"))
    typer.echo("已提出和棋")
    echo_time(resp)


@app.command(name="accept-draw")
def accept_draw():
    seat = load_seat()
    with client(seat) as c:
        typer.echo(json.dumps(show(c.post(f"/api/games/{seat['game_id']}/accept-draw"))))


@app.command()
def rules(server: str = DEFAULT_SERVER):
    with bare_client(server) as c:
        typer.echo(c.get("/api/rules").json()["rules"])


@app.command()
def replay(game_id: str, data_dir: str = "data"):
    path = Path(data_dir) / "games" / f"{game_id}.jsonl"
    for line in path.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if rec.get("type") == "move":
            typer.echo(f"{rec['ply']:>3}. {'红' if rec['color'] == 'r' else '黑'} "
                       f"{rec['chinese']} ({rec['iccs']}) {rec['elapsed']}s")
        elif rec.get("type") == "note":
            typer.echo(f"      [思考·{'红' if rec['color'] == 'r' else '黑'}] {rec['text']}")
