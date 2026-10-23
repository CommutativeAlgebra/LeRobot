"""Xbox 手柄连接测试（与 5_xlerobot_teleop_xbox.py 使用同一套按键/轴映射）

运行:
    conda activate lerobot
    python examples/xlerobot/xbox_test_read_CN.py
"""
import os
import time

# pygame(SDL) 默认会接管 SIGINT 转为内部退出事件，导致终端 Ctrl+C 无法中断本脚本；
# 必须在 import pygame / pygame.init() 之前设置
os.environ.setdefault("SDL_NO_SIGNAL_HANDLERS", "1")

import pygame

# 以下索引必须和 5_xlerobot_teleop_xbox.py 的 get_xbox_key_state 保持一致
AXIS_MAP = {
    "left_x": 0,
    "left_y": 1,  # 左摇杆
    "left_trigger": 2,  # LT（松开约 -1.0，按到底约 +1.0）
    "right_x": 3,
    "right_y": 4,  # 右摇杆
    "right_trigger": 5,  # RT
}
BUTTON_MAP = {
    0: "A",
    1: "B",
    2: "X",
    3: "Y",
    4: "LB",
    5: "RB",
    6: "Back",
    7: "Start",
    8: "Guide",
    9: "L3(左摇杆按下)",
    10: "R3(右摇杆按下)",
}
DPAD_NAMES = {
    (0, 1): "上",
    (0, -1): "下",
    (-1, 0): "左",
    (1, 0): "右",
    (-1, 1): "左上",
    (1, 1): "右上",
    (-1, -1): "左下",
    (1, -1): "右下",
}
TRIGGER_THRESHOLD = 0.5  # 与遥操脚本一致
STICK_THRESHOLD = 0.5  # 与遥操脚本一致


def read_state(joystick):
    axes = [joystick.get_axis(i) for i in range(joystick.get_numaxes())]
    buttons = [joystick.get_button(i) for i in range(joystick.get_numbuttons())]
    hat = joystick.get_hat(0) if joystick.get_numhats() > 0 else (0, 0)
    return axes, buttons, hat


def main() -> None:
    pygame.init()
    pygame.joystick.init()
    if pygame.joystick.get_count() == 0:
        print(
            "❌ 未检测到 Xbox 手柄！请检查 USB 数据线（需支持数据，部分线仅充电），"
            "并确认 ls /dev/input/js* 有输出"
        )
        pygame.quit()
        return
    joystick = pygame.joystick.Joystick(0)
    joystick.init()
    print(f"[OK] 已连接手柄: {joystick.get_name()}")
    print(
        f"     axes={joystick.get_numaxes()}  buttons={joystick.get_numbuttons()}  "
        f"hats={joystick.get_numhats()}"
    )
    # 标准 XInput 布局: 6 轴(0-5) + 1 hat(十字键) + 至少 11 键
    # (Series X|S 手柄多一个 Share 键，buttons=12 也正常，不影响前 11 个索引)
    if joystick.get_numaxes() < 6 or joystick.get_numbuttons() < 11 or joystick.get_numhats() < 1:
        print(
            "⚠️  按键/轴/hat 数量与标准 XInput 布局不一致，"
            "遥操脚本 get_xbox_key_state 中的索引可能需要修改！"
        )
        if joystick.get_numaxes() < 6:
            print("❌ 当前轴数量不足，无法按标准 XInput 布局读取。")
            joystick.quit()
            pygame.quit()
            return

    print("\n输入说明（字面与遥操 keymap 一致）:")
    print("  左/右摇杆: 手臂 XY 平面移动 | 摇杆按下(L3/R3)+左右推: shoulder_pan")
    print("  按住 LB/RB + 摇杆: pitch / wrist_roll | LT/RT: 夹爪")
    print("  头部保持启动姿态 | 十字键: 底盘 | Back: 恢复启动姿态")
    print("按 Ctrl+C 停止\n")
    time.sleep(0.5)

    try:
        while True:
            pygame.event.pump()
            axes, buttons, hat = read_state(joystick)

            lx, ly = axes[AXIS_MAP["left_x"]], axes[AXIS_MAP["left_y"]]
            rx, ry = axes[AXIS_MAP["right_x"]], axes[AXIS_MAP["right_y"]]
            lt, rt = axes[AXIS_MAP["left_trigger"]], axes[AXIS_MAP["right_trigger"]]

            pressed = [
                name for idx, name in BUTTON_MAP.items() if idx < len(buttons) and buttons[idx]
            ]
            dpad = DPAD_NAMES.get(hat, "-")

            # (* = 超过遥操阈值 0.5，会被判为“按下/推满”)
            line = (
                f"LS=({lx:+.2f},{ly:+.2f})"
                f"{'*' if max(abs(lx), abs(ly)) > STICK_THRESHOLD else ' '} "
                f"RS=({rx:+.2f},{ry:+.2f})"
                f"{'*' if max(abs(rx), abs(ry)) > STICK_THRESHOLD else ' '} "
                f"LT={lt:+.2f}{'*' if lt > TRIGGER_THRESHOLD else ' '} "
                f"RT={rt:+.2f}{'*' if rt > TRIGGER_THRESHOLD else ' '} "
                f"十字键={dpad} 按键={','.join(pressed) if pressed else '-'}"
            )
            print("\r" + line.ljust(100), end="", flush=True)
            time.sleep(0.02)
    except KeyboardInterrupt:
        print("\n测试结束")
    finally:
        joystick.quit()
        pygame.quit()


if __name__ == "__main__":
    main()
