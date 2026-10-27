#!/usr/bin/env python
"""发布器自检：不需要真机，用假机器人端到端验证。

覆盖两条接线路径：
  * 默认 —— 包装模式（遥操脚本用的就是这条）：`robot = RobotStateStream(robot)`
  * --install —— 实例注入模式：`install(robot)`，不改类也不包装

然后起子进程跑 raw_recorder.py --probe-only 与一次短录制，校验产物。

用法：
    python dsh/selftest_publisher.py                 # 包装模式，3 秒
    python dsh/selftest_publisher.py --install       # 注入模式
    python dsh/selftest_publisher.py --bus           # 走总线读取回退分支
"""

from __future__ import annotations

import argparse
import glob
import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from state_publisher import RobotStateStream, StateStreamClient, install  # noqa: E402

STATE_NAMES = [
    "left_arm_shoulder_pan.pos", "left_arm_shoulder_lift.pos", "left_arm_elbow_flex.pos",
    "left_arm_wrist_flex.pos", "left_arm_wrist_roll.pos", "left_arm_gripper.pos",
    "right_arm_shoulder_pan.pos", "right_arm_shoulder_lift.pos", "right_arm_elbow_flex.pos",
    "right_arm_wrist_flex.pos", "right_arm_wrist_roll.pos", "right_arm_gripper.pos",
    "head_motor_1.pos", "head_motor_2.pos",
    "x.vel", "y.vel", "theta.vel",
]
CAMS = {"head": (480, 640, 3), "left_wrist": (480, 640, 3), "right_wrist": (480, 640, 3)}


class FakeBus:
    """模拟 FeetechMotorsBus：返回未归一化的关节名（与真实 *_motors 一致）。"""

    def __init__(self):
        self.t0 = time.monotonic()

    def sync_read(self, data_name, motors):
        t = time.monotonic() - self.t0
        if data_name == "Present_Position":
            return {m: float(40.0 * np.sin(0.8 * t + (i % 7))) for i, m in enumerate(motors)}
        return dict.fromkeys(motors, 0.0)


def _frame(index: int) -> np.ndarray:
    """红/绿/蓝各占一个通道，便于发现通道顺序错乱。"""
    key = list(CAMS)[index]
    h, w, _ = CAMS[key]
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:, :, index] = np.linspace(0, 255, w, dtype=np.uint8)[None, :]
    return img


class _FakeCam:
    """单路相机替身，可模拟 USB 掉线。"""

    def __init__(self, key: str, index: int, owner: "FakeRobot"):
        self.key, self.index, self.owner = key, index, owner
        self.connected = True

    @property
    def is_connected(self):
        return self.connected

    def connect(self, warmup: bool = True):
        self.connected = True

    def disconnect(self):
        self.connected = False

    def async_read(self, timeout_ms=None):
        if not self.connected:
            raise RuntimeError(f"{self.key} not connected")
        elapsed = time.monotonic() - self.owner.t0
        if (self.owner.fail_camera == self.key
                and self.owner.fail_after <= elapsed < self.owner.fail_until):
            self.connected = False          # 设备真的从总线上消失
            raise RuntimeError("simulated USB disconnect (No such device)")
        return _frame(self.index)


