#!/usr/bin/env python
"""XLeRobot 原始数据录制器（键盘控制）。

设计原则
--------
1. **接入后才允许打开**：开关受 "数据源在线" 门控。数据源必须能读到一帧
   新鲜快照（state_stream 的 /schema + /snapshot），否则拒绝打开。
2. **只记原始层**：落盘的是物理量（state/action）+ 真实时间戳 + 原始 JPEG。
   不做 resize、不做归一化、不生成 token —— 那些属于训练时的变换，
   放在采集端会永久丢信息，而且 tokenized_prompt 依赖尚未统计出的 norm stats。
3. **不伪造 action**：action 必须来自 `send_action()` 的返回值（真实下发目标）。
   拿不到就报错，绝不用观测位置顶替。
4. **列式缓冲 + 原子提交**：每个 episode 一个目录，先写 `.tmp`，成功后改名。

用法
----
    # 1) 先确认机器人状态（不录制）
    python dsh/raw_recorder.py --probe-only

    # 2) 正常录制（需另开终端跑遥操并带 --publish-state）
    python dsh/raw_recorder.py --task pick_and_place

    # 3) 无硬件自检（合成数据）
    python dsh/raw_recorder.py --source fake --script "on;+1.0;off;quit"

键盘
----
    o   打开/关闭开关（开 = 开始记一段；关 = 保存并关闭）
    d   丢弃当前 episode 并关闭开关
    p   打印当前状态
    q   退出（若正在录制则先保存）

输出
----
    dsh/out/raw/<session>/ep_000001/
      ├── meta.json     # schema 版本、字段名、时间统计、状态
      ├── samples.npz   # t_recv, t_state, seq, state[N,S], action[N,A]
      └── imgs/000000_head.jpg ...

依赖：numpy、opencv（用 lerobot conda 环境）
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import signal
import sys
import time
from dataclasses import dataclass, field, field
from pathlib import Path

import cv2
import numpy as np

SCHEMA_VERSION = "raw-1"


def configure_output(verbose: bool) -> None:
    """默认只输出操作必需的信息：把第三方库的 INFO 与进度条关掉。"""
    import logging

    # 进度条（datasets / huggingface_hub / tqdm）
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

    level = logging.INFO if verbose else logging.ERROR
    # "libav" 是 ffmpeg 的编码统计（[libx264 @ ...] 那些），走 Python logger
    for name in ("lerobot", "datasets", "huggingface_hub", "filelock",
                 "transformers", "urllib3", "PIL", "fsspec", "libav"):
        logging.getLogger(name).setLevel(level)
    if not verbose:
        try:
            import datasets as _hf_datasets

            _hf_datasets.disable_progress_bar()
        except Exception:
            pass
        try:                     # libx264 / libsvtav1 的统计行
            import av

            av.logging.set_level(av.logging.ERROR)
        except Exception:
            pass

# v3.0 直接输出时保留的 12 维（去掉头部 2 维与底盘 3 维）
STATE_NAMES_12 = [
    "left_arm_shoulder_pan.pos", "left_arm_shoulder_lift.pos", "left_arm_elbow_flex.pos",
    "left_arm_wrist_flex.pos", "left_arm_wrist_roll.pos", "left_arm_gripper.pos",
    "right_arm_shoulder_pan.pos", "right_arm_shoulder_lift.pos", "right_arm_elbow_flex.pos",
    "right_arm_wrist_flex.pos", "right_arm_wrist_roll.pos", "right_arm_gripper.pos",
]
DEFAULT_PORT = 8765
JPEG_QUALITY = 92


# ---------------------------------------------------------------------------
# 数据源
# ---------------------------------------------------------------------------


class SourceError(RuntimeError):
    """数据源不可用（未接入 / 遥操未运行 / 状态过期）。"""


@dataclass
class Snapshot:
    """一帧原始数据。图像为 RGB uint8 HWC，未做任何缩放。"""

    state: np.ndarray            # float32[S]
    action: np.ndarray           # float32[A]
    images: dict[str, np.ndarray]
    state_age_s: float
    action_age_s: float
    sequence: int
    t_recv: float                # 本地单调时钟，收到该帧的时刻
    cameras_failed: dict[str, str] = field(default_factory=dict)  # 缺哪几路、原因


class StreamSource:
    """从遥操进程的 state_stream（127.0.0.1:8765）只读拉取快照。

    这是唯一能拿到「真实下发 action」的通道：action 由 RobotStateStream.send_action
    捕获 `send_action()` 的返回值（限幅后），不是从 state 推断的。
    """

    def __init__(self, port: int = DEFAULT_PORT, timeout: float = 1.0):
        from urllib.request import urlopen

        self.url = f"http://127.0.0.1:{port}"
        self.timeout = timeout
        self._urlopen = urlopen
        with urlopen(f"{self.url}/schema", timeout=timeout) as resp:
            self.schema = json.load(resp)

    @property
    def state_names(self) -> list[str]:
        return list(self.schema["state_names"])

    @property
    def action_names(self) -> list[str]:
        return list(self.schema["action_names"])

    @property
    def cameras(self) -> dict[str, tuple]:
        return {k: tuple(v) for k, v in self.schema["cameras"].items()}

    def read(self, max_age_s: float = 0.5) -> Snapshot:
        import io

        t0 = time.monotonic()
        try:
            with self._urlopen(f"{self.url}/snapshot", timeout=self.timeout) as resp:
                payload = resp.read()
        except Exception as e:  # URLError / HTTPError(503) / timeout
            raise SourceError(f"读取快照失败：{e}") from e

        try:
            with np.load(io.BytesIO(payload), allow_pickle=False) as data:
                meta = json.loads(data["metadata"].tobytes())
                state = np.asarray(data["state"], dtype=np.float32)
                action = np.asarray(data["action"], dtype=np.float32)
                images = {k: np.array(data[f"image_{k}"]) for k in self.cameras
                          if f"image_{k}" in data}
        except Exception as e:
            raise SourceError(f"快照解析失败：{e}") from e

        if meta.get("stream_id") != self.schema.get("stream_id"):
            raise SourceError("遥操进程已重启，请重新连接数据源")

        # 缺任何一路相机都不能录：训练需要三视角齐全
        # 缺相机不在这里失败：raw 层要尽量保住数据，缺哪几路由上层标记。
        failed = {k: v for k, v in (meta.get("cameras_failed") or {}).items()
                  if k in self.cameras}
        for k in self.cameras:
            if k not in images and k not in failed:
                failed[k] = "missing from snapshot"

        # 保守估计：服务端 age + 本地往返与反序列化耗时
        age = float(meta["state_age_s"]) + (time.monotonic() - t0)
        if age > max_age_s:
            raise SourceError(f"机器人状态已过期（{age:.2f}s > {max_age_s:.2f}s）")

        if state.shape != (len(self.state_names),) or action.shape != (len(self.action_names),):
            raise SourceError("快照维度与 schema 不一致")
        if not np.isfinite(state).all() or not np.isfinite(action).all():
            raise SourceError("快照包含非有限数值")
        for key, img in images.items():
            shape = self.cameras[key]
            if img.shape != shape or img.dtype != np.uint8:
                raise SourceError(f"相机 {key} 图像格式不符：{img.shape}/{img.dtype}")

        return Snapshot(
            state=state,
            action=action,
            images=images,
            state_age_s=age,
            action_age_s=float(meta["action_age_s"]),
            sequence=int(meta["sequence"]),
            t_recv=time.monotonic(),
            cameras_failed=failed,
        )

    def close(self) -> None:
        pass


class DirectSource:
    """同进程直接读机器人，不经过 HTTP。

    给遥操脚本内嵌录制用（如 examples/xlerobot/4_xlerobot_teleop_keyboard.py）：
    主循环每帧调 update() 把这一帧交进来，Recorder 采样时直接复用，
    不再重复读串口和相机。
    """

    def __init__(self, robot, robot_type: str = "xlerobot", stream_id: str = "direct"):
        self.robot = robot
        self.schema = {"robot_type": robot_type, "stream_id": stream_id, "protocol_version": 1}
        self._seq = 0
        self._latest: Snapshot | None = None
        self._names: list[str] = []
        self._actions: list[str] = []
        self._cameras: dict[str, tuple] = {}

        features = getattr(robot, "observation_features", None) or {}
        self._names = [k for k in features if k.endswith(".pos") or k.endswith(".vel")]
        actions = getattr(robot, "action_features", None) or {}
        self._actions = [k for k in actions if k.endswith(".pos") or k.endswith(".vel")]
        for name, cam in (getattr(robot, "cameras", None) or {}).items():
            w = getattr(cam, "width", None) or 640
            h = getattr(cam, "height", None) or 480
            self._cameras[name] = (int(h), int(w), 3)

    @property
    def state_names(self) -> list[str]:
        return list(self._names)

    @property
    def action_names(self) -> list[str]:
        return list(self._actions)

    @property
    def cameras(self) -> dict[str, tuple]:
        return dict(self._cameras)

    def update(self, obs: dict, action: dict | None = None,
               cameras_failed: dict[str, str] | None = None) -> None:
        """把主循环刚拿到的一帧存起来；Recorder 采样时取用。"""
        self._seq += 1
        state = np.asarray([obs.get(k, 0.0) for k in self._names], dtype=np.float32)
        act_src = action if action is not None else obs
        act = np.asarray([act_src.get(k, 0.0) for k in self._actions], dtype=np.float32)
        images = {}
        for name in self._cameras:
            img = obs.get(name)
            if img is not None:
                images[name] = np.asarray(img, dtype=np.uint8)
        self._latest = Snapshot(
            state=state,
            action=act,
            images=images,
            state_age_s=0.0,
            action_age_s=0.0,
            sequence=self._seq,
            t_recv=time.monotonic(),
            cameras_failed=dict(cameras_failed or {}),
        )

    def read(self, max_age_s: float = 0.5) -> Snapshot:
        if self._latest is None:
            # 首次采样（比如刚 open）主循环还没 update 过：直接读一次
            try:
                obs = self.robot.get_observation()
            except Exception as e:
                raise SourceError(f"读取机器人失败：{e}") from e
            self.update(obs)
        age = time.monotonic() - self._latest.t_recv
        if age > max_age_s:
            raise SourceError(f"机器人状态已过期（{age:.2f}s > {max_age_s:.2f}s）")
        return self._latest

    def close(self) -> None:
        self._latest = None


class FakeSource:
    """合成数据源，用于无硬件自检。数值与真实 schema 对齐。"""

    def __init__(self, fps: int = 30):
        self.fps = fps
        self.schema = {
            "stream_id": "fake-" + str(int(time.time())),
            "robot_type": "fake",
            "state_names": [
                "left_arm_shoulder_pan.pos", "left_arm_shoulder_lift.pos",
                "left_arm_elbow_flex.pos", "left_arm_wrist_flex.pos",
                "left_arm_wrist_roll.pos", "left_arm_gripper.pos",
                "right_arm_shoulder_pan.pos", "right_arm_shoulder_lift.pos",
                "right_arm_elbow_flex.pos", "right_arm_wrist_flex.pos",
                "right_arm_wrist_roll.pos", "right_arm_gripper.pos",
                "head_motor_1.pos", "head_motor_2.pos",
                "x.vel", "y.vel", "theta.vel",
            ],
            "action_names": None,  # 与 state 同序，下面补
            "cameras": {"head": [480, 640, 3], "left_wrist": [480, 640, 3], "right_wrist": [480, 640, 3]},
        }
        self.schema["action_names"] = list(self.schema["state_names"])
        self._t0 = time.monotonic()
        self._seq = 0

    @property
    def state_names(self):
        return list(self.schema["state_names"])

    @property
    def action_names(self):
        return list(self.schema["action_names"])

    @property
    def cameras(self):
        return {k: tuple(v) for k, v in self.schema["cameras"].items()}

    def read(self, max_age_s: float = 0.5) -> Snapshot:
        t = time.monotonic() - self._t0
        n = len(self.state_names)
        state = (40.0 * np.sin(0.7 * t + np.arange(n) * 0.4)).astype(np.float32)
        action = np.clip(state + 2.0, -100.0, 100.0).astype(np.float32)
        images = {}
        for i, (key, (h, w, _)) in enumerate(self.cameras.items()):
            img = np.zeros((h, w, 3), dtype=np.uint8)
            img[:, :, i] = np.linspace(0, 255, w, dtype=np.uint8)[None, :]
            x = int((np.sin(t + i) * 0.4 + 0.5) * (w - 60))
            img[h // 3: h // 3 + 60, x: x + 60] = 255
            images[key] = img
        self._seq += 1
        return Snapshot(
            state=state, action=action, images=images,
            state_age_s=0.01, action_age_s=0.02, sequence=self._seq, t_recv=time.monotonic(),
        )

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# 门控：机器人是否真的接入了
# ---------------------------------------------------------------------------


def enumerate_devices() -> dict[str, list[str]]:
    """从 sysfs / /dev 列出电机串口与相机（仅用于提示，沙箱内可能为空）。"""
    import glob

    serial = sorted(set(glob.glob("/dev/serial/by-id/*") + glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*")))
    cameras = sorted(glob.glob("/dev/v4l/by-path/*video-index0"))
    return {"serial": serial, "camera": cameras}


def probe(source_name: str, port: int, max_age_s: float,
          wait_s: float = 0.0, verbose: bool = False) -> tuple[bool, str, object | None]:
    """返回 (ok, 说明, source)。只有 ok 才允许打开开关。

    ``wait_s`` 内会反复重试：遥操进程可能还没起来，或者起来了但还没下发过
    完整的一组关节目标（此时 /snapshot 返回 503）。
    """
    if source_name == "fake":
        src = FakeSource()
        src.read(max_age_s)
        return True, "合成数据源（自检模式）", src

    if verbose:
        devices = enumerate_devices()
        print("  设备枚举：串口 {} 个，相机 {} 个".format(
            len(devices["serial"]), len(devices["camera"])))
        for p in devices["serial"][:4]:
            print(f"    · {p}")
        for p in devices["camera"][:4]:
            print(f"    · {p}")
        if not devices["serial"] or not devices["camera"]:
            print("    （本进程看不到设备节点，可能是容器/沙箱；以数据源连通性为准）")

    deadline = time.monotonic() + max(0.0, wait_s)
    last_reason = ""
    announced = False
    while True:
        try:
            src = StreamSource(port=port)
            snap = src.read(max_age_s)
        except Exception as e:  # 未启动 / 503 / 状态过期
            last_reason = str(e)
            if time.monotonic() >= deadline:
                return False, f"数据源 {port} 不可用：{last_reason}", None
            if not announced:
                print(f"  等待数据源（最多 {wait_s:.0f}s）… {last_reason}")
                announced = True
            time.sleep(0.3)
            continue

        # 录到一半掉相机可以容忍（raw 会标记 degraded），但开录必须三路齐全
        missing = [c for c in src.cameras if c not in snap.images]
        if missing:
            detail = "；".join(f"{k}: {v}" for k, v in (snap.cameras_failed or {}).items()
                               if k in missing)
            return False, (f"相机缺失 {missing}，无法开始录制"
                           f"{'（' + detail + '）' if detail else ''}"), None

        if verbose:
            info = (
                f"已连接：robot_type={src.schema.get('robot_type')} "
                f"state={len(src.state_names)} action={len(src.action_names)} "
                f"cameras={list(src.cameras)} seq={snap.sequence}"
            )
        else:
            info = (f"已连接 · {len(src.state_names)} 维 · "
                    f"{len(src.cameras)} 路相机 · seq={snap.sequence}")
        return True, info, src


# ---------------------------------------------------------------------------
# 落盘
# ---------------------------------------------------------------------------


class EpisodeWriter:
    """列式缓冲 -> 目录。先写 .tmp，finalize 时原子改名。"""

    def __init__(self, root: Path, index: int, meta: dict):
        self.final = root / f"ep_{index:06d}"
        self.tmp = root / f".ep_{index:06d}.tmp"
        self.index = index
        self.meta = dict(meta)
        if self.final.exists():
            raise FileExistsError(f"目标已存在：{self.final}")
        (self.tmp / "imgs").mkdir(parents=True)
        self.t_recv: list[float] = []
        self.t_state: list[float] = []
        self.seq: list[int] = []
        self.state: list[np.ndarray] = []
        self.action: list[np.ndarray] = []
        self.late = 0
        self.repeats = 0
        self.max_state_age = 0.0
        self.max_action_age = 0.0
        self.cameras = list(meta.get("cameras") or {})
        self.missing_counts: dict[str, int] = {}
        self._last_seq = None
        self._closed = False

    def add(self, snap: Snapshot, t_state: float) -> None:
        i = len(self.t_recv)
        self.t_recv.append(snap.t_recv)
        self.t_state.append(t_state)
        self.seq.append(snap.sequence)
        self.state.append(snap.state)
        self.action.append(snap.action)
        self.max_state_age = max(self.max_state_age, snap.state_age_s)
        self.max_action_age = max(self.max_action_age, snap.action_age_s)
        if self._last_seq is not None and snap.sequence == self._last_seq:
            self.repeats += 1
        self._last_seq = snap.sequence

        for key in self.cameras:
            if key not in snap.images:
                self.missing_counts[key] = self.missing_counts.get(key, 0) + 1

        for key, img in snap.images.items():
            ok, buf = cv2.imencode(".jpg", img[:, :, ::-1], [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
            if not ok:
                raise RuntimeError(f"JPEG 编码失败：{key}")
            buf.tofile(str(self.tmp / "imgs" / f"{i:06d}_{key}.jpg"))

    @property
    def frames(self) -> int:
        return len(self.t_recv)

    def finalize(self, status: str = "ok") -> Path:
        if self._closed:
            raise RuntimeError("episode 已关闭")
        if not self.t_recv:
            raise RuntimeError("空 episode 不能保存")

        t = np.asarray(self.t_recv, dtype=np.float64)
        t0 = float(t[0])
        np.savez_compressed(
            self.tmp / "samples.npz",
            t_recv=t - t0,                       # 相对本段起点，秒
            t_state=np.asarray(self.t_state, dtype=np.float64) - t0,
            seq=np.asarray(self.seq, dtype=np.int64),
            state=np.stack(self.state).astype(np.float32),
            action=np.stack(self.action).astype(np.float32),
        )

        self.meta.update(
            schema_version=SCHEMA_VERSION,
            frame_count=self.frames,
            duration_s=float(t[-1] - t0),
            nominal_fps=(self.frames - 1) / (t[-1] - t0) if len(t) > 1 and t[-1] > t0 else 0.0,
            late_samples=self.late,
            repeated_frames=self.repeats,
            max_state_age_s=self.max_state_age,
            max_action_age_s=self.max_action_age,
            status=status,
            # 缺相机的帧数；非空表示这一段不完整，转换器默认会跳过
            cameras_missing=self.missing_counts,
            degraded=bool(self.missing_counts),
        )
        (self.tmp / "meta.json").write_text(json.dumps(self.meta, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(self.tmp, self.final)
        self._closed = True
        return self.final

    def abort(self) -> None:
        self._closed = True
        shutil.rmtree(self.tmp, ignore_errors=True)


@contextlib.contextmanager
def quiet_fd2(enabled: bool):
    """临时把 fd 2 指向 /dev/null。

    PyAV/ffmpeg 的日志写在 C 层 stderr，Python 的 logging 与 redirect_stderr
    都拦不住，只能换掉文件描述符。
    """
    if not enabled:
        yield
        return
    saved = os.dup(2)
    null = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(null, 2)
        yield
    finally:
        os.dup2(saved, 2)
        os.close(saved)
        os.close(null)


class V30Sink:
    """把一个 episode 直接写进共享的 LeRobot **v3.0** 数据集。

    用的是本仓库自带的 ``LeRobotDataset``（`src/lerobot/datasets/`），
    不依赖任何其他 checkout。

    与 raw 模式的区别：写的是 12 维、不含 head/base，也不写那 4 个诊断字段
    （它们以 ``observation.`` 开头，会被训练侧当成 STATE 拼进输入）。
    """

    def __init__(self, dataset, cameras: list[str], state_idx: list[int],
                 action_idx: list[int], task: str, quiet: bool = True):
        self.dataset = dataset
        self.quiet = quiet
        self.cameras = cameras
        self.state_idx = state_idx
        self.action_idx = action_idx
        self.task = task
        self._frames = 0
        self.late = 0            # 与 raw sink 对齐：录制器会在落后时自增
        self.skipped = 0         # 因缺相机被跳过的帧（不伪造，也不放弃整段）
        self.skipped_cams: dict[str, int] = {}

    def add(self, snap: Snapshot, t_state: float) -> None:
        missing = [c for c in self.cameras if c not in snap.images]
        if missing:
            # 跳过这一帧：既不能写缺帧（数据不完整），也不该放弃整段录制
            self.skipped += 1
            for c in missing:
                self.skipped_cams[c] = self.skipped_cams.get(c, 0) + 1
            return
        frame = {
            "observation.state": np.asarray(snap.state[self.state_idx], dtype=np.float32),
            "action": np.asarray(snap.action[self.action_idx], dtype=np.float32),
            "task": self.task,
        }
        for cam in self.cameras:
            frame[f"observation.images.{cam}"] = snap.images[cam]
        self.dataset.add_frame(frame)
        self._frames += 1

    @property
    def frames(self) -> int:
        return self._frames

    def finalize(self, status: str = "ok") -> "Path":
        """触发 save_episode：写 parquet + 编码 h264（同步，约几秒）。"""
        if self.skipped:
            detail = ", ".join(f"{k}×{v}" for k, v in self.skipped_cams.items())
            print(f"  ⚠️ 本段跳过 {self.skipped} 帧（缺相机：{detail}），时间轴会有压缩")
        total = self._frames + self.skipped
        if self.late and total and self.late / total > 0.05:
            print(f"  ⚠️ 本段 {self.late}/{total} 次采样滞后（{self.late / total:.0%}），"
                  f"v3.0 时间轴按帧号计算，实际时长会被压缩")
        with quiet_fd2(self.quiet):
            self.dataset.save_episode(parallel_encoding=False)
        return Path(self.dataset.root)

    def abort(self) -> None:
        self.dataset.clear_episode_buffer()


# ---------------------------------------------------------------------------
# 键盘
# ---------------------------------------------------------------------------


class TtyKeys:
    """cbreak 模式下的非阻塞单键读取，退出时恢复终端。"""

    def __init__(self):
        self._fd = sys.stdin.fileno()
        self._settings = None

    def __enter__(self):
        if not sys.stdin.isatty():
            raise RuntimeError("标准输入不是终端；请用 --script 或直接连终端运行")
        import termios
        import tty

        self._settings = termios.tcgetattr(self._fd)
        tty.setcbreak(self._fd)
        return self

    def poll(self) -> str | None:
        import select

        r, _, _ = select.select([sys.stdin], [], [], 0)
        if not r:
            return None
        ch = sys.stdin.read(1)
        return ch or None

    def __exit__(self, *exc):
        if self._settings is not None:
            import termios

            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._settings)


class ScriptedKeys:
    """脚本化按键，用于自检： "on;+1.0;off;quit"（+秒数表示等待）。"""

    def __init__(self, script: str):
        self.queue: list[tuple[str | None, float]] = []
        for tok in (t.strip() for t in script.split(";")):
            if not tok:
                continue
            if tok.startswith("+"):
                self.queue.append((None, float(tok[1:])))
            else:
                self.queue.append((tok[0], 0.0))
        self._wait_until = 0.0

    def __enter__(self):
        return self

    def poll(self) -> str | None:
        # 非阻塞：等待用截止时刻表示，绝不能 sleep，否则会挡住采样循环
        now = time.monotonic()
        if now < self._wait_until:
            return None
        while self.queue:
            key, delay = self.queue.pop(0)
            if delay > 0:
                self._wait_until = now + delay
                return None
            return key
        return None

    def __exit__(self, *exc):
        pass


# ---------------------------------------------------------------------------
# 录制器
# ---------------------------------------------------------------------------


class Recorder:
    def __init__(self, args, source):
        self.args = args
        self.source = source
        self.episode_index = 0
        self.writer: EpisodeWriter | V30Sink | None = None
        self.armed = False
        self.connected = True
        self.last_stop_t = 0.0
        self.next_sample = 0.0
        self.errors = 0

        # raw 模式：root/session/ep_xxxxxx/
        self.root = Path(args.root).expanduser().resolve()
        # v3.0 模式：整个目录就是一个数据集
        self.v30_dataset = None
        self.state_idx: list[int] = []
        self.action_idx: list[int] = []
        if args.format == "v30":
            self._prepare_v30()
        else:
            self.root.mkdir(parents=True, exist_ok=True)
            self.session = self.root / time.strftime("%Y%m%d-%H%M%S")

    # -- v3.0 数据集准备 ---------------------------------------------------
    def _prepare_v30(self) -> None:
        """解析路径 / 维度映射，并打开（或新建）v3.0 数据集。"""
        _src = Path(__file__).resolve().parents[1] / "src"
        if _src.is_dir() and str(_src) not in sys.path:
            sys.path.insert(0, str(_src))

        self.v30_root = Path(self.args.v30_out).expanduser().resolve()
        self.v30_repo_id = self.args.repo_id or f"local/{self.v30_root.name}"

        names = list(self.source.state_names)
        missing = [n for n in STATE_NAMES_12 if n not in names]
        if missing:
            raise SystemExit(f"数据源缺少这些关节，无法写 12 维：{missing}")
        self.state_idx = [names.index(n) for n in STATE_NAMES_12]
        anames = list(self.source.action_names)
        self.action_idx = [anames.index(n) for n in STATE_NAMES_12]
        self._open_v30_dataset(first=True)

    def _open_v30_dataset(self, first: bool = False) -> None:
        """打开或新建 v3.0 数据集；对上一次没干净退出的残留做备份。"""
        from lerobot.datasets.lerobot_dataset import LeRobotDataset

        out = self.v30_root
        info_path = out / "meta/info.json"

        resumable = False
        if info_path.is_file():
            try:
                info = json.loads(info_path.read_text(encoding="utf-8"))
                resumable = info.get("total_episodes", 0) > 0 and any(
                    (out / "meta/episodes").rglob("*.parquet")
                )
            except Exception:
                resumable = False

        dataset = None
        if resumable:
            try:
                dataset = LeRobotDataset(
                    self.v30_repo_id, root=out, batch_encoding_size=1,
                    vcodec=self.args.vcodec, video_backend="pyav",
                )
                dataset.start_image_writer(0, 4)
                if self.args.verbose:
                    print(f"  ↳ 续写已有数据集：{out}（已有 {dataset.meta.total_episodes} 集）")
            except Exception as e:
                backup = out.with_name(f"{out.name}.broken-{time.strftime('%Y%m%d-%H%M%S')}")
                out.rename(backup)
                self._say(f"⚠️ 现有数据集打不开（{type(e).__name__}），已备份为 {backup.name}")
        elif info_path.is_file():
            backup = out.with_name(f"{out.name}.empty-{time.strftime('%Y%m%d-%H%M%S')}")
            out.rename(backup)
            self._say(f"⚠️ 上次没有保存成功任何一集，已把残留移到 {backup.name}")

        if dataset is None:
            if out.exists() and any(out.iterdir()):
                if not self.args.overwrite:
                    raise SystemExit(f"{out} 已存在且不可续写；加 --overwrite 清掉后重建")
                shutil.rmtree(out)
            features = {
                "observation.state": {"dtype": "float32", "shape": (len(STATE_NAMES_12),),
                                      "names": list(STATE_NAMES_12)},
                "action": {"dtype": "float32", "shape": (len(STATE_NAMES_12),),
                           "names": list(STATE_NAMES_12)},
            }
            for cam, shape in self.source.cameras.items():
                features[f"observation.images.{cam}"] = {
                    "dtype": "video", "shape": tuple(shape),
                    "names": ["height", "width", "channels"],
                }
            dataset = LeRobotDataset.create(
                repo_id=self.v30_repo_id, fps=self.args.fps, root=out, features=features,
                robot_type=self.source.schema.get("robot_type") or "xlerobot",
                use_videos=True, image_writer_threads=4,
                batch_encoding_size=1, vcodec=self.args.vcodec,
            )
            if self.args.verbose:
                print(f"  ↳ 新建数据集：{out}（repo_id={self.v30_repo_id}）")
                print(f"  ↳ 输出格式：LeRobot v3.0，12 维，"
                      f"{len(self.source.cameras)} 路相机，任务：{self.args.task}")

        self.v30_dataset = dataset

    def _close_v30_dataset(self) -> None:
        """finalize：写出 parquet footer 与视频，之后必须重新打开才能继续写。"""
        dataset = self.v30_dataset
        self.v30_dataset = None
        if dataset is None:
            return
        try:
            dataset.stop_image_writer()
        except Exception:
            pass
        try:
            dataset.finalize()
        except Exception as e:
            print(f"  ⚠️ 数据集 finalize 失败：{e}")

    def close(self) -> None:
        self._close_v30_dataset()

    # -- 开关 --------------------------------------------------------------
    def rotate(self) -> None:
        """保存当前段并立刻开始下一段（自动分段用）。"""
        if self.writer is not None:
            self._close(save=True)
        self.last_stop_t = 0.0          # 跳过冷却，立即开新段
        self._open()

    def toggle(self) -> None:
        if self.writer is None:
            self._open()
        else:
            self._close(save=True)

    def _open(self) -> None:
        if not self.connected:
            self._say("拒绝：机器人未接入")
            return
        if time.monotonic() - self.last_stop_t < self.args.cooldown_s:
            self._say("冷却中，稍后再试")
            return
        try:
            snap = self.source.read(self.args.max_state_age_s)   # 开录前先验证一帧
        except SourceError as e:
            self.connected = False
            self._say(f"拒绝：{e}")
            return

        missing_now = [c for c in self.source.cameras if c not in snap.images]
        if missing_now:
            detail = "；".join(f"{k}: {v}" for k, v in (snap.cameras_failed or {}).items())
            self._say(f"拒绝：相机 {missing_now} 不可用{'（' + detail + '）' if detail else ''}")
            return

        self.episode_index += 1
        if self.args.format == "v30":
            if self.v30_dataset is None:
                try:
                    self._open_v30_dataset()
                except Exception as e:
                    self._say(f"拒绝：无法打开数据集：{e}")
                    return
            self.writer = V30Sink(
                self.v30_dataset,
                list(self.source.cameras),
                self.state_idx,
                self.action_idx,
                self.args.task,
                quiet=not self.args.verbose,
            )
        else:
            self.writer = EpisodeWriter(
                self.session,
                self.episode_index,
                {
                    "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "episode_index": self.episode_index,
                    "source": self.args.source,
                    "stream_id": self.source.schema.get("stream_id"),
                    "robot_type": self.source.schema.get("robot_type"),
                    "state_names": self.source.state_names,
                    "action_names": self.source.action_names,
                    "cameras": self.source.cameras,
                    "fps_target": self.args.fps,
                    "max_state_age_s": self.args.max_state_age_s,
                    "task": self.args.task,
                },
            )
        self.armed = True
        self.next_sample = time.monotonic() + 1.0 / self.args.fps
        self.writer.add(snap, snap.t_recv - snap.state_age_s)
        self._say(f"▶ 录制开始 ep_{self.episode_index:06d}")

    def _close(self, save: bool, status: str = "ok") -> None:
        w = self.writer
        if w is None:
            return
        self.writer = None
        self.armed = False
        self.last_stop_t = time.monotonic()

        if not save:
            w.abort()
            self._say("✕ 已丢弃")
            return
        if w.frames < self.args.min_frames:
            w.abort()
            self._say(f"✕ 帧数不足（{w.frames} < {self.args.min_frames}），已丢弃")
            return
        try:
            path = w.finalize(status)
        except Exception as e:
            self._say(f"保存失败：{e}")
            return
        duration = w.meta["duration_s"] if hasattr(w, "meta") else w.frames / self.args.fps
        if self.args.format == "v30":
            self._close_v30_dataset()      # 立刻 finalize：崩溃最多损失当前这一段
        self._say(f"■ 已保存 {path.name}（{w.frames} 帧 / {duration:.1f}s）")

    def discard(self) -> None:
        if self.writer is None:
            self._say("当前没有录制中的 episode")
            return
        self._close(save=False)

    # -- 采样 --------------------------------------------------------------
    def tick(self) -> None:
        now = time.monotonic()
        if not self.armed or now < self.next_sample:
            return
        self.next_sample += 1.0 / self.args.fps
        try:
            snap = self.source.read(self.args.max_state_age_s)
        except SourceError as e:
            self._say(f"⚠ {e} → 自动停止并保存已采数据")
            self.connected = False
            self._close(save=True, status="stale")
            return
        try:
            self.writer.add(snap, snap.t_recv - snap.state_age_s)
        except Exception as e:
            self._say(f"⚠ 写入失败：{e} → 停止")
            self._close(save=True, status="write_error")
            return
        if self.next_sample < time.monotonic():
            self.writer.late += 1
            self.next_sample = time.monotonic() + 1.0 / self.args.fps

    def status(self) -> None:
        w = self.writer
        if w is None:
            print(f"  状态：待机（已录 {self.episode_index} 段）")
        else:
            print(f"  状态：录制中 ep_{self.episode_index:06d}，{w.frames} 帧，滞后 {getattr(w, 'late', 0)}")

    def _say(self, msg: str) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="XLeRobot 原始数据录制器（键盘控制）")
    ap.add_argument("--source", choices=("stream", "fake"), default="stream", help="数据源")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help="state_stream 端口")
    ap.add_argument("--format", choices=("raw", "v30"), default="raw",
                    help="raw = 原始层 npz+JPEG；v30 = 直接写 LeRobot v3.0 数据集")
    ap.add_argument("--root", default="dsh/out/raw", help="raw 模式的输出根目录")
    ap.add_argument("--v30-out", default=None,
                    help="v30 模式的数据集目录（如 data/serve/local/pick_and_place）")
    ap.add_argument("--repo-id", default=None, help="v30 的 repo_id（默认 local/<目录名>）")
    ap.add_argument("--vcodec", default="h264", choices=("h264", "hevc", "libsvtav1"),
                    help="v30 模式的视频编码器")
    ap.add_argument("--overwrite", action="store_true", help="v30 输出目录已存在时清掉")
    ap.add_argument("--task", default="", help="任务标签，写入 meta")
    ap.add_argument("--fps", type=int, default=30, help="采样频率 (1-240)")
    ap.add_argument("--max-state-age-s", type=float, default=0.5, help="状态最大有效年龄")
    ap.add_argument("--min-frames", type=int, default=15, help="低于此帧数自动丢弃")
    ap.add_argument("--cooldown-s", type=float, default=0.3, help="两次录制之间的冷却")
    ap.add_argument("--wait-ready", type=float, default=15.0,
                    help="等待数据源就绪的最长秒数（可先启动本程序）")
    ap.add_argument("--probe-only", action="store_true", help="只做接入检查然后退出")
    ap.add_argument("--verbose", "-v", action="store_true",
                    help="输出设备枚举、数据集详情与第三方库日志（默认关闭）")
    ap.add_argument("--script", default="", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    configure_output(getattr(args, "verbose", False))

    if not 1 <= args.fps <= 240:
        ap.error("--fps 必须在 1..240")
    if args.format == "v30" and not args.v30_out:
        ap.error("--format v30 必须给 --v30-out（数据集目录）")

    if args.verbose:
        print("== 接入检查 ==")
    ok, msg, source = probe(args.source, args.port, args.max_state_age_s,
                            args.wait_ready, verbose=args.verbose)
    print(("  ✅ " if ok else "  ❌ ") + msg)
    if not ok or args.probe_only:
        return 0 if ok else 2

    recorder = Recorder(args, source)

    if args.format == "v30":
        target = f"{recorder.v30_root}  (v3.0 · 12 维 · {args.task})"
    else:
        target = f"{recorder.root}  (raw)"
    print(f"  输出 → {target}")
    print("  o 开关 / d 丢弃 / p 状态 / q 退出")
    if args.verbose:
        print()

    keys = ScriptedKeys(args.script) if args.script else TtyKeys()
    stop = {"flag": False}

    def _signal(_sig, _frm):
        stop["flag"] = True

    signal.signal(signal.SIGINT, _signal)
    signal.signal(signal.SIGTERM, _signal)

    try:
        with keys:
            while not stop["flag"]:
                ch = keys.poll()
                if ch is not None:
                    ch = ch.lower()
                    if ch == "o":
                        recorder.toggle()
                    elif ch == "d":
                        recorder.discard()
                    elif ch == "p":
                        recorder.status()
                    elif ch == "q":
                        break
                recorder.tick()
                time.sleep(0.005)
    finally:
        if recorder.writer is not None:
            recorder._close(save=True)
        recorder.close()          # v3.0：finalize 写 parquet footer
        source.close()
        print("已退出。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
