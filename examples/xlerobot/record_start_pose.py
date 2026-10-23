"""Record a manually posed XLeRobot into its local ``start_pose`` config.

Run from the LeRobot repository root:

    PYTHONPATH=src python examples/xlerobot/record_start_pose.py

The script stops the mobile base, disables torque only for the arms and head,
then writes their calibrated positions to the gitignored xlerobot_config.json.
"""

from __future__ import annotations

import json
from pathlib import Path

from lerobot.robots.xlerobot import (
    XLEROBOT_LOCAL_CONFIG_PATH,
    XLEROBOT_POSITION_MOTORS,
    XLerobot,
    XLerobotConfig,
)


def read_position_joints(robot: XLerobot) -> dict[str, float]:
    positions = {
        **robot.bus1.sync_read("Present_Position", robot.left_arm_motors),
        **robot.bus2.sync_read("Present_Position", robot.right_arm_motors),
        **robot.bus1.sync_read("Present_Position", robot.head_motors),
    }
    return {motor: round(float(positions[motor]), 4) for motor in XLEROBOT_POSITION_MOTORS}


def print_pose(pose: dict[str, float]) -> None:
    print("\n当前关节位置：")
    for motor in XLEROBOT_POSITION_MOTORS:
        print(f"  {motor:<27} {pose[motor]:>9.4f}")


def save_start_pose(config_path: Path, pose: dict[str, float]) -> None:
    if config_path.is_file():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"配置文件不是有效 JSON，未写入：{config_path}: {exc}") from exc
        if not isinstance(config, dict):
            raise ValueError(f"配置文件顶层必须是 JSON 对象，未写入：{config_path}")
    else:
        config = {}

    config["start_pose"] = pose
    config_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = config_path.with_suffix(f"{config_path.suffix}.tmp")
    temporary_path.write_text(
        json.dumps(config, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(config_path)


def main() -> None:
    config = XLerobotConfig(id="my_xlerobot")
    # Joint recording does not need cameras and should not fail because a camera is unavailable.
    config.cameras = {}
    robot = XLerobot(config)
    connected = False

    try:
        robot.connect()
        connected = True
        robot.stop_base()
        print("[MAIN] 机器人已连接，底盘速度已设为 0。")
        print(
            "\n警告：下一步会释放左右机械臂和头部的力矩，"
            "它们可能因重力突然下落。"
        )
        input(
            "请先用手支撑机械臂和头部，确认周围安全后按 Enter 释放力矩；"
            "Ctrl+C 取消："
        )

        robot.bus1.disable_torque(robot.left_arm_motors + robot.head_motors)
        robot.bus2.disable_torque(robot.right_arm_motors)
        print("\n[MAIN] 左右机械臂和头部力矩已释放；底盘仍保持停止。")

        while True:
            input("请手动摆好两臂、夹爪和头部，然后按 Enter 读取关节位置：")
            pose = read_position_joints(robot)
            print_pose(pose)
            choice = (
                input("\n输入 s 保存，r 重新摆放并读取，或 q 不保存退出 [s/r/q]：")
                .strip()
                .lower()
            )
            if choice == "s":
                save_start_pose(XLEROBOT_LOCAL_CONFIG_PATH, pose)
                print(f"\n[MAIN] start_pose 已保存到：{XLEROBOT_LOCAL_CONFIG_PATH}")
                break
            if choice == "q":
                print("\n[MAIN] 已取消，配置未修改。")
                break
            if choice != "r":
                print("请输入 s、r 或 q。")

    except KeyboardInterrupt:
        print("\n[MAIN] 已取消，配置未修改。")
    finally:
        if connected:
            robot.disconnect()
        print("[MAIN] 已断开机器人。")


if __name__ == "__main__":
    main()
