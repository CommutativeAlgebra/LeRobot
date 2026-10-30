#!/usr/bin/env python
"""用 Florence-2 零样本出 2D 框（不需要 SFT）。

原理：模型输出是一段文本，框被编码成 `<loc_0>..<loc_999>` 这样的量化 token；
`processor.post_process_generation()` 负责把这些 token 还原成原图像素坐标。

用法：
    # 通用检测（框出图里所有物体）
    $P dsh/florence_boxes.py --images a.jpg b.jpg --task "<OD>"

    # 开放词表：只找指定物体
    $P dsh/florence_boxes.py --images a.jpg --task "<OPEN_VOCABULARY_DETECTION>" --phrase apple

    # 只看某个目录下的图，结果存 json
    $P dsh/florence_boxes.py --glob "data/serve/**/*.jpg" --phrase cube --out boxes.json

依赖：transformers（需要装 `.[pi]` 或 `.[transformers-dep]`）
国内记得先：export HF_ENDPOINT=https://hf-mirror.com
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

import torch
from PIL import Image
from transformers import AutoModelForCausalLM, AutoProcessor

# Florence-2 支持的任务 token（输出里带框的几种）
BOX_TASKS = {
    "<OD>": "全图目标检测",
    "<OPEN_VOCABULARY_DETECTION>": "按给定名词找框（需要 --phrase）",
    "<REGION_PROPOSAL>": "只出候选框（label 为空）",
    "<CAPTION_TO_PHRASE_GROUNDING>": "从描述里定位短语",
    "<DENSE_REGION_CAPTION>": "框 + 每个框的描述",
}


def collect_images(args) -> list[Path]:
    paths: list[Path] = []
    for p in args.images or []:
        paths.append(Path(p).expanduser())
    for pattern in args.glob or []:
        paths.extend(Path(p) for p in glob.glob(pattern, recursive=True))
    out, seen = [], set()
    for p in paths:
        if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}:
            if p.resolve() not in seen:
                seen.add(p.resolve())
                out.append(p)
    if not out:
        raise SystemExit("没有找到图片：用 --images 或 --glob 指定")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Florence-2 零样本 2D 框提取")
    ap.add_argument("--images", nargs="+", help="图片路径")
    ap.add_argument("--glob", nargs="+", help="glob 模式，如 'data/**/*.jpg'")
    ap.add_argument("--task", default="<OPEN_VOCABULARY_DETECTION>", choices=sorted(BOX_TASKS))
    ap.add_argument("--phrase", default="", help="开放词表检测的目标名，如 apple")
    ap.add_argument("--model", default="microsoft/Florence-2-large",
                    help="microsoft/Florence-2-base 或 -large")
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    ap.add_argument("--dtype", default="bfloat16", choices=("bfloat16", "float32"))
    ap.add_argument("--max-new-tokens", type=int, default=1024)
    ap.add_argument("--num-beams", type=int, default=3)
    ap.add_argument("--min-area", type=float, default=0.0,
                    help="过滤掉面积小于该值(像素²)的框，用于剔除噪声")
    ap.add_argument("--out", default=None, help="结果写到 json 文件")
    args = ap.parse_args(argv)

    if args.task == "<OPEN_VOCABULARY_DETECTION>" and not args.phrase:
        print("⚠️  开放词表检测没给 --phrase，模型会把它当成通用检测", file=sys.stderr)

    images = collect_images(args)
    dtype = getattr(torch, args.dtype)

    print(f"加载 {args.model}（{args.dtype}，{args.device}）…")
    model = (
        AutoModelForCausalLM.from_pretrained(args.model, trust_remote_code=True, dtype=dtype)
        .to(args.device)
        .eval()
    )
    processor = AutoProcessor.from_pretrained(args.model, trust_remote_code=True)

    prompt = args.task + args.phrase
    results = {}
    for path in images:
        image = Image.open(path).convert("RGB")
        inputs = processor(text=prompt, images=image, return_tensors="pt").to(args.device)
        inputs["pixel_values"] = inputs["pixel_values"].to(dtype)   # 图像转精度，input_ids 保持 long

        with torch.inference_mode():
            generated = model.generate(
                **inputs,
                max_new_tokens=args.max_new_tokens,
                num_beams=args.num_beams,
                do_sample=False,
            )
        text = processor.batch_decode(generated, skip_special_tokens=False)[0]

        # 关键一步：loc token → 像素坐标（image_size 用原图尺寸）
        parsed = processor.post_process_generation(text, task=args.task, image_size=image.size)
        entry = parsed.get(args.task, {}) or {}
        boxes = entry.get("bboxes", []) or []
        labels = entry.get("labels", []) or []

        width, height = image.size
        kept = []
        for box, label in zip(boxes, labels):
            x1, y1, x2, y2 = (float(v) for v in box)
            area = max(0.0, x2 - x1) * max(0.0, y2 - y1)
            if area < args.min_area:
                continue
            kept.append({
                "label": label,
                "box": [round(x1, 1), round(y1, 1), round(x2, 1), round(y2, 1)],
                "box_norm": [round(x1 / width, 4), round(y1 / height, 4),
                             round(x2 / width, 4), round(y2 / height, 4)],
                "center": [round((x1 + x2) / 2, 1), round((y1 + y2) / 2, 1)],
                "area": round(area, 1),
            })

        results[str(path)] = {"raw_text": text, "boxes": kept}
        print(f"{path}  {len(kept)} 个框")
        for b in kept:
            print(f"   {b['label'] or '(无标签)':12s} box={b['box']}  center={b['center']}")

    if args.out:
        Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n已写入 {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
