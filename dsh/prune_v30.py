#!/usr/bin/env python
"""从 LeRobot v3.0 数据集中删除指定 episode。

适用前提：待删除的 episode **独占**它的 data 文件与视频文件（本项目的录制器
每保存一集就 finalize 并重新打开数据集，所以正好是"一集一文件"）。
如果某个文件被其他保留的集共用，脚本会拒绝执行，避免切坏视频。

用法：
    $P dsh/prune_v30.py --root data/serve/local/your_task --drop 2
    $P dsh/prune_v30.py --root ... --drop 1,2 --no-backup
    $P dsh/prune_v30.py --root ... --drop 2 --dry-run
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

_SRC = Path(__file__).resolve().parents[1] / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from lerobot.datasets.compute_stats import aggregate_stats  # noqa: E402
from lerobot.datasets.utils import write_stats  # noqa: E402


def load_episode_rows(root: Path) -> list[tuple[Path, pa.Table]]:
    files = sorted((root / "meta/episodes").rglob("*.parquet"))
    if not files:
        raise SystemExit(f"{root}/meta/episodes 下没有 parquet")
    return [(f, pq.read_table(f)) for f in files]


def table_episodes(table: pa.Table) -> list[int]:
    return [int(x) for x in table.column("episode_index").to_pylist()]


def file_refs(table: pa.Table, row: int, video_keys: list[str]) -> tuple[str | None, dict[str, str]]:
    """返回该行引用的 data 文件与各相机视频文件的相对路径。"""
    data_chunk = table.column("data/chunk_index")[row].as_py()
    data_file = table.column("data/file_index")[row].as_py()
    data_ref = f"data/chunk-{data_chunk:03d}/file-{data_file:03d}.parquet" if data_chunk is not None else None
    video_refs = {}
    for key in video_keys:
        col_c = f"videos/{key}/chunk_index"
        col_f = f"videos/{key}/file_index"
        if col_c in table.column_names:
            c, f = table.column(col_c)[row].as_py(), table.column(col_f)[row].as_py()
            video_refs[key] = f"videos/{key}/chunk-{c:03d}/file-{f:03d}.mp4"
    return data_ref, video_refs


def row_stats(table: pa.Table, row: int, features: list[str]) -> dict:
    out: dict[str, dict[str, np.ndarray]] = {}
    for feat in features:
        entry = {}
        for stat in ("min", "max", "mean", "std", "count", "q01", "q10", "q50", "q90", "q99"):
            col = f"stats/{feat}/{stat}"
            if col in table.column_names:
                value = table.column(col)[row].as_py()
                entry[stat] = np.asarray(value, dtype=np.float64)
        if entry:
            out[feat] = entry
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="删除 v3.0 数据集中的指定 episode")
    ap.add_argument("--root", required=True)
    ap.add_argument("--drop", required=True, help="要删除的 episode_index，逗号分隔")
    ap.add_argument("--dry-run", action="store_true", help="只打印将要做什么")
    ap.add_argument("--no-backup", action="store_true", help="不先备份整个数据集")
    args = ap.parse_args(argv)

    root = Path(args.root).expanduser().resolve()
    if not (root / "meta/info.json").is_file():
        raise SystemExit(f"不是 v3.0 数据集：{root}")
    info = json.loads((root / "meta/info.json").read_text(encoding="utf-8"))
    if info.get("codebase_version") != "v3.0":
        raise SystemExit(f"只支持 v3.0，当前是 {info.get('codebase_version')}")

    drop = {int(x) for x in args.drop.replace(" ", "").split(",") if x}
    video_keys = [k for k, v in info["features"].items() if v["dtype"] == "video"]
    features = [k for k in info["features"]]

    rows = load_episode_rows(root)
    present = {ep for _, t in rows for ep in table_episodes(t)}
    if not drop <= present:
        raise SystemExit(f"这些 episode 不存在：{sorted(drop - present)}；现有 {sorted(present)}")
    if len(present) - len(drop) <= 0:
        raise SystemExit("不能删掉全部 episode")

    # 收集引用，检查共享
    refs_data: dict[str, set[int]] = {}
    refs_video: dict[str, set[int]] = {}
    per_ep: dict[int, tuple[pa.Table, int]] = {}
    for path, table in rows:
        for row, ep in enumerate(table_episodes(table)):
            per_ep[ep] = (table, row)
            data_ref, video_refs = file_refs(table, row, video_keys)
            if data_ref:
                refs_data.setdefault(data_ref, set()).add(ep)
            for key, ref in video_refs.items():
                refs_video.setdefault(f"{key}|{ref}", set()).add(ep)

    shared = []
    for ep in drop:
        data_ref, video_refs = file_refs(per_ep[ep][0], per_ep[ep][1], video_keys)
        for ref, eps in refs_data.items():
            if ep in eps and (eps - drop):
                shared.append(("data", ref, sorted(eps - drop)))
        for key, ref in video_refs.items():
            others = refs_video.get(f"{key}|{ref}", set()) - drop
            if others:
                shared.append(("video", ref, sorted(others)))
    if shared:
        print("❌ 这些文件被要保留的集共用，不能安全删除：")
        for kind, ref, others in shared:
            print(f"   {kind}: {ref} 仍被 ep{others} 使用")
        raise SystemExit("请先重新导出这些集，或改用其他方式裁剪")

    print(f"数据集: {root}")
    print(f"现有 {sorted(present)} → 删除 {sorted(drop)} → 保留 {sorted(present - drop)}")

    files_to_delete: set[Path] = set()
    for ep in drop:
        table, row = per_ep[ep]
        data_ref, video_refs = file_refs(table, row, video_keys)
        if data_ref:
            files_to_delete.add(root / data_ref)
        for ref in video_refs.values():
            files_to_delete.add(root / ref)

    if args.dry_run:
        print("\n[dry-run] 将删除这些文件：")
        for f in sorted(files_to_delete):
            print("  ", f.relative_to(root))
        print("并把 meta/info.json / meta/stats.json / meta/episodes 改为只含保留集")
        return 0

    if not args.no_backup:
        backup = root.with_name(f"{root.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copytree(root, backup)
        print(f"已备份到 {backup}")

    # 1) 删数据/视频文件
    for f in sorted(files_to_delete):
        if f.is_file():
            f.unlink()
            print(f"  删除 {f.relative_to(root)}")

    # 2) 重写/删除 episodes 元数据
    kept_stats = []
    kept_lengths: dict[int, int] = {}
    for path, table in rows:
        mask = pc.invert(pc.is_in(table.column("episode_index"), value_set=pa.array(sorted(drop))))
        kept = table.filter(mask)
        for ep, length in zip(kept.column("episode_index").to_pylist(),
                              kept.column("length").to_pylist()):
            kept_lengths[int(ep)] = int(length)
        if kept.num_rows == 0:
            path.unlink()
            print(f"  删除 {path.relative_to(root)}")
        else:
            pq.write_table(kept, path)

    # 3) 重建 stats.json（按保留集的顺序重新聚合）
    for ep in sorted(present - drop):
        table, row = per_ep[ep]
        kept_stats.append(row_stats(table, row, features))
    if kept_stats:
        write_stats(aggregate_stats(kept_stats), root)

    # 4) 更新 info.json
    info["total_episodes"] = len(kept_lengths)
    info["total_frames"] = int(sum(kept_lengths.values()))
    info["total_videos"] = info["total_episodes"] * len(video_keys)
    info["splits"] = {"train": f"0:{info['total_episodes']}"}
    (root / "meta/info.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  更新 meta/info.json → {info['total_episodes']} 集 / {info['total_frames']} 帧")

    # 5) 验证
    from lerobot.datasets.lerobot_dataset import LeRobotDataset

    ds = LeRobotDataset(info.get("repo_id") or f"local/{root.name}", root=root, video_backend="pyav")
    print(f"\n✅ 校验：{ds.num_episodes} 集 / {ds.num_frames} 帧 / {ds.fps}fps")
    ds.finalize()
    return 0


if __name__ == "__main__":
    sys.exit(main())
