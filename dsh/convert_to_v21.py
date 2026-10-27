#!/usr/bin/env python
"""把 raw_recorder 的原始层转换成 LeRobot **v2.1** 数据集。

为什么是 v2.1：openpi 钉的 lerobot 提交 `0cf8648` 里 `CODEBASE_VERSION = "v2.1"`，
主版本不同会被 `check_version_compatibility` 拒绝。

输入（dsh/raw_recorder.py 产出）
    <raw>/<session>/ep_000001/
        meta.json      # state_names / action_names / cameras / frame_count ...
        samples.npz    # t_recv, t_state, seq, state[N,17], action[N,17]
        imgs/          # 000000_head.jpg …

输出
    <out>/meta/info.json
    <out>/meta/tasks.jsonl
    <out>/meta/episodes.jsonl
    <out>/meta/episodes_stats.jsonl
    <out>/meta/stats.json
    <out>/data/chunk-000/episode_000000.parquet
    <out>/videos/chunk-000/observation.images.<cam>/episode_000000.mp4

处理链
    原始 17 维 → 30Hz 均匀网格（state 线性插值 / action 零阶保持 / 图像最近帧）
              → 裁成 12 维（去掉 head 2 + base 3）
              → JPEG 一次编码成 h264
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import av
import cv2
import numpy as np

CODEBASE_VERSION = "v2.1"
CHUNKS_SIZE = 1000
STATE_NAMES_12 = [
    "left_arm_shoulder_pan.pos",
    "left_arm_shoulder_lift.pos",
    "left_arm_elbow_flex.pos",
    "left_arm_wrist_flex.pos",
    "left_arm_wrist_roll.pos",
    "left_arm_gripper.pos",
    "right_arm_shoulder_pan.pos",
    "right_arm_shoulder_lift.pos",
    "right_arm_elbow_flex.pos",
    "right_arm_wrist_flex.pos",
    "right_arm_wrist_roll.pos",
    "right_arm_gripper.pos",
]
# v2.1 的路径模板（与 lerobot/common/datasets/utils.py 一致）
DATA_PATH = "data/chunk-{episode_chunk:03d}/episode_{episode_index:06d}.parquet"
VIDEO_PATH = "videos/chunk-{episode_chunk:03d}/{video_key}/episode_{episode_index:06d}.mp4"


# ---------------------------------------------------------------------------
# 读取原始层
# ---------------------------------------------------------------------------


@dataclass
class RawEpisode:
    path: Path
    meta: dict
    t: np.ndarray            # 相对起点的秒
    state: np.ndarray        # [N, S]
    action: np.ndarray       # [N, A]

    @property
    def cameras(self) -> list[str]:
        return list(self.meta["cameras"])


def load_episode(path: Path) -> RawEpisode:
    meta = json.loads((path / "meta.json").read_text(encoding="utf-8"))
    with np.load(path / "samples.npz", allow_pickle=False) as d:
        t = np.asarray(d["t_recv"], dtype=np.float64)
        state = np.asarray(d["state"], dtype=np.float32)
        action = np.asarray(d["action"], dtype=np.float32)
    if t.ndim != 1 or state.shape[0] != t.shape[0] or action.shape[0] != t.shape[0]:
        raise ValueError(f"{path}: 帧数不一致 t={t.shape} state={state.shape} action={action.shape}")
    if not np.all(np.diff(t) > 0):
        raise ValueError(f"{path}: 时间戳不是严格递增")
    return RawEpisode(path=path, meta=meta, t=t, state=state, action=action)


def discover(raw_root: Path) -> list[Path]:
    eps = sorted(p for p in raw_root.glob("*/ep_*") if (p / "samples.npz").is_file())
    # 排除 .tmp
    eps = [p for p in eps if ".tmp" not in p.name and ".tmp" not in p.parent.name]
    if not eps:
        raise SystemExit(f"{raw_root} 下没有找到可用的 episode（需要 */ep_*/samples.npz）")
    return eps


# ---------------------------------------------------------------------------
# 重采样与裁剪
# ---------------------------------------------------------------------------


def resample(ep: RawEpisode, fps: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """返回 (state, action, frame_indices, grid)。

    state  → 线性插值（连续物理量）
    action → 零阶保持（离散指令，插值会造出从未下发的目标）
    图像   → 最近帧
    """
    t = ep.t - ep.t[0]
    n = int(round(t[-1] * fps)) + 1
    grid = np.arange(n, dtype=np.float64) / fps

    state = np.empty((n, ep.state.shape[1]), dtype=np.float32)
    for i in range(ep.state.shape[1]):
        state[:, i] = np.interp(grid, t, ep.state[:, i])

    hold = np.clip(np.searchsorted(t, grid, side="right") - 1, 0, len(t) - 1)
    action = ep.action[hold].astype(np.float32)

    # 图像取时间上最近的一帧
    upper = np.clip(np.searchsorted(t, grid), 0, len(t) - 1)
    lower = np.clip(upper - 1, 0, len(t) - 1)
    nearest = np.where(np.abs(t[upper] - grid) < np.abs(t[lower] - grid), upper, lower)
    return state, action, nearest.astype(np.int64), grid


def trim_to_12d(ep: RawEpisode, state: np.ndarray, action: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    names = list(ep.meta["state_names"])
    missing = [n for n in STATE_NAMES_12 if n not in names]
    if missing:
        raise ValueError(f"{ep.path}: 缺少关节字段 {missing}")
    idx = [names.index(n) for n in STATE_NAMES_12]
    return state[:, idx], action[:, idx]


# ---------------------------------------------------------------------------
# 视频
# ---------------------------------------------------------------------------


def encode_video(
    ep: RawEpisode, camera: str, frame_indices: np.ndarray, out_path: Path, fps: int, crf: int
) -> dict:
    """把每一帧对应的 JPEG 编成 h264。只编码一次，不落中间 PNG。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    first = cv2.imread(str(ep.path / "imgs" / f"{frame_indices[0]:06d}_{camera}.jpg"))
    if first is None:
        raise RuntimeError(f"{ep.path}: 读不到相机 {camera} 的首帧")
    height, width = first.shape[:2]

    container = av.open(str(out_path), mode="w")
    try:
        stream = container.add_stream("libx264", rate=fps)
        stream.width, stream.height = width, height
        stream.pix_fmt = "yuv420p"
        stream.options = {"crf": str(crf), "g": "2"}   # GOP=2：便于按帧切分
        for src_index in frame_indices:
            bgr = cv2.imread(str(ep.path / "imgs" / f"{src_index:06d}_{camera}.jpg"))
            if bgr is None:
                raise RuntimeError(f"{ep.path}: 缺帧 {src_index}_{camera}.jpg")
            frame = av.VideoFrame.from_ndarray(bgr[:, :, ::-1], format="rgb24")  # BGR → RGB
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    finally:
        container.close()

    return {
        "video.height": height,
        "video.width": width,
        "video.codec": "h264",
        "video.pix_fmt": "yuv420p",
        "video.is_depth_map": False,
        "video.fps": fps,
        "video.channels": 3,
        "has_audio": False,
    }


