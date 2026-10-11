"""一条命令搭好一场 Agent 对局：服务器、对局、工作目录、提示词、预入座。"""

import json
import os
import subprocess
import time
from pathlib import Path

import httpx

from xqcli.prompt_template import render_agent_prompt


def ensure_server(server: str, xq_cmd: str, port: int,
                  data_dir: str = "data") -> bool:
    """探测服务器；未运行则后台拉起。返回 True = 复用已在运行的实例。"""
    try:
        httpx.get(f"{server}/api/rules", timeout=2).raise_for_status()
        return True
    except Exception:
        pass
    log = Path(data_dir) / "serve.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "ab") as lf:
        subprocess.Popen([xq_cmd, "serve", "--port", str(port),
                          "--data-dir", data_dir],
                         stdout=lf, stderr=subprocess.STDOUT,
                         start_new_session=True)
    for _ in range(50):
        try:
            httpx.get(f"{server}/api/rules", timeout=1).raise_for_status()
            return False
        except Exception:
            time.sleep(0.2)
    raise RuntimeError(f"裁判服务器拉起失败，日志见 {log}")


def setup_arena(red_name: str, black_name: str, server: str,
                base_dir: str = "arena", xq_cmd: str = "xq",
                time_limit: str = "15m", step_time: str = "3m",
                increment: int = 10, step_timeout: str = "pass") -> dict:
    """创建对局 + 双方工作目录（PROMPT.md + 预入座 .xqseat.json）。"""
    from xqcli.main import parse_duration
    r = httpx.post(f"{server}/api/games", json={
        "red_name": red_name, "black_name": black_name,
        "base_seconds": parse_duration(time_limit),
        "step_seconds": parse_duration(step_time),
        "increment": increment,
        "step_timeout": step_timeout,
    }, timeout=10)
    r.raise_for_status()
    g = r.json()
    gid = g["game_id"]
    dirs = {}
    for color, cn in (("r", "red"), ("b", "black")):
        d = Path(base_dir) / f"{gid}-{cn}"
        d.mkdir(parents=True, exist_ok=True)
        prompt = render_agent_prompt(gid, color, g[f"{cn}_token"], server,
                                     step_time=step_time, xq_cmd=xq_cmd,
                                     step_timeout=step_timeout,
                                     time_limit=time_limit, increment=increment)
        md = d / "PROMPT.md"
        md.write_text(prompt, encoding="utf-8")
        os.chmod(md, 0o600)
        seat = {"server": server, "game_id": gid, "color": color,
                "token": g[f"{cn}_token"]}
        sf = d / ".xqseat.json"
        sf.write_text(json.dumps(seat, ensure_ascii=False, indent=2))
        os.chmod(sf, 0o600)
        dirs[cn] = str(d)
    return {"game_id": gid, "dirs": dirs, "spectate": f"{server}/api/games/{gid}/spectate"}
