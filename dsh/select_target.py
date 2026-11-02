#!/usr/bin/env python
"""从多个同类目标里挑出指定的那一个：按框对角线中点（即框中心）的横坐标从左到右排序。

场景：图里有 3 个一模一样的红杯子，Florence-2 给出 3 个框但顺序是乱的。
本脚本把它们按中心 x 从小到大排好，然后取 `--which` 指定的那一个。

用法：
    # 1) 先出框（florence_boxes.py 的 --out 格式）
    $P dsh/florence_boxes.py --images a.jpg \
        --task "<OPEN_VOCABULARY_DETECTION>" --phrase "red cup" --out boxes.json

    # 2) 选中间那个
    $P dsh/select_target.py --in boxes.json --which middle

    # 一步到位：脚本内部会去调 florence_boxes.py
    $P dsh/select_target.py --image a.jpg --phrase "red cup" --which 1

    # 手写框快速验证
    $P dsh/select_target.py --boxes "10,10,50,60; 80,12,120,58; 150,9,190,61" --which left

选择器（--which）：
    middle / center   中间那个（要求奇数个且 ≥3；偶数个取 n//2，即偏右）
    left / leftmost   最左
    right / rightmost 最右
    2nd / third ...   从左边数第几个（1 起，口语）
    0 1 2 ...         从左边数第几个（0 起，脚本接口）
    -1 -2 ...         从右边数第几个（-1 最右）

数量不足时（要第 5 个但只有 3 个、要 middle 但只有 1 个）：
    默认 clamp —— 播报 "only 3 apples" / "only one apple"，然后夹最近的那个（通常是最后一个）；
    --on-mismatch error 则直接退出（离线质检用）。

只依赖标准库；--annotate 画图时才需要 Pillow。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

Box = list[float]
Item = dict[str, Any]

# ---------------------------------------------------------------- 几何小工具


def box_center(box: Box) -> tuple[float, float]:
    """对角线中点 == 框中心。"""
    x1, y1, x2, y2 = box
    return (x1 + x2) / 2.0, (y1 + y2) / 2.0


def box_size(box: Box) -> tuple[float, float]:
    x1, y1, x2, y2 = box
    return abs(x2 - x1), abs(y2 - y1)


def box_area(box: Box) -> float:
    w, h = box_size(box)
    return w * h


def iou(a: Box, b: Box) -> float:
    ax1, ay1, ax2, ay2 = min(a[0], a[2]), min(a[1], a[3]), max(a[0], a[2]), max(a[1], a[3])
    bx1, by1, bx2, by2 = min(b[0], b[2]), min(b[1], b[3]), max(b[0], b[2]), max(b[1], b[3])
    ix = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    iy = max(0.0, min(ay2, by2) - max(ay1, by1))
    inter = ix * iy
    union = box_area(a) + box_area(b) - inter
    return inter / union if union > 0 else 0.0


# ---------------------------------------------------------------- 预处理


def normalize_items(raw: Any) -> list[Item]:
    """把各种输入形态统一成 [{"label": str, "box": [x1,y1,x2,y2]}, ...]。

    支持：
      - florence_boxes.py 的 {"<path>": {"boxes": [...]}}（多图时需先用 --image-key 选一张）
      - {"boxes": [...]} / {"<task>": {"bboxes": [...], "labels": [...]}}（post_process_generation 原样）
      - [{"box": [...], "label": ...}, ...]
      - [[x1,y1,x2,y2], ...]
    """
    if isinstance(raw, dict):
        if "boxes" in raw:
            return normalize_items(raw["boxes"])
        if "bboxes" in raw:  # post_process_generation 的 {'bboxes': [[..]], 'labels': [..]}
            labels = raw.get("labels") or raw.get("bboxes_labels") or []
            return [
                {"label": str(labels[i]) if i < len(labels) else "", "box": [float(v) for v in b]}
                for i, b in enumerate(raw["bboxes"])
            ]
        raise ValueError("看不懂的 JSON 结构：顶层 dict 里既没有 boxes 也没有 bboxes")

    if not isinstance(raw, list):
        raise ValueError(f"看不懂的 JSON 结构：{type(raw).__name__}")
    if not raw:
        return []

    out: list[Item] = []
    for e in raw:
        if isinstance(e, dict):
            box = e.get("box") or e.get("bbox") or e.get("bboxes")
            if box is None:
                raise ValueError(f"条目缺少 box 字段：{e}")
            box = list(box)
            # post_process 的 bboxes 是嵌套的 [[x1,y1,x2,y2]]，展平一层
            if box and isinstance(box[0], (list, tuple)):
                box = list(box[0])
            out.append({"label": str(e.get("label", "") or ""), "box": [float(v) for v in box]})
        elif isinstance(e, (list, tuple)):
            out.append({"label": "", "box": [float(v) for v in e]})
        else:
            raise ValueError(f"看不懂的条目：{e!r}")

    for it in out:
        if len(it["box"]) != 4:
            raise ValueError(f"框必须是 4 个数 (x1,y1,x2,y2)：{it['box']}")
    return out


def load_from_json(path: Path, image_key: str | None) -> list[Item]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    # florence_boxes.py 的输出：{图片路径: {"raw_text":..., "boxes":[...]}}
    if isinstance(raw, dict) and raw and "boxes" not in raw and "bboxes" not in raw:
        keys = list(raw)
        if image_key is None:
            if len(keys) != 1:
                raise SystemExit(
                    f"{path} 里有 {len(keys)} 张图，用 --image-key 指定其中一张：\n  "
                    + "\n  ".join(keys[:10])
                )
            image_key = keys[0]
        if image_key not in raw:
            raise SystemExit(f"{path} 里没有 {image_key}，可选：{keys[:10]}")
        payload = raw[image_key]
        if isinstance(payload, dict) and "boxes" not in payload and "bboxes" in payload:
            return normalize_items(payload)
        return normalize_items(payload.get("boxes", []) if isinstance(payload, dict) else payload)
    return normalize_items(raw)


def filter_and_dedupe(
    items: list[Item],
    label: str | None = None,
    min_area: float = 0.0,
    iou_thr: float = 0.85,
) -> list[Item]:
    """按标签/面积过滤，并去掉高度重叠的重复框（保留大的那个）。"""
    kept = items
    if label:
        want = label.strip().lower()
        kept = [it for it in kept if it["label"].strip().lower() == want]
        if not kept:  # 精确匹配不上就退回子串匹配，兼容 "red cup" vs "cup"
            kept = [it for it in items if want in it["label"].strip().lower()]
    if min_area > 0:
        kept = [it for it in kept if box_area(it["box"]) >= min_area]

    kept = sorted(kept, key=lambda it: box_area(it["box"]), reverse=True)
    out: list[Item] = []
    for it in kept:
        if all(iou(it["box"], k["box"]) < iou_thr for k in out):
            out.append(it)
    return out


# ---------------------------------------------------------------- 排序与选择


def sort_left_to_right(items: list[Item], row_tol: float | None = None) -> list[Item]:
    """按中心 x 升序。row_tol 非 None 时先按行分组（行内再排），多行场景用。"""
    for it in items:
        it["box"] = [min(it["box"][0], it["box"][2]), min(it["box"][1], it["box"][3]),
                     max(it["box"][0], it["box"][2]), max(it["box"][1], it["box"][3])]
        it["center"] = box_center(it["box"])

    if row_tol is None:
        return sorted(items, key=lambda it: (it["center"][0], it["center"][1]))

    heights = [box_size(it["box"])[1] for it in items]
    tol = row_tol * (sum(heights) / len(heights) if heights else 1.0)
    rows: list[list[Item]] = []
    for it in sorted(items, key=lambda it: it["center"][1]):
        if rows and abs(it["center"][1] - sum(r["center"][1] for r in rows[-1]) / len(rows[-1])) <= tol:
            rows[-1].append(it)
        else:
            rows.append([it])
    ordered: list[Item] = []
    for row in rows:  # 行从上到下，行内从左到右
        ordered.extend(sorted(row, key=lambda it: (it["center"][0], it["center"][1])))
    return ordered


ORDINALS = {
    "first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3,
    "fourth": 4, "4th": 4, "fifth": 5, "5th": 5, "sixth": 6, "6th": 6,
    "seventh": 7, "7th": 7, "eighth": 8, "8th": 8, "ninth": 9, "9th": 9,
    "tenth": 10, "10th": 10,
}

# middle 要几个目标才算"有中间那个"：奇数且 ≥3
MIDDLE_MIN_COUNT = 3


def parse_selector(which: str) -> dict[str, Any]:
    """把 --which 解析成 {kind, value, required}，required 是"要满足这个选择器至少需要几个框"。"""
    s = str(which).strip().lower()
    if s in ("middle", "center", "centre", "mid"):
        return {"kind": "middle", "value": None, "required": MIDDLE_MIN_COUNT}
    if s in ("left", "leftmost", "first-from-left"):
        return {"kind": "edge", "value": "left", "required": 1}
    if s in ("right", "rightmost", "last"):
        return {"kind": "edge", "value": "right", "required": 1}
    if s in ORDINALS:                      # "2nd" / "third"：从 1 数
        k = ORDINALS[s]
        return {"kind": "index", "value": k - 1, "required": k}
    try:
        i = int(s)                         # 纯数字：0 起下标（兼容旧用法，负数从右数）
    except ValueError:
        raise SystemExit(
            f"不认识的 --which：{which!r}。可用：middle/left/right、序数(2nd/third)、整数下标"
        ) from None
    return {"kind": "index", "value": i, "required": (i + 1 if i >= 0 else -i)}


def resolve_index(spec: dict[str, Any], n: int) -> tuple[int, bool]:
    """返回 (实际采用的 0-based 下标, 是否精确满足)。不精确时由调用方夹取到边界。"""
    kind, value = spec["kind"], spec["value"]

    if kind == "edge":
        return (0 if value == "left" else n - 1), True

    if kind == "middle":
        idx = n // 2
        exact = n >= MIDDLE_MIN_COUNT and n % 2 == 1   # 偶数个没有"中间那个"
        return idx, exact

    # kind == "index"
    if value >= 0:
        return min(value, n - 1), value < n
    return max(n + value, 0), -value <= n


def _plural(noun: str) -> str:
    if not noun:
        return "objects"
    if noun.endswith(("s", "x", "ch", "sh")):
        return noun + "es"
    if noun.endswith("y") and len(noun) > 1 and noun[-2] not in "aeiou":
        return noun[:-1] + "ies"
    return noun + "s"


def count_message(n: int, noun: str) -> str:
    """人话播报：only one apple / only 3 apples。"""
    if n == 1:
        return f"only one {noun or 'object'}"
    return f"only {n} {_plural(noun)}"


def _pick_phrase(spec: dict[str, Any], idx: int, n: int) -> str:
    if n == 1:
        return "the only one"
    if spec["kind"] == "edge":
        return "the leftmost one" if spec["value"] == "left" else "the rightmost one"
    if spec["kind"] == "middle":
        if n % 2 == 1:
            return "the middle one"
        return "the right one of the two" if n == 2 else "the one just right of centre"
    return "the last one" if idx == n - 1 else f"number {idx + 1} from the left"


def select(
    items: list[Item],
    which: str = "middle",
    row_tol: float | None = None,
    on_mismatch: str = "clamp",
    noun: str | None = None,
) -> dict[str, Any]:
    """选出目标。数量不足时默认夹取（clamp）并给出人话提示，而不是报错。"""
    if not items:
        raise SystemExit("没有可用框：先确认目标真的在画面里，或放宽 --min-area / --label")

    spec = parse_selector(which)
    ranked = sort_left_to_right(items, row_tol=row_tol)
    n = len(ranked)
    idx, exact = resolve_index(spec, n)

    noun = (noun or ranked[idx]["label"] or "").strip()
    message = None if exact else count_message(n, noun)

    if not exact and on_mismatch == "error":
        print(f"⚠️  {message}（--on-mismatch=error，不选取）", file=sys.stderr)
        raise SystemExit(2)

    chosen = ranked[idx] if 0 <= idx < n else ranked[-1]
    cx, cy = chosen["center"]
    return {
        "selector": str(which),
        "count": n,
        "requested": {"required": spec["required"], "kind": spec["kind"]},
        "exact": exact,
        "message": message,          # 给 TTS/UI 播报："only one apple"
        "picked": _pick_phrase(spec, idx, n),
        "selected_index": ranked.index(chosen),
        "selected": {
            "rank": ranked.index(chosen),
            "equal_from_right": n - 1 - ranked.index(chosen),
            "label": chosen["label"],
            "box": [round(v, 2) for v in chosen["box"]],
            "center": [round(cx, 2), round(cy, 2)],
            "area": round(box_area(chosen["box"]), 1),
        },
        "ranked": [
            {
                "rank": i,
                "label": it["label"],
                "box": [round(v, 2) for v in it["box"]],
                "center_x": round(it["center"][0], 2),
                "center_y": round(it["center"][1], 2),
            }
            for i, it in enumerate(ranked)
        ],
    }


# ---------------------------------------------------------------- 输出


# 选中框用亮绿：和红/蓝/木色物体都有强反差（用红色画红杯子等于没画）
COLOR_SELECTED = (0, 255, 128)
COLOR_OTHER = (190, 190, 190)
FILL_SELECTED = (0, 255, 0)


def line_width_px(size: tuple[int, int], ratio: float = 0.008, minimum: int = 2) -> int:
    """按图短边算线宽：0.8% 短边 → 640×480 上是 4 px，1280×720 上是 6 px。"""
    w, h = size
    return max(minimum, int(round(ratio * min(w, h))))


def draw(
    items: list[Item],
    selected_index: int,
    out_path: Path,
    image_path: Path,
    for_model: bool = False,
    line_width: int | None = None,
    fill_alpha: float | None = None,
) -> int:
    """把排序结果画到图上。返回实际用的线宽。

    for_model=True 时用"给 π0.5 看"的参数：线更粗 + 半透明填充、不写字。
    （图会被 resize_with_pad 到 224，细线会被插值抹掉，只能靠填充撑住信号。）
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        raise SystemExit("--annotate 需要 Pillow：pip install pillow") from None

    img = Image.open(image_path).convert("RGB")
    W, H = img.size
    if line_width is None:
        line_width = line_width_px((W, H), 0.008 if for_model else 0.004)
    if fill_alpha is None:
        fill_alpha = 0.20 if for_model else 0.0

    w_sel = line_width
    w_other = max(1, line_width // 2)

    # ① 先铺半透明填充（只填选中的），再画线，保证边框清晰
    if fill_alpha > 0:
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        x1, y1, x2, y2 = items[selected_index]["box"]
        ld.rectangle([x1, y1, x2, y2], fill=(*FILL_SELECTED, int(255 * fill_alpha)))
        img = Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")

    draw_ctx = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", max(12, line_width * 4))
    except OSError:
        font = ImageFont.load_default()

    for i, it in enumerate(items):
        x1, y1, x2, y2 = it["box"]
        is_sel = i == selected_index
        color = COLOR_SELECTED if is_sel else COLOR_OTHER
        # 线框画在框内侧，避免超出图像边界
        hw = (w_sel if is_sel else w_other) // 2
        draw_ctx.rectangle([x1 + hw, y1 + hw, x2 - hw, y2 - hw],
                           outline=color, width=w_sel if is_sel else w_other)
        if not for_model:
            tag = f"{i}" + ("  <== target" if is_sel else "")
            draw_ctx.text((x1 + 3, max(0.0, y1 - line_width * 6)), tag, fill=color, font=font)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    print(f"标注图已写入 {out_path}（{W}×{H}，线宽 {w_sel}px"
          f"{f'，填充 α={fill_alpha:.2f}' if fill_alpha else ''}）")
    return w_sel


# ---------------------------------------------------------------- CLI


def call_florence(image: Path, phrase: str, task: str, model: str, cache: Path) -> list[Item]:
    """调 dsh/florence_boxes.py 出框，再读回 JSON。"""
    script = Path(__file__).with_name("florence_boxes.py")
    if not script.is_file():
        raise SystemExit(f"找不到 {script}")
    cmd = [sys.executable, str(script), "--images", str(image), "--task", task,
           "--phrase", phrase, "--model", model, "--out", str(cache)]
    print("→ " + " ".join(cmd), file=sys.stderr)
    proc = subprocess.run(cmd, check=False)
    if proc.returncode != 0:
        raise SystemExit(f"florence_boxes.py 退出码 {proc.returncode}")
    return load_from_json(cache, image_key=str(image))


def parse_inline_boxes(spec: str) -> list[Item]:
    out: list[Item] = []
    for chunk in spec.replace(";", "\n").splitlines():
        chunk = chunk.strip()
        if not chunk:
            continue
        vals = [float(v) for v in chunk.replace(",", " ").split()]
        if len(vals) != 4:
            raise SystemExit(f"--boxes 每段要 4 个数：{chunk!r}")
        out.append({"label": "", "box": vals})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="按框中心横坐标从左到右排序，选出指定的一个",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    src = ap.add_argument_group("框的来源（三选一）")
    src.add_argument("--in", dest="in_json", type=Path, help="florence_boxes.py 的 --out JSON")
    src.add_argument("--image-key", default=None, help="多图 JSON 里选哪张图（默认要求只有一张）")
    src.add_argument("--boxes", help='手写框："x1,y1,x2,y2; x1,y1,x2,y2"')
    src.add_argument("--image", type=Path, help="直接给图，内部调 florence_boxes.py 出框")
    src.add_argument("--phrase", default="object", help="--image 模式的检索词（英文）")
    src.add_argument("--task", default="<OPEN_VOCABULARY_DETECTION>", help="--image 模式的任务 token")
    src.add_argument("--model", default="microsoft/Florence-2-large", help="--image 模式的模型")

    sel = ap.add_argument_group("选择与过滤")
    sel.add_argument("--which", default="middle",
                     help="middle/left/right、序数 2nd/third、下标 0,1,2 / -1,-2（默认 middle）")
    sel.add_argument("--noun", default=None, help='播报用的名词，如 "apple"（默认取框的 label）')
    sel.add_argument("--on-mismatch", choices=("clamp", "error"), default="clamp",
                     help="目标数不足时的行为：clamp=夹最近的一个并播报（默认），error=退出码 2")
    sel.add_argument("--label", default=None, help="只保留该标签（大小写不敏感）")
    sel.add_argument("--min-area", type=float, default=0.0, help="过滤掉面积小于该值的框（像素²）")
    sel.add_argument("--iou", type=float, default=0.85, help="去重阈值：IoU 超过它就合并（默认 0.85）")
    sel.add_argument("--row-tol", type=float, default=None,
                     help="多行摆放时先分行：行容差 = 该值 × 平均框高（如 0.6）")
    sel.add_argument("--expect", type=int, default=None,
                     help="[离线质检] 期望的框数量；不符只告警，不中断")
    sel.add_argument("--strict", action="store_true",
                     help="[已废弃] 等价于 --on-mismatch error")

    out = ap.add_argument_group("输出")
    out.add_argument("--out", type=Path, default=None, help="把选择结果写成 JSON")
    out.add_argument("--annotate", type=Path, default=None, help="画出排序结果（需要 --image 或 --annotate-image）")
    out.add_argument("--annotate-image", type=Path, default=None, help="配合 --in 时，指定底图")
    out.add_argument("--annotate-for", choices=("human", "model"), default="human",
                     help="human=细线+编号（默认）；model=粗线+半透明填充、不写字，用于喂 π0.5")
    out.add_argument("--line-width", type=int, default=None,
                     help="线宽（像素）。默认按短边 0.4%%(human) / 0.8%%(model) 自动取")
    out.add_argument("--fill-alpha", type=float, default=None,
                     help="选中框的填充透明度 0~1。默认 human=0，model=0.2")
    out.add_argument("--quiet", action="store_true", help="只输出选中的框")

    args = ap.parse_args(argv)

    # 1) 取框
    if args.image:
        cache = Path(args.out).with_suffix(".boxes.json") if args.out else Path("dsh/out/_boxes.json")
        cache.parent.mkdir(parents=True, exist_ok=True)
        items = call_florence(args.image, args.phrase, args.task, args.model, cache)
    elif args.in_json:
        items = load_from_json(args.in_json, args.image_key)
    elif args.boxes:
        items = parse_inline_boxes(args.boxes)
    else:
        ap.error("要给出框的来源：--in、--image 或 --boxes")

    # 2) 过滤 + 去重
    n_raw = len(items)
    items = filter_and_dedupe(items, label=args.label, min_area=args.min_area, iou_thr=args.iou)

    # 3) [离线质检] 数量校验：只告警，不阻断
    if args.expect is not None and len(items) != args.expect:
        msg = f"⚠️  期望 {args.expect} 个框，实际 {len(items)} 个（原始 {n_raw} 个，过滤后）"
        print(msg, file=sys.stderr)

    # 4) 选择（数量不足时默认夹取并播报，不报错）
    on_mismatch = "error" if args.strict else args.on_mismatch
    result = select(items, which=args.which, row_tol=args.row_tol,
                    on_mismatch=on_mismatch, noun=args.noun)
    ranked = result["ranked"]

    # 5) 打印
    if result["message"]:
        print(f"🔈 {result['message']}  → 夹 {result['picked']}")
    if not args.quiet:
        print(f"\n共 {result['count']} 个框，按中心 x 从左到右：")
        for r in ranked:
            mark = "  ← 选中" if r["rank"] == result["selected_index"] else ""
            label = r["label"] or "(无标签)"
            print(f"  [{r['rank']}] {label:14s} x={r['center_x']:8.2f}  y={r['center_y']:8.2f}"
                  f"  box={r['box']}{mark}")

    s = result["selected"]
    print(f"\n选中：第 {s['rank']} 个（0 起，从右边数是第 {s['equal_from_right']} 个）")
    print(f"  label = {s['label'] or '(无标签)'}")
    print(f"  box   = {s['box']}   # x1,y1,x2,y2（像素）")
    print(f"  center= {s['center']}   # 对角线中点")
    print(f"  area  = {s['area']}")

    # 6) 落盘
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n结果已写入 {args.out}")

    if args.annotate:
        base = args.annotate_image or args.image
        if base is None:
            raise SystemExit("--annotate 需要底图：给 --image 或 --annotate-image")
        # 必须按排好序的列表画，否则框上的序号和 [rank] 对不上
        draw(sort_left_to_right(items, row_tol=args.row_tol), result["selected_index"],
             args.annotate, base, for_model=(args.annotate_for == "model"),
             line_width=args.line_width, fill_alpha=args.fill_alpha)

    return 0


if __name__ == "__main__":
    sys.exit(main())