# ---------------------------------------------------------------------------
# 元数据
# ---------------------------------------------------------------------------


def numeric_stats(values: np.ndarray) -> dict:
    values = np.asarray(values, dtype=np.float64)
    if values.ndim == 1:
        values = values.reshape(-1, 1)
    return {
        "mean": values.mean(axis=0).tolist(),
        "std": values.std(axis=0).tolist(),
        "min": values.min(axis=0).tolist(),
        "max": values.max(axis=0).tolist(),
        "count": [int(values.shape[0])],  # 必须是形如 (1,) 的数组；v3.0 聚合需要，v2.1 会忽略
    }


def build_features(cameras: list[str], video_info: dict[str, dict], fps: int) -> dict:
    features = {
        "observation.state": {"dtype": "float32", "shape": [len(STATE_NAMES_12)],
                              "names": list(STATE_NAMES_12)},
        "action": {"dtype": "float32", "shape": [len(STATE_NAMES_12)],
                   "names": list(STATE_NAMES_12)},
    }
    for cam in cameras:
        key = f"observation.images.{cam}"
        info = video_info[cam]
        features[key] = {
            "dtype": "video",
            "shape": [info["video.height"], info["video.width"], 3],
            "names": ["height", "width", "channels"],
            "info": info,
        }
    features.update({
        "timestamp": {"dtype": "float32", "shape": [1], "names": None},
        "frame_index": {"dtype": "int64", "shape": [1], "names": None},
        "episode_index": {"dtype": "int64", "shape": [1], "names": None},
        "index": {"dtype": "int64", "shape": [1], "names": None},
        "task_index": {"dtype": "int64", "shape": [1], "names": None},
    })
    return features


