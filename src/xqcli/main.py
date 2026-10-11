"""xq CLI：Agent Harness 与裁判服务器交互的命令行。"""

import json
import os
from pathlib import Path

import httpx
import typer

app = typer.Typer(help="Xiangqi Arena CLI")
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


def print_state(st: dict):
    typer.echo(st["ascii"])
    typer.echo(f"FEN: {st['fen']}")
    typer.echo(f"轮到你: {st['your_turn']}  时钟: {st['clocks']}")
    if st.get("time_status"):
        typer.echo("⏱ " + st["time_status"])
    lm = st.get("last_move")
    if lm:
        who = "红" if lm["color"] == "r" else "黑"
        typer.echo(f"上一步: {who} {lm['chinese']} ({lm['iccs']}) 用时 {int(lm['elapsed'])}s")
    if st.get("in_check"):
        typer.echo("!! 将军：你正被将军，本步必须应将 !!")
    if st["result"]:
        typer.echo(f"对局结束: {st['result']} ({st['result_reason']})")


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000, data_dir: str = "data"):
    import uvicorn
    from xqserver.app import create_app
    uvicorn.run(create_app(data_dir), host=host, port=port)


@app.command()
def arena(red_name: str, black_name: str, port: int = 8000,
          base_dir: str = "arena", data_dir: str = "data",
          time_limit: str = "15m", step_time: str = "3m", increment: int = 10,
          step_timeout: str = "pass",
          human: str = typer.Option(None, "--human",
                                    help="人类座位: red 或 black；该方在浏览器对弈，不建 Agent 目录")):
    """一条命令搭好一场 Agent 对局：自动拉起服务器、建对局、建目录、预入座。
    --step-timeout pass=步时超时记停着一手（默认）；lose=超时即判负（正式规则）。
    --human red/black=该方由人类在浏览器 /play 页面入座对弈。"""
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
                       human=human)
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
    typer.echo(f"浏览器观战: {server}/watch/{info['game_id']}")
    typer.echo(f"文本观战: curl -N {info['spectate']}")


@app.command()
def new(red_name: str, black_name: str, server: str = DEFAULT_SERVER,
        time_limit: str = "15m", step_time: str = "3m", increment: int = 10,
        step_timeout: str = "pass"):
    with bare_client(server) as c:
        r = c.post("/api/games", json={
            "red_name": red_name, "black_name": black_name,
            "base_seconds": parse_duration(time_limit),
            "step_seconds": parse_duration(step_time),
            "increment": increment,
            "step_timeout": step_timeout,
        })
    g = show(r)
    from xqcli.prompt_template import render_agent_prompt
    seats = Path("seats")
    seats.mkdir(exist_ok=True)
    for color, cn in (("r", "red"), ("b", "black")):
        p = render_agent_prompt(g["game_id"], color, g[f"{cn}_token"], server,
                                step_time, step_timeout=step_timeout,
                                time_limit=time_limit, increment=increment)
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


@app.command()
def state(json_out: bool = typer.Option(False, "--json")):
    seat = load_seat()
    with client(seat) as c:
        st = show(c.get(f"/api/games/{seat['game_id']}/state"))
    if json_out:
        typer.echo(json.dumps(st, ensure_ascii=False))
        return
    print_state(st)


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
        typer.echo(f"{m['ply']}. {who} {m['chinese']} ({m['iccs']}) {int(m['elapsed'])}s")
    echo_time(d)


@app.command()
def move(iccs: str, note: str | None = None):
    seat = load_seat()
    with client(seat) as c:
        pv = show(c.post(f"/api/games/{seat['game_id']}/move",
                         json={"move": iccs, "note": note}))
    typer.echo(json.dumps(pv, ensure_ascii=False, indent=2))
    typer.echo("核对无误后: xq commit " + pv["pending_id"])
    echo_time(pv)


@app.command()
def play(iccs: str, expect: str | None = typer.Option(None, "--expect"),
         note: str | None = typer.Option(None, "--note")):
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
    typer.echo(json.dumps(d, ensure_ascii=False, indent=2))
    echo_time(d)


@app.command()
def commit(pending_id: str):
    seat = load_seat()
    with client(seat) as c:
        resp = show(c.post(f"/api/games/{seat['game_id']}/commit",
                           json={"pending_id": pending_id}))
    typer.echo(json.dumps(resp, ensure_ascii=False, indent=2))
    echo_time(resp)


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
def wait(json_out: bool = typer.Option(False, "--json")):
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
