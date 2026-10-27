# 现状盘点与实施计划

核对日期：2026-10-26

---

## 1. 现状

| 环节 | 状态 | 位置 |
|---|---|---|
| 遥操（Xbox / Joycon） | ✅ 可用 | 本仓库 `examples/xlerobot/5_*.py`、`7_*.py` |
| 状态源 `--publish-state` | ✅ 已实现 | 并行 checkout `XLeRobot`（**未提交**） |
| 数采 UI | ✅ 已实现 | 同上，写 **LeRobot v3.0** |
| 已有数据 | ❌ **零** | `~/xhz/XLeRobot/LeRobot/data/recordings` 不存在；全盘无 `meta/info.json` |
| v3.0 → v2.1 转换 | ❌ 缺 | — |
| openpi 适配（Inputs/Outputs/TrainConfig） | ❌ 缺 | 本仓库无 `xlerobot_policy.py` |
| norm stats | ❌ 缺 | 依赖转换产物 |

本工作区 `XLeRobot_wmf` **没有**数采系统；它只存在于并行 checkout `XLeRobot`。

---

## 2. 已确定的目标决策

| 项 | 决策 | 理由 |
|---|---|---|
| 受控维度 | **12 维**（5 关节+夹爪 ×2） | 头部/轮子固定；5 个常量维度会让 quantile norm 的 `q99-q01=0`，把噪声放大成满幅 |
| 数据集格式 | **LeRobot v2.1** | openpi 钉的 lerobot commit `0cf8648` 是 v2.1；本仓库产 v3.0，读不了 |
| 频率 | **30 Hz** | `action_horizon=50` @30Hz ≈ 1.67s，匹配 pi0.5 默认视野 |
| action 表示 | **绝对关节角** | delta 由 `DeltaActions(make_bool_mask(5,-1,5,-1))` 在训练侧做，推理侧 `AbsoluteActions` 还原 |
| 动作来源 | **`send_action()` 返回值** | 唯一真值（限幅后实际写入电机），现有 `state_stream` 已实现 |

---

## 3. 缺口与实施顺序

### ① `v3_0_to_v2_1.py`（先做）

输入确定（v3.0 schema 在 `meta/info.json`），后续步骤都依赖它。

- 按 episode 切分（v3.0 所有 episode 拼在同一 parquet）
- 用 `source_sequence` 去重
- 重采样到 30Hz：state 线性插值 / action 最近邻 ZOH / image 最近帧
- 裁到 12 维（保持 `[L5, Lgrip, R5, Rgrip]` 顺序）
- **切视频**：v3.0 每路相机每 chunk 一个 mp4，v2.1 要每 episode 一个 → 按 episode 时间区间重编码（`-c copy` 只能切关键帧，会引入 GOP 偏移）
- 写 v2.1 meta：`info.json`(`codebase_version: v2.1`)、`tasks.jsonl`、`episodes.jsonl`、`episodes_stats.jsonl`、`stats.json`
- 回读校验：episode 数、帧数、fps、键名、数值范围、`timestamp` 等间隔

### ② `compute_norm_stats.py`

在 openpi 侧对转换后的数据集跑，产出 q01/q99。pi0.5 用 quantile norm。

### ③ `xlerobot_policy.py`（openpi 侧）

```python
RepackTransform({
  "images": {"base_0_rgb": "observation.images.head",
             "left_wrist_0_rgb": "observation.images.left_wrist",
             "right_wrist_0_rgb": "observation.images.right_wrist"},
  "state": "observation.state", "actions": "action"})
```

- `XLeRobotInputs`：HWC uint8、构建 `image_mask`、state float32
- `XLeRobotOutputs`：`actions[:, :12]` 切片
- `TrainConfig(model=Pi0Config(pi05=True), data=..., prompt_from_task=True)`

### ④ 推理客户端改 12 维

`examples/xlerobot/9_xlerobot_policy_client.py` 目前写死 17 个 `_STATE_KEYS`（含 head 2 + base 3），与 12 维模型不匹配。同时推理循环需**显式补** `head = start_pose`、`x/y/theta.vel = 0`。

### ⑤ `train.py --config-name=pi05_xlerobot`

---

## 4. 待确认 / 风险

| 项 | 说明 |
|---|---|
| **Xbox 夹爪几乎不动** | `5_xlerobot_teleop_xbox.py` 中扳机目标 `2.0` vs `zero_pos`（左 2.77 / 右 0.58），0~100 量程上只走 0.77%。用它录抓取任务会废。**建议用 Joycon，或先修这两行** |
| 采集分辨率 | 640×480 → 224×224 上下留白 25%，建议采集端改 480×480 |
| 提示词 | 每任务一句英文描述，写入 `.tasks/<task>.json`；太长会挤掉动作 token（`max_token_len=200`） |
| 数据量 | pi0.5 微调通常需数十条以上演示；少于 20 条基本学不会 |
| 越界 action | `send_action` 返回未钳制值（电机侧 `_unnormalize` 会静默 clamp 到 ±100），落盘前应自行 clamp |
| 动作配对时差 | action 是锁存值，可能比 state 早一个控制周期（~33ms）；可用 `action_age_s` 过滤 |

---

## 5. 验收标准

转换产物需满足：

- [ ] `meta/info.json` 中 `codebase_version == "v2.1"`、`fps == 30`
- [ ] `observation.state` / `action` 形状均为 `(12,)`，顺序 `[L5, Lgrip, R5, Rgrip]`
- [ ] 三路视频键 `observation.images.{head,left_wrist,right_wrist}`
- [ ] 每 episode 一个 mp4，时长与帧数严格对应
- [ ] `timestamp` 严格等间隔 `1/30`
- [ ] 数值范围：臂 `|v| ≤ 100`，夹爪 `0 ≤ v ≤ 100`
- [ ] 用 openpi 的 `LeRobotDataset` + `RepackTransform` 能成功取出一个 batch
