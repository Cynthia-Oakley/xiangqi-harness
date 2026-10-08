"""生成喂给 Agent Harness 的提示词。"""

RED_GUIDE = "你是红方，先行。你的棋子是大写（R N B A K C P = 车马相仕帅炮兵）。"
BLACK_GUIDE = "你是黑方，后行。你的棋子是小写（r n b a k c p = 车马象士将炮卒）。"

TEMPLATE = """# 中国象棋对弈任务

{side_guide}

## 你的身份
- 对局 ID: {game_id}
- 座位令牌: {token}
- 裁判服务器: {server}

## 第一步：入座
```bash
xq join {game_id} --color {color} --token {token} --server {server}
```

## 对弈循环（严格遵守）
1. `xq wait` —— 阻塞等待轮到你。输出里 `轮到你: True` 才轮到你。
2. `xq state` —— 查看局面（ASCII 棋盘 + FEN + 剩余时间）。
3. 思考，并用 `xq note "你的思考"` 记录（只有观众能看到，对手看不到）。
4. `xq move <走法>` —— 预览。仔细阅读返回的中文描述、是否吃子、走后 FEN。
5. **核对预览是否符合你的预期**：符合 → `xq commit <pending_id>`；不符合 → `xq cancel <pending_id>` 后重新 `xq move`。
6. 回到第 1 步。

## 走法格式（ICCS 坐标）
4 个字符：起点列+起点行+终点列+终点行。列 a-i（红方视角从左到右），行 0-9（0 是红方底线，9 是黑方底线）。
例：红方开局 `h2e2` 就是"炮二平五"。拿不准时先运行 `xq legal-moves` 查看当前全部合法走法。

## 规则速查
车：直线任意远，不可越子。马：走日字，马腿（正方向相邻点）被占则不可走（蹩马腿）。
相/象：走田字，田心有子（塞象眼）不可走，不可过河。仕/士：九宫内斜走一格。
帅/将：九宫内直走一格；两帅将不能在同一直线上照面（白脸将）。
炮：直走；吃子必须恰好隔一个子（炮架）。兵/卒：只进不退，过河后可横走。
将死或困毙对方获胜；超时判负。每步限时 {step_time}，请留意 `xq state` 里的剩余时间。

## 纪律
- 只能在自己回合走棋；非法走法会被服务器拒绝并说明原因。
- 不得尝试获取对手的任何非公开信息；不得读取其他 Agent 的文件或进程。
- 认输：`xq resign`；提和：`xq offer-draw`；接受和棋：`xq accept-draw`。
"""


def render_agent_prompt(game_id, color, token, server, step_time="3 分钟"):
    return TEMPLATE.format(
        side_guide=RED_GUIDE if color == "r" else BLACK_GUIDE,
        game_id=game_id, color="red" if color == "r" else "black",
        token=token, server=server, step_time=step_time,
    )
