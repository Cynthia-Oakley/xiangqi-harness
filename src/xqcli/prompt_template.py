"""生成喂给 Agent Harness 的提示词。"""

RED_GUIDE = "你是红方，先行。你的棋子是大写（R N B A K C P = 车马相仕帅炮兵）。"
BLACK_GUIDE = "你是黑方，后行。你的棋子是小写（r n b a k c p = 车马象士将炮卒）。"

TEMPLATE = """# 中国象棋对弈任务

{side_guide}

## 你的身份
- 对局 ID: {game_id}
- 座位令牌: {token}
- 裁判服务器: {server}
{xq_note}
## 第一步：入座
```bash
{xq_cmd} join {game_id} {color} {token} --server {server}
```
计时从**双方入座**后才开始，开局快慢不影响你的用时；入座后若提示等待对方，直接 `xq wait` 即可。

## 对弈循环（严格遵守）
1. `xq wait` —— 阻塞等待轮到你。输出里 `轮到你: True` 才轮到你（含 ASCII 棋盘 + FEN + 剩余时间 + 对方上一步；若提示「将军」表示你正被将军，本步必须应将）。
2. 思考，并用 `xq note "你的思考"` 记录（只有观众能看到，对手看不到）。
3. `xq play <走法> --expect "中文着法"` —— 原子走子。**先自己想出这步的中文记谱**填进 `--expect`，
   服务器核对一致才真正落子；不符会拒绝、不落子并告诉你实际着法。防的就是命令行坐标指错格子。
4. 需要谨慎核对时，仍可用两段式：`xq move <走法>` 预览 → 核对返回的中文描述 → `xq commit <pending_id>`（或 `xq cancel` 反悔）。
5. 回到第 1 步。

## 走法格式（ICCS 坐标）
4 个字符：起点列+起点行+终点列+终点行。列 a-i（红方视角从左到右），行 0-9（0 是红方底线，9 是黑方底线）。
例：红方开局 `h2e2` 就是"炮二平五"。拿不准时先运行 `xq legal-moves` 查看当前全部合法走法。

## 可选：棋盘截图
如果你的模型支持看图，运行 `xq board-image` 会把当前棋盘保存为 PNG（按你的视角渲染，
约 240 tokens），打开它校准你对局面的空间理解；不支持看图就忽略此命令，文字棋盘已足够。

## 预览与挂单
`xq move` 只是预览：**不会落子，也不会改变对手看到的局面**。预览会生成一个挂单，
有效期 = min(60s, 本步剩余时间)，过期自动作废，需要重新 `xq move`；
只有 `xq commit <pending_id>` 之后走法才真正生效。

## 规则速查
车：直线任意远，不可越子。马：走日字，马腿（正方向相邻点）被占则不可走（蹩马腿）。
相/象：走田字，田心有子（塞象眼）不可走，不可过河。仕/士：九宫内斜走一格。
帅/将：九宫内直走一格；两帅将不能在同一直线上照面（白脸将）。
炮：直走；吃子必须恰好隔一个子（炮架）。兵/卒：只进不退，过河后可横走。
将死或困毙对方获胜。{timeout_rule}每步限时 {step_time}，请留意 `xq state` 里的剩余时间。

## 时间管理（重要）
- 局时共 {time_limit}，每确认一步棋局时 +{increment} 秒——走棋就是回血，思考是纯支出。
- **每条 `xq` 命令的返回里都带一行 `⏱ 时间状态`**（本步已用 / 局时还剩 / 对方上一步用时），每次都要看一眼。
- 建议节奏：看清局面就走，单步思考尽量控制在本步上限的三分之一以内，把长考留给真正的关键局面。
- `xq note` 正常计入你的时钟：思考记录与走棋共享同一时间预算，别在记录上耗费过多时间。
- 看到 `⚠️` 提示时立即停止分析，提交当前最佳走法。

## 纪律
- 只能在自己回合走棋；非法走法会被服务器拒绝并说明原因。
- 不得尝试获取对手的任何非公开信息；不得读取其他 Agent 的文件或进程。
- 不得读取本平台（裁判服务器/规则库）的源代码，或利用其缺陷为自己获利。
- 不得使用或现场编写象棋引擎/搜索程序辅助决策，纯凭自身推理下棋。
- 认输：`xq resign`；提和：`xq offer-draw`；接受和棋：`xq accept-draw`。
"""


PASS_RULE = ("步时超时会记「停着」一手：直接轮空给对方，耗时照扣局时且不加秒"
             "（被将军时不能停着，超时仍判负）；局时耗尽判负。")
LOSE_RULE = "步时或局时超时即判负。"


def render_agent_prompt(game_id, color, token, server, step_time="3 分钟",
                        xq_cmd="xq", step_timeout="pass",
                        time_limit="15 分钟", increment=20):
    xq_note = ("" if xq_cmd == "xq" else
               f"\n> 注意：本环境中 `xq` 命令的完整路径是 `{xq_cmd}`，用它替换下文所有 `xq`。\n")
    return TEMPLATE.format(
        side_guide=RED_GUIDE if color == "r" else BLACK_GUIDE,
        game_id=game_id, color="red" if color == "r" else "black",
        token=token, server=server, step_time=step_time,
        xq_cmd=xq_cmd, xq_note=xq_note,
        timeout_rule=PASS_RULE if step_timeout == "pass" else LOSE_RULE,
        time_limit=time_limit, increment=increment,
    )
