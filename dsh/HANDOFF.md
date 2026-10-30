# XLeRobot → π0.5 数据管线与真机适配 · 交接文档

> 面向项目组所有成员。读完这份就能独立跑通采集、检查数据、训练和排错。
> 最后更新：2026-10-30

---

## 目录

- [0. 项目背景与名词表](#0-项目背景与名词表)
- [1. 五分钟上手](#1-五分钟上手)
- [2. 系统架构](#2-系统架构)
- [3. 代码地图](#3-代码地图)
- [4. 数据集说明](#4-数据集说明)
- [5. 操作手册](#5-操作手册)
- [6. 硬件与已知问题](#6-硬件与已知问题)
- [7. 关键设计决策](#7-关键设计决策)
- [8. 训练 π0.5](#8-训练-pi05)
- [9. 研究结论摘要](#9-研究结论摘要)
- [10. 路线图与待办](#10-路线图与待办)
- [11. 排错手册](#11-排错手册)
- [12. 变更日志](#12-变更日志)

---

## 0. 项目背景与名词表

### 目标

把 **XLeRobot**（双 SO-101 机械臂 + 头部 + 三轮全向底盘）的手柄/键盘遥操数据，
做成 **π0.5（VLA 模型）能直接训练**的 LeRobot v3.0 数据集，并在真机上跑起来。

### 当前进度

| 阶段 | 状态 |
|---|---|
| 数据采集链路（遥操 → 发布器 → 录制器） | ✅ 完成 |
| 数据格式（LeRobot v3.0，30 Hz，12 维） | ✅ 完成 |
| 采集工具链（录制/转换/剪枝/自检/诊断） | ✅ 完成 |
| 夹爪控制（模拟量） | ✅ 完成（2026-10-30） |
| 键盘遥操 + 中止复位 | ✅ 完成 |
| π0.5 训练 | ⬜ 未开始（环境未装） |
| 真机推理 / RTC | ⬜ 未开始 |

**当前瓶颈不是代码，是数据量**：只有 3 集 / 618 帧 / 20.6 秒，而 π0.5 微调通常要 50+ 条同任务演示。

### 名词表

| 名词 | 含义 |
|---|---|
| **VLA** | Vision-Language-Action 模型：输入图像+语言，输出动作 |
| **π0.5** | Physical Intelligence 的 VLA 基座模型（2B VLM + 300M 动作专家） |
| **chunk** | 一次推理输出的连续动作段（本项目 `chunk_size=50`） |
| **RTC** | Real-Time Chunking：在推理延迟下平滑衔接两个 chunk 的算法 |
| **IK** | 逆运动学：从"手要到哪"反推"关节转多少" |
| **遥操** | 人用遥控器/键盘实时操纵机器人 |
| **raw 层** | 原始数据层：npz + JPEG，17 维全存，可反复转换 |
| **v3.0** | LeRobot 数据集格式版本，可直接被 `lerobot-train` 读取 |
| **发布器** | 把机器人实时状态通过 HTTP 广播的只读服务（`:8765`） |

---

## 1. 五分钟上手

```bash
cd ~/xhz/XLeRobot_wmf/LeRobot

# 每个新终端都要先定义这个（不继承）
P=~/miniconda3/envs/lerobot/bin/python
```

**① 启动遥操**

```bash
PYTHONPATH=src $P examples/xlerobot/5_xlerobot_teleop_xbox.py --publish-state
```

看到 `[MAIN] Using controller: ...` 就说明手柄通了。

**② 另开终端录制**

```bash
cd ~/xhz/XLeRobot_wmf/LeRobot
$P dsh/raw_recorder.py --format v30 \
  --v30-out $PWD/data/serve/local/pick_cube \
  --repo-id local/pick_cube \
  --task "pick up the cube"
```

按键：`o` 开始/结束一段，`d` 丢弃，`p` 状态，`q` 退出。

**③ 检查数据**

```bash
HF_HOME=$PWD/dsh/out/.hf PYTHONPATH=src $P -c "
from lerobot.datasets.lerobot_dataset import LeRobotDataset
ds = LeRobotDataset('local/pick_cube', root='data/serve/local/pick_cube', video_backend='pyav')
print(ds.num_episodes, '集', ds.num_frames, '帧', ds.fps, 'fps')"
```

---

## 2. 系统架构

```
┌───────────────────────────── 终端 1 ─────────────────────────────┐
│ 5_xlerobot_teleop_xbox.py --publish-state                        │
│   ├─ 串口读写（2 条总线，16 个舵机）                              │
│   ├─ 3 路相机（30 Hz 读取线程，带掉线自愈）                       │
│   └─ HTTP :8765  /schema  /snapshot(npz)                         │
└──────────────────────────────┬───────────────────────────────────┘
                               │ 只读拉取
┌──────────────────────────────▼──────── 终端 2 ───────────────────┐
│ dsh/raw_recorder.py                                              │
│   ├─ --format raw  → npz + JPEG（17 维，母版）                    │
│   └─ --format v30  → LeRobot v3.0（12 维，可直接训练）            │
└──────────────────────────────┬───────────────────────────────────┘
                               │
        ┌──────────────────────┴──────────────────────┐
        ▼                                             ▼
 convert_to_v21.py → v2.1 → openpi             lerobot-train → π0.5
 （官方转换器可升到 v3.0）
```

### 为什么是"发布器 + 录制器"两个进程

| 原因 | 说明 |
|---|---|
| **真正的动作值只有遥操进程知道** | 发布器捕获 `send_action()` 的返回值（限幅后），不是从状态推断 |
| 采集不占手柄循环 | 录制器只是个 HTTP 客户端，不会拖慢 30 Hz 控制 |
| 可以随时起停 | 遥操开着，录制器随时开关 |

---

## 3. 代码地图

### 核心模块（`src/`）

| 文件 | 作用 | 关键点 |
|---|---|---|
| `robots/xlerobot/state_publisher.py` | 只读状态发布器 | 相机隔离、掉线自愈、串口锁 |
| `robots/xlerobot/xlerobot.py` | 机器人本体 | `motors_connected` 与相机解耦 |
| `robots/xlerobot/config_xlerobot.py` | 配置 | 起始姿态、相机、发布器参数 |
| `utils/visualization_utils.py` | Rerun 可视化 | 查看器缺失时自动降级 |

### 工具（`dsh/`）

| 文件 | 作用 |
|---|---|
| `raw_recorder.py` | 录制器：`--format raw` / `--format v30`，含 `DirectSource`（同进程复用） |
| `convert_to_v21.py` | raw → LeRobot v2.1（给 openpi 用） |
| `prune_v30.py` | 从 v3.0 数据集删除指定 episode（带备份） |
| `probe_gripper.py` | 测夹爪在 0~100 上的真实行程 |
| `florence_boxes.py` | 用 Florence-2 零样本出 2D 框（替代人工标注） |
| `diag_servos.py` | 只读扫描舵机（电压/温度/位置/力矩） |
| `selftest_publisher.py` | 无硬件自检发布器（5 个场景） |
| `state_publisher.py` | 28 行 shim，方便 `dsh/` 内部 import |

### 文档（`dsh/*.md` + 根目录）

| 文件 | 内容 |
|---|---|
| `RUNBOOK.md` | **操作手册**：键位、命令、已知问题、FAQ |
| `dsh/raw_format.md` | raw 层的字段与目录结构 |
| `dsh/model_contract.md` | π0.5 的输入输出契约 |
| `dsh/pipeline.md` | 数据流设计说明 |
| `dsh/README.md` | 工具索引 |

---

## 4. 数据集说明

### 当前数据

```
data/serve/local/your_task/          v3.0 · 3 集 · 618 帧 · 30 fps · 20.6 秒
  file-000.parquet  231 帧
  file-001.parquet  128 帧
  file-002.parquet  259 帧
  task = "你的任务描述"              ← ⚠️ 仍是占位符

data/serve/local/your_task.bak-20261026-183911/  剪枝前备份（3 集 532 帧）
```

> **注意**：这三集的夹爪维度是常量（2.77），来自夹爪映射修复之前。
> **抓取类任务不能用这批数据**，需要重录。

### 12 维是什么

```
0-5   left_arm:  shoulder_pan, shoulder_lift, elbow_flex, wrist_flex, wrist_roll, gripper
6-11  right_arm: 同上
```

**单位**：度。手臂关节归一化到 −100~100，夹爪 0~100。

**为什么不含头部和底盘**：π0.5 的动作维度要与训练数据一致；头部和底盘在任务中不动，剔除可以降低学习难度。需要时可用 raw 层（17 维）重新生成。

### v3.0 目录结构

```
<dataset>/
├── data/chunk-000/file-000.parquet      # 状态 + 动作 + 时间戳
├── videos/<camera>/chunk-000/file-000.mp4
├── meta/info.json                       # 版本、维度、帧数
├── meta/episodes/                       # 每集元数据（含视频时间区间）
├── meta/tasks.parquet                   # 任务文本
└── meta/stats.json                      # 归一化统计量（训练时自动读取）
```

**一集一个文件**：录制器每保存一集就 finalize 并重新打开数据集，所以崩溃最多损失当前段。

---

## 5. 操作手册

### 5.1 手柄遥操

```bash
PYTHONPATH=src $P examples/xlerobot/5_xlerobot_teleop_xbox.py --publish-state
```

| 操作 | 动作 |
|---|---|
| 左摇杆 | 左臂 XY 平面移动 |
| 按住左摇杆 + 左右 | 左臂 `shoulder_pan` |
| 按住 LB + 左摇杆 | 俯仰 / `wrist_roll` |
| **LT** | 左夹爪（松开=夹紧，按到底=张开） |
| 右摇杆 / RS / RB / RT | 右臂，对称 |
| 十字键 | 底盘前后左右 / 转向 |
| 单按 LB / RB | 底盘速度档 −1 / +1 |
| Back | 回起始姿态（继续遥操） |
| **Start** | **中止：复位后退出** |
| Ctrl-C | 同上 |

### 5.2 键盘遥操（没手柄时）

```bash
PYTHONPATH=src $P examples/xlerobot/4_xlerobot_teleop_keyboard.py --publish-state
```

| 键 | 动作 |
|---|---|
| `w/s/a/d` | 左臂 XY |
| `q/e` | 左 `shoulder_pan` |
| `r/f` | 左 `wrist_roll` |
| `z/x` | 左俯仰 |
| `t/g` | 左夹爪 开/合 |
| `c` | 左臂回起始位 |
| 小键盘 `8/2/4/6` 等 | 右臂，对称 |
| `+/-` | 右夹爪 |
| `i/k/j/l` | 底盘前后左右 |
| `u/o` | 转向 |
| `n/m` | 速度档 |
| **`b`** | **中止（复位后退出）** |

### 5.3 录制

**方式 A：独立录制器（推荐）**

```bash
# v3.0（直接可训练）
$P dsh/raw_recorder.py --format v30 \
  --v30-out $PWD/data/serve/local/<任务名> \
  --repo-id local/<任务名> \
  --task "<任务描述>"
```

```bash
# raw（母版，可后处理）
$P dsh/raw_recorder.py --root $PWD/dsh/out/raw --task "<任务描述>"
```

**方式 B：键盘脚本内嵌录制**

```bash
PYTHONPATH=src $P examples/xlerobot/4_xlerobot_teleop_keyboard.py \
  --record --record-format v30 \
  --record-out $PWD/data/serve/local/pick_cube \
  --record-repo-id local/pick_cube \
  --task-description "pick up the cube"
```

按键：`空格` 开关一段，`Backspace` 丢弃当前段。

> ⚠️ `--task` / `--task-description` 是**训练 prompt**，必须按实际任务改写。
> 规则：描述任务目标+对象、10 词以内、同任务用完全相同的字符串。

### 5.4 数据转换

```bash
# raw → v2.1（给 openpi）
$P dsh/convert_to_v21.py --raw dsh/out/raw --out data/serve/local/xxx_v21 \
  --task "<同录制时>" --overwrite

# v2.1 → v3.0（官方转换器）
PYTHONPATH=src $P src/lerobot/datasets/v30/convert_dataset_v21_to_v30.py \
  --root data/serve/local/xxx_v21
```

### 5.5 删除某一集

```bash
$P dsh/prune_v30.py --root data/serve/local/your_task --drop 2 --dry-run   # 先看
$P dsh/prune_v30.py --root data/serve/local/your_task --drop 2             # 执行（自动备份）
```

### 5.6 自检与诊断

```bash
# 无硬件跑通发布器（5 个场景）
$P dsh/selftest_publisher.py
$P dsh/selftest_publisher.py --camera-fail right_wrist

# 只读扫描舵机
$P dsh/diag_servos.py

# 测夹爪行程
PYTHONPATH=src $P dsh/probe_gripper.py --motor left_arm_gripper --values 0,50,100 --hold 2
```

### 5.7 灵敏度调参

```bash
--degree-step 2.0      # 每帧关节增量（度），越大越快
--xy-step 0.005        # 每帧平面增量（米）
--kp 1.0               # 跟随增益，越小越柔
--stick-deadzone 0.1   # 摇杆死区
--stick-expo 0.5       # 摇杆曲线，中间更细腻
```

预设：

```bash
# 精细操作
--degree-step 1.0 --xy-step 0.002 --stick-expo 1.0
# 快速搬运
--degree-step 4.0 --xy-step 0.010 --stick-expo 0
```

---

## 6. 硬件与已知问题

### 硬件配置

| 项 | 值 |
|---|---|
| 机械臂 | 2 × SO-101（各 6 个舵机：5 关节 + 1 夹爪） |
| 头部 | 2 个舵机 |
| 底盘 | 3 个全向轮（STS3215） |
| 串口 | `ttyACM0`（左臂 + 头）、`ttyACM1`（右臂 + 底盘） |
| 相机 | 3 × USB `05a3:9230`，640×480 MJPG 30 fps |
| 计算 | RTX 4090 24 GB / Ultra 9 285K / 125 GB RAM |

### 已知问题

**① USB 相机间歇性掉线**

三路相机轮流从总线上消失（`errno=19 No such device`），指向供电/带宽而非单根线。

当前容错：
- 遥操**不会崩**（发指令只看电机总线）
- raw 模式继续录，标 `degraded`
- v3.0 模式**跳过缺帧**并告警
- 转换器自动跳过残缺段

排查：`sudo dmesg -T | tail -80 | grep -iE "over-current|reset|disconnect"`

**② 夹爪是单动颚**

每组夹爪只有一根手指由舵机驱动，另一根固定。**"按扳机只有一根手指动"是正常的。**

方向：`0 = 夹紧`，`100 = 张开`。参数 `--left-/--right-gripper-open|closed` 可对调。

**③ 头部相机随 head 关节转动**

做 3D 定位时必须锁死头部，或把 head 关节角纳入变换链。

**④ 十字键上下反向**（↑ = 后退）

**⑤ 标定文件在另一份 checkout**

`/home/user/xhz/XLeRobot/LeRobot/data/calibration/my_xlerobot.json`
**不要删除**，也不要修改 `/home/user/xhz/XLeRobot` 目录。

---

## 7. 关键设计决策

| 决策 | 原因 |
|---|---|
| **只读发布器，不直接读串口** | 串口只能被一个进程打开；发布器捕获真实下发的 action |
| **12 维（去头/底盘）** | 降低动作维度，头部/底盘在任务中不动 |
| **30 Hz** | π0 系列的标准控制频率 |
| **raw + v3.0 双路径** | raw 是母版（可回溯），v3.0 直接可训 |
| **v3.0 每集 finalize** | 崩溃最多损失当前段，而不是整个文件 |
| **夹爪 0=紧 / 100=开** | 实测结果；`start_pose` 的 2.77 实际是"接近闭合" |
| **相机掉线不阻断 `send_action`** | `motors_connected` 与 `is_connected` 解耦 |
| **Ctrl-C 只置标志不抛异常** | 抛异常会打断串口事务，导致 SDK 的 `is_using` 卡死 |
| **录制器默认静默** | 操作员只需要看关键事件，`--verbose` 查看细节 |

---

## 8. 训练 π0.5

### 8.1 环境准备（**尚未执行**）

```bash
# 1) 取 transformers 的 fork（GitHub HTTPS 时通时不通，SSH 稳定）
git clone --depth 1 -b fix/lerobot_openpi \
  git@github.com:huggingface/transformers.git ~/src/transformers-openpi

# 2) 独立环境（不要装在采集环境里，会降级 scipy）
conda create -y -n lerobot-train python=3.10
conda activate lerobot-train
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126
cd ~/xhz/XLeRobot_wmf/LeRobot && pip install -e .
pip install ~/src/transformers-openpi "scipy>=1.10.1,<1.15"

# 3) 验证（必须返回 True）
python -c "from lerobot.policies.pi05 import modeling_pi05 as m; print(m.GemmaForCausalLM is not None)"
```

> ⚠️ 不建议用 `pip install -e ".[pi]"`——它的 `git+https` 地址在本机实测会间歇超时，
> 卡住时很难判断是网络还是构建问题。先 clone 再装更可控。

### 8.2 训练命令

```bash
export HF_ENDPOINT=https://hf-mirror.com     # huggingface.co 本体不通（实测超时），走镜像

PYTHONPATH=src python -m lerobot.scripts.lerobot_train \
  --policy.type=pi05 \
  --policy.path=lerobot/pi05_base \
  --policy.device=cuda \
  --policy.dtype=bfloat16 \
  --policy.gradient_checkpointing=true \
  --dataset.repo_id=local/pick_cube \
  --dataset.root=$PWD/data/serve/local/pick_cube \
  --output_dir=outputs/pi05_pick_cube \
  --batch_size=8 --steps=20000 --save_freq=2000 \
  --wandb.enable=false
```

### 8.3 关键配置项

| 参数 | 默认 | 说明 |
|---|---|---|
| `dtype` | `float32` | **必须改 bfloat16**，否则 24 GB 卡爆显存 |
| `gradient_checkpointing` | `False` | 开启省显存 |
| `chunk_size` | 50 | 一次输出 50 步（1.67 s @30 Hz） |
| `n_action_steps` | 50 | 执行多少步后重新推理 |
| `train_expert_only` | `False` | **数据少时设为 True**：冻结 VLM，只训动作专家 |
| `freeze_vision_encoder` | `False` | 只冻视觉塔 |
| `rtc_config` | `None` | 启用 RTC 平滑衔接 |

### 8.4 数据要求（论文依据见第 9 节）

| 要求 | 现状 |
|---|---|
| 至少 50 条同任务演示 | ❌ 3 条 |
| 覆盖多个物体 / 位置 / 背景 | ❌ 单工位 |
| prompt 明确且一致 | ❌ 占位符 |
| 夹爪真实动作 | ❌ 旧数据是常量 |

---

## 9. 研究结论摘要

### 核心论文

| 论文 | 关键结论 | 对我们的要求 |
|---|---|---|
| [π0.5](https://arxiv.org/abs/2504.16054) | 异构共训练 + 混合多模态目标；可泛化到**新家**做长时序任务 | 我们做的是 specialist 微调，路线正确 |
| [π0](https://arxiv.org/abs/2410.24164) | VLM + flow-matching 动作专家；预训练后微调学新技能 | 12 维关节空间可直接学 |
| [KI](https://proceedings.neurips.cc//paper_files/paper/2025/hash/94e936034d12bcd04834ec2773f02aff-Abstract-Conference.html) | 直接加动作专家会**拖慢训练、破坏语义知识** | 数据少时用 `train_expert_only` |
| [RTC](https://arxiv.org/abs/2506.07339) | 推理延迟导致 chunk 边界停顿/跳变；inference-time 算法，无需重训 | 实机部署建议开启 |
| [SmolVLA](https://arxiv.org/abs/2506.01844) | 小模型单卡可训，面向社区数据 | 可作为同数据对照基线 |
| [Embodiment Gap](https://arxiv.org/abs/2608.18433) | 模型泛化 ≠ 能在你的机器人上跑；每台机器要做适配清单 | 我们的 12 维/30Hz/相机布置就是清单项 |
| [Data Scaling Laws](https://proceedings.iclr.cc/paper_files/paper/2025/hash/88b7b2c896506daabc8d3fd587055167-Abstract-Conference.html) | 泛化随**环境数**和**物体数**呈幂律；多样性 >> 演示数量 | **换位置/换物体比多录同一物体更有用** |
| [LIBERO-PRO](https://arxiv.org/abs/2510.03827) | 标准基准 90%+ → 扰动下 0.0%；模型在背动作序列 | 评测必须做扰动测试 |
| [ObjectVLA](https://arxiv.org/abs/2502.19250) | 100 个新物体选择成功率 64%（专门设计的方法） | 物体泛化本身就很难 |
| [RoboFollow](https://arxiv.org/abs/2609.25636) | 低场景熵下语言是冗余的，模型可以高分却不听指令 | **采集时场景里要多放物体** |

### 三条最重要的实践结论

**① 多样性 > 数量**

同样的 50 条，铺在 8 个环境里远好于全挤在一个工位。

**② 场景熵要够高**

桌上放 3 个物体，用不同指令抓不同目标——否则模型学不到"用语言"。

**③ 数据少时先隔离训练**

`train_expert_only=true` 保住 VLM 的语义知识，避免过拟合到单一工位。

---

## 10. 路线图与待办

### 已完成

- [x] 状态发布器（HTTP，相机容错）
- [x] 录制器（raw / v3.0，续写，崩溃安全）
- [x] 转换器（v2.1）、剪枝工具、诊断工具
- [x] 夹爪模拟量控制（扳机 0~1 → 0~100）
- [x] 键盘遥操（发布状态 + 中止复位 + 内嵌录制）
- [x] 中止流程（Start/Ctrl-C → 复位 → 退出），含串口忙标志修复
- [x] 灵敏度参数化 + 摇杆比例控制
- [x] 交接文档 + RUNBOOK

### 下一步（按优先级）

| # | 事项 | 阻塞 | 预计 |
|---|---|---|---|
| 1 | **重录 40~50 条带夹爪动作的数据** | 无 | 1~2 天 |
| 2 | 定真实 prompt（替换占位符） | 无 | 10 分钟 |
| 3 | 装训练环境（`.[pi]`） | GitHub SSH 可用 | 1 小时 |
| 4 | 冒烟训练（`--steps 100`） | 依赖 3 | 30 分钟 |
| 5 | 正式训练 + 真机评测 | 依赖 1~4 | 数天 |
| 6 | RTC 部署 | 依赖 5 | 1 天 |

### 中期方向（可选）

| 方向 | 说明 |
|---|---|
| 检测辅助 | Florence-2 出 2D 框，画进图像或写进 state |
| 3D 定位 | 标定相机 → 平面假设 → 目标 XYZ 进 state |
| 底盘小范围移动 | 视觉伺服（脚本控制）或 15 维动作（模型控制） |
| 多物体采集 | 提高场景熵，教模型"听指令选目标" |

---

## 11. 排错手册

| 报错 | 原因 | 解决 |
|---|---|---|
| `-c：未找到命令` | `$P` 未定义（新终端） | 先 `P=~/miniconda3/envs/lerobot/bin/python` |
| `--overwrite：未找到命令` | `\` 续行中间写了 `#` 注释 | 注释写在命令前一行 |
| `ModuleNotFoundError: numpy` | 用了 base 环境 | 用 `$P` |
| `Port is in use!` | Ctrl-C 打断了串口事务 | 已修复：Ctrl-C 只置标志 + 自动清 `is_using` |
| `Camera(s) failing` | USB 相机掉线 | 检查供电/带宽；代码已容错 |
| `Failed to find Rerun Viewer` | 查看器不在 PATH | 已 fail-soft；或 `export PATH=$HOME/miniconda3/envs/lerobot/bin:$PATH` |
| `pkg_resources is deprecated` | pygame 警告 | 忽略 |
| `Parquet magic bytes not found` | 进程被强杀，文件未收尾 | 数据不可恢复；已改为每集 finalize |
| 录制器拒绝开录 | 三路相机不全 | 检查 USB |
| `transformers` 导入成功但建模失败 | `modeling_pi05` 里 try/except 兜底成 None | 装 fork 后验证 `GemmaForCausalLM is not None` |
| HF 下载卡住 | huggingface.co 本体不通（实测超时） | `export HF_ENDPOINT=https://hf-mirror.com` |
| `git ls-remote` / `pip` 卡住 | GitHub HTTPS 间歇性超时 | 改用 SSH（`git@github.com:...`） |

---

## 12. 变更日志

| 日期 | 变化 |
|---|---|
| 2026-10-26 | 发布器 + 录制器 + 转换器；首次真实录制；修相机掉线导致遥操崩溃 |
| 2026-10-27 | Rerun fail-soft；整理 4 个提交；确认 GitHub 推送权限问题 |
| 2026-10-30 | 夹爪模拟量控制（0=紧/100=开）；键盘遥操对齐手柄；中止复位；灵敏度参数化；检测/3D/伺服方案调研；本文件重写为团队文档 |

---

## 附：相关链接

- 仓库：`git@github.com:fduTristin/LeRobot.git`（分支 `feat/data_recording`）
- LeRobot 文档：https://huggingface.co/docs/lerobot
- π0.5 开源实现：https://github.com/Physical-Intelligence/openpi
- Florence-2 文档：https://huggingface.co/docs/transformers/model_doc/florence2
