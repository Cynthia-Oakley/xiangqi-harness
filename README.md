# Xiangqi Arena · 象棋竞技场

一个本地运行的中国象棋对弈平台，让**人类**与 **AI Agent Harness**（Kimi Code、DeepSeek、豆包等命令行智能体）同台对弈：

- **Agent vs Agent**：两个 Agent 通过 `xq` CLI 对弈，你在浏览器实时观战棋盘与双方的思考流。
- **人 vs Agent**：你在浏览器点击棋盘走子，Agent 用命令行应战。
- **人 vs 人**：双方都走浏览器对弈页。

裁判服务器是唯一的规则与状态权威：轮流走子、走法合法性（蹩马腿 / 塞象眼 / 炮架 / 白脸将）、将军应将、将死 / 困毙判定、双钟计时（局时 + 步时 + 每步加秒）全部由服务端裁决，客户端无法绕过。

## 快速开始

```bash
pip install -e .          # Python 3.11+
xq arena 红方名 黑方名     # 一条命令：拉起裁判服务器、建对局、生成双方 PROMPT.md
```

arena 命令会为每个 Agent 建好工作目录（含 `PROMPT.md` 和预入座凭证），把 `PROMPT.md` 喂给对应 harness（工作目录设为该目录）即可开赛。输出里还会给出浏览器观战链接与网页控制台地址（可终止对局/停止服务器，无需记命令）。

人机对局：

```bash
xq arena 我 黑方名 --human red     # 你执红，浏览器打开输出的链接入座
```

辅助模式（双方对等，棋谱会标记）：

```bash
xq arena 红方名 黑方名 --assist
```

## 对 Agent 友好的设计

- **纯文本交互**：ICCS 坐标（`h2e2`）+ FEN + 带坐标尺的 ASCII 棋盘 + 双方棋子坐标清单；不需要读图也能下。
- **棋盘截图**：`wait`/`state` 自动刷新 `board.png`（约 240 tokens，按你的视角渲染，自己的棋子在下方），供支持看图的模型校准局面。
- **防指错格子**：`xq play h2e2 --expect "炮二平五"` 原子走子——服务器核对中文着法一致才落子；也有 `move`（预览走后棋盘）→ `commit` 两段式。
- **时间感知**：每条命令返回都带 `⏱ 时间状态`（本步已用 / 局时还剩 / 对方上一步用时）。
- **复盘**：`xq log` 输出整局着法（ICCS + 中文 + 用时）。
- **辅助模式**（`--assist` 开局）：`xq inspect <格>` 落点攻防清点（炮架/马腿/象眼单独标注）、落子后自动「走后自检」（新送子 / 新攻击线 / 新牵制）、`xq translate` ICCS↔中文记谱互译。它是感知辅助而非形势评估，人类网页端有同款面板。

## 对人类友好的设计

- 浏览器对弈页：点选棋子显示合法落点，预览确认（中文着法 + 吃子/将军提示）后落子。
- 观战页：实时棋盘 + 双方思考流（Agent 的 `xq note` 仅观众可见，对手看不到）。
- 网页控制台：查看/终止对局、停止服务器，处理后台进程不用碰命令行。
- 服务器空闲 60 分钟自动关机。

## 架构

```
浏览器（对弈/观战/控制台）          Agent A CLI          Agent B CLI
        │ HTTP/SSE                    │ HTTP                │ HTTP
        └──────────────►  xq-server（FastAPI，唯一裁判）◄──────────┘
                            ├─ 规则核心（自研 Python 包，可独立测试）
                            ├─ 两阶段提交 / 座位令牌 / 信息隔离
                            ├─ 双钟计时与超时结算
                            └─ SSE 观战广播 / PNG 棋盘渲染
```

```
src/xiangqi/    规则引擎：board / moves / rules / notation / attack / textboard
src/xqserver/   裁判服务器：app / game / store / board_image / static(网页)
src/xqcli/      Agent 命令行：main（typer 薄客户端）/ arena / prompt_template
tests/          pytest，约 140 个用例
```

运行测试：`pytest`（需先 `pip install -e ".[dev]"`）。

## 致谢与引用

- **《象棋竞赛规则（2020）》**（中国象棋协会审定）——走法、将军/应将、计时与停着等裁判规则的依据。
- **ICCS 坐标记谱**——源自互联网中国象棋服务器（Internet Chinese Chess Server）的通用坐标记谱约定，本项目用作 Agent 接口与内部表示。
- **[Pikafish](https://github.com/official-pikafish/Pikafish)**（GPLv3）——开源中国象棋引擎（Stockfish 衍生）。本项目设计文档中的「可选引擎对手 / 局面分析」模块以其 UCI 协议接口为集成方向（尚未实现）；规则核心与裁判逻辑均为本项目自研，未使用其代码。
- 开源依赖：[FastAPI](https://github.com/fastapi/fastapi)、[Uvicorn](https://github.com/encode/uvicorn)、[Typer](https://github.com/fastapi/typer)、[httpx](https://github.com/encode/httpx)、[Pillow](https://github.com/python-pillow/Pillow)、[pytest](https://github.com/pytest-dev/pytest)——感谢这些项目及其维护者。
- 棋盘 ASCII / SVG 渲染遵循中国象棋传统谱式（红方简体、黑方繁体、楚河汉界）。

## 许可证

[MIT](LICENSE) © wushuyue

> 注：本仓库规则核心为自研实现；若未来直接集成 Pikafish 二进制，请留意其 GPLv3 许可义务。
