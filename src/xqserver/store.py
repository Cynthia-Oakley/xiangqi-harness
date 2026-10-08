"""对局注册表 + JSONL 棋谱持久化。"""

import json
import os
import secrets
import time
from pathlib import Path

from xqserver.game import Game


class Store:
    def __init__(self, data_dir="data"):
        self.data_dir = Path(data_dir)
        (self.data_dir / "games").mkdir(parents=True, exist_ok=True)
        os.chmod(self.data_dir, 0o700)
        self.games: dict[str, Game] = {}

    def create(self, red_name, black_name, **clock_cfg) -> Game:
        gid = "g-" + time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
        g = Game(gid, red_name, black_name, **clock_cfg)
        self.games[gid] = g
        return g

    def get(self, gid) -> Game:
        if gid not in self.games:
            raise KeyError(gid)
        return self.games[gid]

    def append_record(self, game: Game, record: dict):
        path = self.data_dir / "games" / f"{game.id}.jsonl"
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        os.chmod(path, 0o600)