def write_parquet(path: Path, state, action, timestamps, frame_index, episode_index, index, task_index):
    from datasets import Dataset, Features, Sequence, Value

    features = Features({
        "observation.state": Sequence(Value("float32"), length=state.shape[1]),
        "action": Sequence(Value("float32"), length=action.shape[1]),
        "timestamp": Value("float32"),
        "frame_index": Value("int64"),
        "episode_index": Value("int64"),
        "index": Value("int64"),
        "task_index": Value("int64"),
    })
    dataset = Dataset.from_dict(
        {
            "observation.state": [row for row in state],
            "action": [row for row in action],
            "timestamp": timestamps.astype(np.float32),
            "frame_index": frame_index.astype(np.int64),
            "episode_index": episode_index.astype(np.int64),
            "index": index.astype(np.int64),
            "task_index": task_index.astype(np.int64),
        },
        features=features,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    dataset.to_parquet(str(path))


def write_jsonlines(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


def validate(out_root: Path, episodes: list[dict], fps: int) -> None:
    import pyarrow.parquet as pq

    info = json.loads((out_root / "meta/info.json").read_text(encoding="utf-8"))
    assert info["codebase_version"] == CODEBASE_VERSION, info["codebase_version"]
    assert info["fps"] == fps
    assert info["total_episodes"] == len(episodes)
    assert info["total_frames"] == sum(e["length"] for e in episodes)

    global_index = 0
    for ep in episodes:
        chunk = ep["episode_index"] // CHUNKS_SIZE
        parquet = out_root / DATA_PATH.format(episode_chunk=chunk, episode_index=ep["episode_index"])
        table = pq.read_table(parquet).to_pydict()
        assert len(table["action"]) == ep["length"], (len(table["action"]), ep["length"])
        assert len(table["observation.state"][0]) == len(STATE_NAMES_12)
        ts = np.asarray(table["timestamp"], dtype=np.float64)
        assert np.allclose(np.diff(ts), 1.0 / fps, atol=1e-5), "时间戳不等间隔"
        assert table["frame_index"][0] == 0 and table["frame_index"][-1] == ep["length"] - 1
        assert table["index"][0] == global_index
        global_index += ep["length"]

        for cam in [k for k, v in info["features"].items() if v["dtype"] == "video"]:
            video = out_root / VIDEO_PATH.format(
                episode_chunk=chunk, video_key=cam, episode_index=ep["episode_index"]
            )
            with av.open(str(video)) as container:
                frames = sum(1 for _ in container.decode(video=0))
            assert frames == ep["length"], f"{video.name}: {frames} 帧 ≠ {ep['length']}"
    print(f"  ✅ 校验通过：{len(episodes)} 集 / {global_index} 帧 / {fps}fps")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def convert(args) -> int:
    raw_root = Path(args.raw).expanduser().resolve()
    out_root = Path(args.out).expanduser().resolve()

    paths = discover(raw_root)
    print(f"发现 {len(paths)} 个 episode：{raw_root}")
    if out_root.exists():
        if not args.overwrite:
            raise SystemExit(f"输出目录已存在：{out_root}\n加 --overwrite 覆盖")
        shutil.rmtree(out_root)
    out_root.mkdir(parents=True)

    task = args.task
    episodes_meta: list[dict] = []
    episodes_stats: list[dict] = []
    video_info_by_episode: list[dict[str, dict]] = []
    global_index = 0
    cameras0: list[str] = []
    skipped: list[str] = []
    t0 = time.time()

    for ep_index, path in enumerate(paths):
        ep = load_episode(path)
        if not cameras0:
            cameras0 = ep.cameras
        status = ep.meta.get("status", "ok")
        if ep.meta.get("degraded"):
            missing = ep.meta.get("cameras_missing") or {}
            detail = ", ".join(f"{k}×{v}" for k, v in missing.items())
            print(f"  [{ep_index + 1}/{len(paths)}] {path.name}: 跳过（录制时缺相机：{detail}）")
            skipped.append(path.name)
            continue
        state, action, frame_indices, grid = resample(ep, args.fps)
        state12, action12 = trim_to_12d(ep, state, action)
        n = len(grid)
        chunk = ep_index // CHUNKS_SIZE

        print(f"  [{ep_index + 1}/{len(paths)}] {path.name}: {ep.meta['frame_count']} 帧 → {n} 帧"
              f" | {grid[-1]:.2f}s | status={status}")
        if status != "ok":
            print(f"      ⚠️  原始段状态为 {status}，已照常转换但请自行判断是否可用")

        video_info: dict[str, dict] = {}
        for cam in ep.cameras:
            out_video = out_root / VIDEO_PATH.format(episode_chunk=chunk, video_key=f"observation.images.{cam}",
                                                     episode_index=ep_index)
            if args.skip_video:
                first = cv2.imread(str(path / "imgs" / f"{frame_indices[0]:06d}_{cam}.jpg"))
                h, w = first.shape[:2]
                out_video.parent.mkdir(parents=True, exist_ok=True)
                out_video.touch()
                video_info[cam] = {"video.height": h, "video.width": w, "video.codec": "h264",
                                   "video.pix_fmt": "yuv420p", "video.is_depth_map": False,
                                   "video.fps": args.fps, "video.channels": 3, "has_audio": False}
            else:
                video_info[cam] = encode_video(ep, cam, frame_indices, out_video, args.fps, args.crf)
        video_info_by_episode.append(video_info)

        write_parquet(
            out_root / DATA_PATH.format(episode_chunk=chunk, episode_index=ep_index),
            state12, action12,
            timestamps=grid,
            frame_index=np.arange(n),
            episode_index=np.full(n, ep_index),
            index=np.arange(global_index, global_index + n),
            task_index=np.zeros(n),
        )
        global_index += n

        stats = {
            "observation.state": numeric_stats(state12),
            "action": numeric_stats(action12),
            "timestamp": numeric_stats(grid),
        }
        episodes_stats.append({"episode_index": ep_index, "stats": stats})
        episodes_meta.append({"episode_index": ep_index, "tasks": [task], "length": n})

    if not episodes_meta:
        raise SystemExit("没有可转换的 episode（全部被跳过：录制时缺相机）")

    # 所有 episode 共用同一套 feature 形状；用第 0 集的 video_info
    features = build_features(cameras0, video_info_by_episode[0], args.fps)
    info = {
        "codebase_version": CODEBASE_VERSION,
        "robot_type": "xlerobot",
        "total_episodes": len(episodes_meta),
        "total_frames": global_index,
        "total_tasks": 1,
        "total_videos": len(episodes_meta) * len(video_info_by_episode[0]),
        "total_chunks": (len(episodes_meta) - 1) // CHUNKS_SIZE + 1,
        "chunks_size": CHUNKS_SIZE,
        "fps": args.fps,
        "splits": {"train": f"0:{len(episodes_meta)}"},
        "data_path": DATA_PATH,
        "video_path": VIDEO_PATH,
        "features": features,
    }

    write_jsonlines(out_root / "meta/tasks.jsonl", [{"task_index": 0, "task": task}])
    write_jsonlines(out_root / "meta/episodes.jsonl", episodes_meta)
    write_jsonlines(out_root / "meta/episodes_stats.jsonl", episodes_stats)
    (out_root / "meta/info.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")

    aggregate = {
        key: {
            k: np.mean([s["stats"][key][k] for s in episodes_stats], axis=0).tolist()
            for k in ("mean", "std", "min", "max", "count")
        }
        for key in ("observation.state", "action", "timestamp")
    }
    (out_root / "meta/stats.json").write_text(json.dumps(aggregate, ensure_ascii=False, indent=2),
                                              encoding="utf-8")

    if skipped:
        print(f"\n⚠️  跳过 {len(skipped)} 个残缺 episode（录制时缺相机）：{', '.join(skipped)}")
    print(f"\n写入完成：{out_root}（{time.time() - t0:.1f}s）")
    if not args.skip_video:
        validate(out_root, episodes_meta, args.fps)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="原始层 → LeRobot v2.1 数据集")
    ap.add_argument("--raw", default="dsh/out/raw", help="raw_recorder 的输出根目录")
    ap.add_argument("--out", required=True, help="输出的 v2.1 数据集目录")
    ap.add_argument("--task", required=True, help="任务描述（写入 tasks.jsonl，训练时作为 prompt）")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--crf", type=int, default=18, help="h264 质量，越小越好（默认 18）")
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--skip-video", action="store_true", help="只写 parquet 与 meta（快速调试）")
    args = ap.parse_args(argv)
    return convert(args)


if __name__ == "__main__":
    sys.exit(main())