class FakeRobot:
    """最小可用的 XLeRobot 替身：字段与真实对象一致。"""

    name = "xlerobot-fake"
    observation_features = {**{k: float for k in STATE_NAMES}, **CAMS}
    action_features = {k: float for k in STATE_NAMES}

    def __init__(self, *, with_split: bool, fail_camera: str | None = None,
                 fail_after: float = 0.5, fail_until: float = float("inf")):
        self.t0 = time.monotonic()
        self.fail_camera = fail_camera
        self.fail_after = fail_after
        self.fail_until = fail_until
        self.bus1 = FakeBus()
        self.bus2 = FakeBus()
        self.cameras = {key: _FakeCam(key, i, self) for i, key in enumerate(CAMS)}
        self.left_arm_motors = [k.removesuffix(".pos") for k in STATE_NAMES[:6]]
        self.right_arm_motors = [k.removesuffix(".pos") for k in STATE_NAMES[6:12]]
        self.head_motors = ["head_motor_1", "head_motor_2"]
        self.base_motors = ["base_left_wheel", "base_back_wheel", "base_right_wheel"]
        self.disconnected = False
        if with_split:
            self.get_state_observation = self._state

    def _state(self):
        t = time.monotonic() - self.t0
        v = 40.0 * np.sin(0.8 * t + np.arange(len(STATE_NAMES)) * 0.5)
        return {k: float(v[i]) for i, k in enumerate(STATE_NAMES)}

    def _wheel_raw_to_body(self, a, b, c):
        return {"x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0}

    def get_camera_observation(self):
        return {key: cam.async_read() for key, cam in self.cameras.items()}

    def get_observation(self):
        obs = self.get_state_observation() if hasattr(self, "get_state_observation") else {}
        obs.update(self.get_camera_observation())
        return obs

    def send_action(self, action):
        return {k: float(action[k]) for k in STATE_NAMES if k in action}

    def stop_base(self):
        pass

    def disconnect(self):
        self.disconnected = True


def _drive(robot, stop, fps=30):
    """模拟遥操主循环：30Hz 下发 action 并读观测。"""
    period = 1.0 / fps
    while not stop.is_set():
        robot.send_action({k: float(np.sin(time.monotonic())) for k in STATE_NAMES})
        robot.get_observation()
        stop.wait(period)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="发布器端到端自检（无硬件）")
    ap.add_argument("--port", type=int, default=8795)
    ap.add_argument("--seconds", type=float, default=2.0)
    ap.add_argument("--install", action="store_true", help="用 install() 注入，而不是包装")
    ap.add_argument("--bus", action="store_true", help="不提供 get_state_observation，强制走总线分支")
    ap.add_argument("--keep", action="store_true", help="保留录制产物")
    ap.add_argument("--camera-fail", default=None, metavar="NAME",
                    help="模拟某路相机掉线（head/left_wrist/right_wrist）")
    ap.add_argument("--fail-after", type=float, default=0.5, help="掉线发生在多少秒后")
    ap.add_argument("--camera-flap", default=None, metavar="NAME",
                    help="模拟某路相机短暂掉线后自愈（验证看门狗重连）")
    ap.add_argument("--fail-for", type=float, default=1.5, help="掉线持续多少秒")
    args = ap.parse_args(argv)

    flap = args.camera_flap
    base = FakeRobot(with_split=not args.bus, fail_camera=args.camera_fail or flap,
                     fail_after=args.fail_after,
                     fail_until=(args.fail_after + args.fail_for) if flap else float("inf"))
    mode = "install()" if args.install else "包装"
    print(f"== 启动发布器 :{args.port}（{mode}模式，{'总线' if args.bus else '拆分'}读取）==")

    if args.install:
        install(base, port=args.port, fps=30)
        robot = base
        stream = base._state_stream
    else:
        stream = RobotStateStream(base, port=args.port, fps=30).start()
        robot = stream

    stop = threading.Event()
    threading.Thread(target=_drive, args=(robot, stop), daemon=True).start()
    out_root = HERE / "out" / "selftest"
    try:
        time.sleep(0.5)
        py, rec = sys.executable, HERE / "raw_recorder.py"

        if flap:
            time.sleep(args.fail_after + 0.4)                 # 掉线窗口内
            mid = StateStreamClient(port=args.port, timeout=2).read(max_age_s=1.5)
            mid_cams = sorted(k.split(".")[-1] for k in mid if k.startswith("observation.images."))
            time.sleep(args.fail_for + 3.0)                   # 等看门狗重连
            after = StateStreamClient(port=args.port, timeout=2).read(max_age_s=1.5)
            after_cams = sorted(k.split(".")[-1] for k in after if k.startswith("observation.images."))
            print(f"相机自愈测试（{flap}）：")
            print(f"  掉线窗口内：{mid_cams}")
            print(f"  自愈之后  ：{after_cams}")
            ok = len(mid_cams) == len(CAMS) - 1 and len(after_cams) == len(CAMS)
            print(f"  {'✅' if ok else '❌'} 掉线可降级，且看门狗自动重连")
            return 0 if ok else 1

        if args.camera_fail:
            # 相机掉线：发布器应继续供状态与其余视角，录制器应明确拒绝
            time.sleep(args.fail_after + 0.3)
            snap = StateStreamClient(port=args.port, timeout=2).read(max_age_s=1.5)
            present = sorted(k.split(".")[-1] for k in snap if k.startswith("observation.images."))
            print(f"相机掉线测试（{args.camera_fail}）：")
            print(f"  仍在发布：{present}")
            print(f"  state 仍可用：{snap['observation.state'].shape}")
            p = subprocess.run([py, str(rec), "--source", "stream", "--port", str(args.port),
                                "--probe-only"], capture_output=True, text=True)
            print("  录制器：" + p.stdout.strip().splitlines()[-1])

            # 关键：坏相机不能把快照卡住，帧率必须仍是 ~30Hz
            c = StateStreamClient(port=args.port, timeout=2)
            a = c.read(max_age_s=1.5)
            time.sleep(1.0)
            b = c.read(max_age_s=1.5)
            rate = b["sequence"] - a["sequence"]
            print(f"  掉线后发布速率：{rate} 帧/秒（应 ≥25）")

            ok = (len(present) == len(CAMS) - 1
                  and p.returncode == 2 and "相机缺失" in p.stdout
                  and rate >= 25)
            print(f"  {'✅' if ok else '❌'} 单路掉线：状态仍 30Hz，录制器拒绝开录")
            return 0 if ok else 1

        print("\n-- 1) 探测 --")
        p = subprocess.run([py, str(rec), "--source", "stream", "--port", str(args.port), "--probe-only"],
                           capture_output=True, text=True)
        print(p.stdout.strip())
        if p.returncode != 0:
            print(p.stderr.strip())
            return 1

        print("\n-- 2) 录制 --")
        p = subprocess.run(
            [py, str(rec), "--source", "stream", "--port", str(args.port),
             "--root", str(out_root), "--task", "selftest",
             "--script", f"on;+{args.seconds:.1f};off;quit"],
            capture_output=True, text=True,
        )
        print("\n".join(p.stdout.strip().splitlines()[-3:]))
        if p.returncode != 0:
            print(p.stderr.strip())
            return 1

        print("\n-- 3) 校验 --")
        episodes = sorted(glob.glob(str(out_root / "*" / "ep_000001")))
        if not episodes:
            print("  ❌ 没有产物")
            return 1
        ep = Path(episodes[-1])
        meta = json.loads((ep / "meta.json").read_text(encoding="utf-8"))
        data = np.load(ep / "samples.npz")
        checks = {
            "schema_version": meta["schema_version"] == "raw-1",
            "17 维 state": data["state"].shape[1] == 17,
            "17 维 action": data["action"].shape[1] == 17,
            "帧数 ≥ 15": meta["frame_count"] >= 15,
            "fps ≈ 30": abs(meta["nominal_fps"] - 30) < 3,
            "有 3 路图": len(list((ep / "imgs").glob("000000_*.jpg"))) == 3,
            "无残留 tmp": not list(out_root.rglob("*.tmp")),
        }
        for name, ok in checks.items():
            print(f"  {'✅' if ok else '❌'} {name}")
        print(f"\n  帧数 {meta['frame_count']} / {meta['duration_s']:.2f}s / {meta['nominal_fps']:.1f}fps")

        print("\n-- 4) 生命周期 --")
        stream.close()
        ok_closed = stream._server is None
        print(f"  {'✅' if ok_closed else '❌'} close() 后 HTTP 服务已停")
        checks["close"] = ok_closed
        return 0 if all(checks.values()) else 1
    finally:
        stop.set()
        try:
            stream.close()
        except Exception:
            pass
        if not args.keep and out_root.exists():
            shutil.rmtree(out_root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
