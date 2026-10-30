#!/usr/bin/env python
"""探测夹爪在 0~100 归一化尺度上的真实行程。

用途：确定哪一端是"夹紧"，给 teleop 的 `--gripper-closed` 用。

    PYTHONPATH=src ~/miniconda3/envs/lerobot/bin/python dsh/probe_gripper.py
    PYTHONPATH=src ... dsh/probe_gripper.py --motor right_arm_gripper
    PYTHONPATH=src ... dsh/probe_gripper.py --values 0,50,100 --hold 1.0

安全：
- 只改夹爪一维，其余关节保持当前目标
- 底盘速度显式写 0
- 结束时（含 Ctrl-C）恢复原始夹爪目标
- 请把手从夹爪里移开
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from lerobot.robots.xlerobot import XLerobot  # noqa: E402
from lerobot.robots.xlerobot.config_xlerobot import XLerobotConfig  # noqa: E402


def base_action(obs: dict, motor: str, value: float) -> dict:
    """只把 motor 改成 value，其余保持，底盘速度置零。"""
    action = {}
    for key, val in obs.items():
        if key.endswith(".vel"):
            action[key] = 0.0          # 底盘保持静止
        elif key.endswith(".pos"):
            action[key] = float(val)
    action[motor] = float(value)
    return action


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="探测夹爪行程")
    ap.add_argument("--motor", default="left_arm_gripper",
                    choices=("left_arm_gripper", "right_arm_gripper"))
    ap.add_argument("--values", default="0,25,50,75,100", help="要试的目标值，逗号分隔")
    ap.add_argument("--hold", type=float, default=1.5, help="每个值保持秒数")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不连接机器人")
    ap.add_argument("--no-cameras", action="store_true", default=True,
                    help="跳过相机（默认开；缺相机时也能跑）")
    args = ap.parse_args(argv)

    values = [float(x) for x in args.values.replace(" ", "").split(",") if x]
    print(f"目标关节: {args.motor}")
    print(f"将依次尝试: {values}（每个保持 {args.hold}s）")
    print("⚠️  请把手移开夹爪，并确保机械臂周围没有障碍物\n")

    if args.dry_run:
        print("dry-run：不连接机器人，也不发送任何指令")
        return 0

    robot = XLerobot(XLerobotConfig())
    if args.no_cameras:
        robot.cameras = {}          # 探针不需要相机；缺相机时也不该拦路
    robot.connect(calibrate=False)
    try:
        obs = robot.get_observation()
        start = float(obs[args.motor])
        print(f"当前实测: {start:.2f}\n")

        print(f"{'目标':>6}  {'实测':>6}  {'差值':>6}   说明")
        results = []
        for v in values:
            deadline = time.monotonic() + args.hold
            while time.monotonic() < deadline:
                robot.send_action(base_action(obs, args.motor, v))
                time.sleep(1 / 30)
            measured = float(robot.get_observation()[args.motor])
            diff = measured - v
            note = ""
            if abs(diff) > 20:
                note = "← 差很多：可能已到限位，或本端就是行程端点"
            results.append((v, measured))
            print(f"{v:6.0f}  {measured:6.2f}  {diff:+6.2f}   {note}")

        span = max(m for _, m in results) - min(m for _, m in results)
        print(f"\n实测行程: {span:.1f}（0~100 尺度）")
        if span < 10:
            print("❌ 几乎没有移动：检查该夹爪是否有机械卡死、标定是否正确")
        else:
            lo = min(results, key=lambda t: t[1])
            hi = max(results, key=lambda t: t[1])
            print(f"实测最小 {lo[1]:.1f}（目标 {lo[0]:.0f}） → 实测最大 {hi[1]:.1f}（目标 {hi[0]:.0f}）")
            print("目视确认哪一端是夹紧，然后把那个目标值作为 --gripper-closed")

        # 恢复
        end = float(robot.get_observation()[args.motor])
        for _ in range(int(1.0 * 30)):
            robot.send_action(base_action(obs, args.motor, start))
            time.sleep(1 / 30)
        print(f"\n已恢复夹爪目标到 {start:.2f}（当前实测 {end:.2f}）")
    finally:
        robot.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
