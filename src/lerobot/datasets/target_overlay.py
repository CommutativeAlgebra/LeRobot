#!/usr/bin/env python

# Copyright 2024 The HuggingFace Inc. team. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""在读取 LeRobot v3.0 数据时，把目标框画到指定的相机画面上（训练时用）。

为什么要"读的时候画"而不是"录的时候烧进视频"：
- 视频已经编码成 mp4，烧进去要重编码一遍，体积翻倍、二次有损；
- 框的样式（线宽/颜色/填充/丢弃概率）要能随时调，画在内存里才灵活；
- 训练时随机丢弃框（drop_prob）是重要的鲁棒性手段，烧进去就做不到。

数据要求：数据集里有
    annotation.target_bbox   float32 (4,)   归一化 [0,1] 的 x1,y1,x2,y2
    annotation.target_valid  int64   (1,)   0 表示这一帧没有可用框
两列都可以缺；缺了就不画（只告警一次）。

用法（训练）：
    lerobot-train ... --dataset.target_overlay.enable=true

用法（代码）：
    from lerobot.datasets.target_overlay import TargetBoxOverlay, TargetOverlayConfig
    ds = TargetBoxOverlay(LeRobotDataset("..."), TargetOverlayConfig(enable=True))
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import torch
from torch.utils.data import Dataset


@dataclass
class TargetOverlayConfig:
    """目标框叠加的配置。所有样式参数都要和推理端保持一致。"""

    enable: bool = False
    # 画在哪一路相机上。框的坐标是这一路的像素坐标（归一化后与被画的分辨率无关）。
    camera: str = "observation.images.head"
    bbox_key: str = "annotation.target_bbox"
    valid_key: str = "annotation.target_valid"
    # 框颜色。默认亮绿：和红/蓝/木色物体反差大。别用和物体同色的颜色。
    color: list[int] = field(default_factory=lambda: [0, 255, 128])
    fill_color: list[int] = field(default_factory=lambda: [0, 255, 0])
    # 填充透明度：0 只画线（细线在 224 下会被插值抹掉，不建议低于 0.1）
    fill_alpha: float = 0.20
    # 线宽 = max(min_line_px, line_ratio × 短边)。0.008×480 ≈ 4 px
    line_ratio: float = 0.008
    min_line_px: int = 2
    # 训练时随机丢弃框的比例（模拟检测失败）
    drop_prob: float = 0.0


def _as_rgb_float(color: list[int] | tuple[int, int, int], device, dtype) -> torch.Tensor:
    t = torch.tensor(color[:3], device=device, dtype=torch.float32) / 255.0
    return t.to(dtype)


def draw_box(
    img: torch.Tensor,
    box: torch.Tensor | list[float],
    config: TargetOverlayConfig | None = None,
    *,
    normalized: bool = True,
) -> torch.Tensor:
    """在 [C,H,W] 的图上画一个框，返回新张量（不改原图）。

    img: float [0,1] 或 uint8 [0,255]，CHW。
    box: x1,y1,x2,y2；normalized=True 时是 [0,1] 归一化坐标。
    """
    cfg = config or TargetOverlayConfig()
    if img.ndim != 3:
        raise ValueError(f"期望 CHW 图像，拿到 shape={tuple(img.shape)}")

    _, height, width = img.shape
    b = torch.as_tensor(box, dtype=torch.float32, device=img.device).reshape(-1)[:4]
    x1, y1, x2, y2 = b.tolist()
    if normalized:
        x1, x2 = x1 * width, x2 * width
        y1, y2 = y1 * height, y2 * height

    # 归一化成左上/右下并裁到画面内（模型偶尔吐反向框）
    x1, x2 = sorted((x1, x2))
    y1, y2 = sorted((y1, y2))
    xi1, xi2 = max(0, int(round(x1))), min(width, int(round(x2)))
    yi1, yi2 = max(0, int(round(y1))), min(height, int(round(y2)))
    if xi2 - xi1 < 2 or yi2 - yi1 < 2:
        return img  # 退化框，画了也没意义

    is_float = img.is_floating_point()
    scale = 1.0 if is_float else 255.0
    line = max(cfg.min_line_px, int(round(cfg.line_ratio * min(height, width))))
    out = img.clone()

    # ① 半透明填充：模型真正能感知到的信号（细线缩放后会消失）
    if cfg.fill_alpha > 0:
        fill = _as_rgb_float(cfg.fill_color, img.device, img.dtype) * scale
        region = out[:, yi1:yi2, xi1:xi2]
        out[:, yi1:yi2, xi1:xi2] = region * (1.0 - cfg.fill_alpha) + fill[:, None, None] * cfg.fill_alpha

    # ② 边框：画在框内侧，避免越界
    edge = _as_rgb_float(cfg.color, img.device, img.dtype) * scale
    h = min(line, max(1, (yi2 - yi1) // 2))
    w = min(line, max(1, (xi2 - xi1) // 2))
    out[:, yi1:yi1 + h, xi1:xi2] = edge[:, None, None]
    out[:, yi2 - h:yi2, xi1:xi2] = edge[:, None, None]
    out[:, yi1:yi2, xi1:xi1 + w] = edge[:, None, None]
    out[:, yi1:yi2, xi2 - w:xi2] = edge[:, None, None]
    return out


class TargetBoxOverlay(Dataset):
    """包一层数据集，在 __getitem__ 里把 annotation 里的框画到画面上。

    其余属性（meta、features、stats、num_frames…）都透传给原数据集，
    所以可以直接丢给 lerobot 的 DataLoader / 训练循环。
    """

    def __init__(self, dataset: Dataset, config: TargetOverlayConfig | None = None):
        self.dataset = dataset
        self.config = config or TargetOverlayConfig(enable=True)
        self._warned: set[str] = set()

    # ---- 透传 ----
    def __len__(self) -> int:
        return len(self.dataset)

    def __getattr__(self, name: str) -> Any:
        # __getattr__ 只在常规查找失败时调用，self.dataset 在 __init__ 里已赋值
        if name in {"dataset", "config", "_warned"}:
            raise AttributeError(name)
        return getattr(self.dataset, name)

    # ---- 核心 ----
    def _warn_once(self, key: str, msg: str) -> None:
        if key not in self._warned:
            self._warned.add(key)
            logging.warning(msg)

    def __getitem__(self, idx: int) -> dict[str, Any]:
        item = self.dataset[idx]
        cfg = self.config

        cam = cfg.camera
        if cam not in item:
            return item

        box = item.get(cfg.bbox_key)
        valid = item.get(cfg.valid_key)
        if box is None:
            self._warn_once(
                "no_bbox",
                f"数据集里没有 '{cfg.bbox_key}' 列，TargetBoxOverlay 不生效。"
                "先跑标注脚本把框写进 v3.0 数据集。",
            )
            return item
        if valid is not None and float(torch.as_tensor(valid).reshape(-1)[0]) <= 0:
            return item
        if cfg.drop_prob > 0 and torch.rand(()) < cfg.drop_prob:
            return item  # 随机丢弃：训练模型在没框时也能干活

        item = dict(item)
        item[cam] = draw_box(item[cam], box, cfg, normalized=True)
        return item
