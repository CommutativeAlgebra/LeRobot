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
| 目标框链路（检测 → 选择 → 画框） | ✅ 完成（2026-11-02） |
| Florence-2-large 权重 | ✅ 本地 `models/`（已 gitignore，1.5 GB） |
| π0.5 基座权重（`lerobot/pi05_base`，14.5 GB） | ⬜ 未下载 |
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
| `datasets/target_overlay.py` | **读 v3.0 时把目标框画到画面上** | `TargetBoxOverlay`；只改内存像素，不动 mp4 |

### 工具（`dsh/`）

| 文件 | 作用 |
|---|---|
| `raw_recorder.py` | 录制器：`--format raw` / `--format v30`，含 `DirectSource`（同进程复用） |
| `convert_to_v21.py` | raw → LeRobot v2.1（给 openpi 用） |
| `prune_v30.py` | 从 v3.0 数据集删除指定 episode（带备份） |
| `probe_gripper.py` | 测夹爪在 0~100 上的真实行程 |
| `florence_boxes.py` | 用 Florence-2 零样本出 2D 框（替代人工标注） |
| `select_target.py` | 多个框里按中心 x 排序选指定那个，数量不足时夹取并播报 |
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

### 5.8 提交与推送（Git）

**我们有两个远端**

| 远端 | 地址 | 用途 |
|---|---|---|
| **`mine`** | `git@github.com:CommutativeAlgebra/LeRobot.git` | ✅ **我们的仓库**，日常推送 |
| `origin` | `git@github.com:fduTristin/LeRobot.git` | 上游参考，**不要往这里推** |

默认分支是 `main`，功能分支是 `feat/data_recording`。

**认证：本机有两把 SSH key**

| key | 对应账号 |
|---|---|
| `~/.ssh/id_rsa` | `fduTristin` |
| `~/.ssh/id_ed25519_commutative` | **`CommutativeAlgebra`**（推 mine 时用这把） |

验证当前身份：

```bash
ssh -T git@github.com                                            # Hi fduTristin!
ssh -i ~/.ssh/id_ed25519_commutative -o IdentitiesOnly=yes -T git@github.com   # Hi CommutativeAlgebra!
```

---

**方式 A：每次指定 key（立刻可用，无需改配置）**

```bash
cd ~/xhz/XLeRobot_wmf/LeRobot

export GIT_SSH_COMMAND='ssh -i ~/.ssh/id_ed25519_commutative -o IdentitiesOnly=yes'

git add -A
git commit -m "feat(xlerobot): 说明这次改了什么"
git push mine main
```

**方式 B：配置 SSH 别名（一次性，之后不用带环境变量）**

```bash
cat >> ~/.ssh/config <<'EOF'

Host github-commutative
  HostName github.com
  User git
  IdentityFile ~/.ssh/id_ed25519_commutative
  IdentitiesOnly yes
EOF
chmod 600 ~/.ssh/config

# 验证
ssh -T github-commutative        # 期望：Hi CommutativeAlgebra!

# 把 mine 改成别名地址
cd ~/xhz/XLeRobot_wmf/LeRobot
git remote set-url mine git@github-commutative:CommutativeAlgebra/LeRobot.git
```

之后就是普通流程：

```bash
git add -A && git commit -m "..." && git push mine main
```

---

**推送前的自检（重要）**

```bash
# 1. 看改了什么
git status --short
git diff --stat

# 2. 确认没有数据文件被跟踪（应该输出为空）
git ls-files data/ | head
git ls-files dsh/out/ | head

# 3. 有没有把真实配置文件加进来（应该只有 .example）
git ls-files | grep xlerobot_config
```

**这些已经被 `.gitignore` 挡住，但改了 `.gitignore` 之后要复查**：

| 路径 | 为什么不能提交 |
|---|---|
| `data/` | 录制数据集（几十 MB 起） |
| `dsh/out/` | 录制器临时输出 |
| `outputs/` | 训练产物（checkpoint 很大） |
| `src/lerobot/robots/xlerobot/xlerobot_config.json` | **真实配置**：标定路径、相机序列号、本机端口 |
| `__pycache__/` | 编译缓存 |

---

**推送后核对**

```bash
git log --oneline -3 --decorate
git ls-remote --heads mine
```

或直接在网页上看：https://github.com/CommutativeAlgebra/LeRobot

---

### 5.9 目标框：检测 → 选择 → 画框（2026-11-02 新增）

三段链路，可以只跑其中一段：

```bash
# ① 出框（零样本，不需要标注数据）
$P dsh/florence_boxes.py --images a.jpg \
    --task "<OPEN_VOCABULARY_DETECTION>" --phrase "apple" --out boxes.json

# ② 从多个框里选一个（按中心 x 从左到右）
$P dsh/select_target.py --in boxes.json --which middle --noun apple

# ③ 训练时把框画进 v3.0 数据（读的时候画，不写回视频）
lerobot-train ... --dataset.target_overlay.enable=true
```

**选择器 `--which`**

| 写法 | 含义 |
|---|---|
| `middle` / `center` | 中间那个（要求奇数个且 ≥3；偶数个取 `n//2`） |
| `left` / `right` | 最左 / 最右 |
| `2nd` / `third` | 从 1 数的序数 |
| `0 1 2` / `-1 -2` | 0 起下标 / 从右数 |

数量不足时**默认夹取 + 播报**，不报错：

| 输入 | 检测到 | 播报 | 实际抓 |
|---|---|---|---|
| `5th apple` | 3 个 | `only 3 apples` | 最后一个 |
| `middle apple` | 1 个 | `only one apple` | 它本身 |
| `middle apple` | 2 个 | `only 2 apples` | 右边那个 |

`--on-mismatch error` 可切回"数量不符直接退出 2"，用于离线质检。

**画框模式 `--annotate-for`**

| 用途 | 线宽 | 填充 |
|---|---|---|
| 给人看（`human`） | 0.4% 短边 → 640×480 上 2 px | 无 |
| **给 π0.5 看（`model`）** | **0.8% 短边 → 4 px** | **α=0.20 亮绿** |

细线缩到 224 会被插值抹掉，必须靠填充；颜色用亮绿，别用红色（画在红物体上等于没画）。

**v3.0 标注列**

| 列 | 类型 | 说明 |
|---|---|---|
| `annotation.target_bbox` | float32 (4,) | 归一化 `[0,1]` 的 x1,y1,x2,y2 |
| `annotation.target_valid` | int64 (1,) | 0 = 该帧无可用框（不画） |

`--dataset.target_overlay.camera / fill_alpha / line_ratio / drop_prob` 可调；`drop_prob` 是训练时随机丢框的比例（建议 0.1~0.2），让模型在框丢失时也能干活。

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

### 视觉提示与目标选择（2026-11-02 补充）

| 论文 | 关键结论 | 对我们的用法 |
|---|---|---|
| [VP-VLA](https://arxiv.org/abs/2603.22003) | 把目标框/crosshair **渲染进 RGB**，避免"坐标转文本"的模态错配 | 画框优于 Box token，已按此实现 |
| [DeicticVLA](https://arxiv.org/abs/2608.28108) | 同类物体歧义必须用**指向性掩码**；纯语言在未见序数/空间表述下骤降（12.5% vs 62.5%）；框线宽取**图像尺寸 1%** | 支撑"框"路线；线宽 0.8%~1% |
| [Clutter-Robust VLA](https://arxiv.org/abs/2512.22519) | 目标不存在时会**过度抓取**、被杂物干扰 | `target_valid=0` 样本必须存在 |
| [Conditional Grounding](https://arxiv.org/abs/2609.05376) | 同外观干扰物会让策略失效；所需视觉目标随阶段变化 | 每个 chunk 重新检测一次框 |

四条落地结论：

1. **框进图像 > 框进文本**（模态一致性），文本坐标需要额外学跨模态映射
2. **训练分两阶段**：先纯语言再联合，否则学不会用框，或把语言能力练没
3. **遮挡判断用"底边中点 + 面积比"**即可，不需要 SAM 2（SAM 2 只在要像素掩码时值）
4. **底盘移动时不要重新执行"中间"选择**，只做身份关联（IoU/光流/卡尔曼），否则目标会中途切换

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
- [x] 目标框链路：`florence_boxes.py` → `select_target.py` → `TargetBoxOverlay`（2026-11-02）

### 下一步（按优先级）

| # | 事项 | 阻塞 | 预计 |
|---|---|---|---|
| 1 | **重录 40~50 条带夹爪动作的数据** | 无 | 1~2 天 |
| 2 | 定真实 prompt（替换占位符） | 无 | 10 分钟 |
| 3 | 装训练环境（`.[pi]`） | GitHub SSH 可用 | 1 小时 |
| 4 | 冒烟训练（`--steps 100`） | 依赖 3 | 30 分钟 |
| 5 | 正式训练 + 真机评测 | 依赖 1~4 | 数天 |
| 6 | RTC 部署 | 依赖 5 | 1 天 |
| 7 | 写 `dsh/annotate_targets.py`（批量 Florence-2 + 时序跟踪，回填两列） | 依赖 1 | 半天 |
| 8 | 下载 `lerobot/pi05_base`（14.5 GB，走 hf-mirror） | 网络 | 40 分钟 |
| 9 | 底盘接近脚本（两跳 + 尺度伺服 + 四条安全闸门） | 无 | 1 天 |

### 中期方向（可选）

| 方向 | 说明 |
|---|---|
| 检测辅助 | ✅ 已完成：Florence-2 出框 → 选择 → 读 v3.0 时画进图像 |
| 3D 定位 | 标定相机 → 平面假设 → 目标 XYZ 进 state |
| 底盘小范围移动 | 🟡 方案已定：两跳接近 + 尺度伺服；脚本待写（见第 9 节） |
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
| 2026-11-02 | `select_target.py`（多框选择 + 夹取播报）；`TargetBoxOverlay`（v3.0 读时画框 + CLI 开关）；`.gitignore` 加 `models/`；本地拉取 Florence-2-large；补视觉提示调研（VP-VLA / DeicticVLA） |
| 2026-10-30 | 夹爪模拟量控制（0=紧/100=开）；键盘遥操对齐手柄；中止复位；灵敏度参数化；检测/3D/伺服方案调研；本文件重写为团队文档 |

---

## 附：相关链接

- **我们的仓库**：https://github.com/CommutativeAlgebra/LeRobot （`main` / `feat/data_recording`）
- 上游参考：`git@github.com:fduTristin/LeRobot.git`
- LeRobot 文档：https://huggingface.co/docs/lerobot
- π0.5 开源实现：https://github.com/Physical-Intelligence/openpi
- Florence-2 文档：https://huggingface.co/docs/transformers/model_doc/florence2
- Florence-2 权重：https://huggingface.co/microsoft/Florence-2-large （国内用 https://hf-mirror.com）
- π0.5 基座权重：https://huggingface.co/lerobot/pi05_base （14.5 GB，F32）
- 视觉提示：[VP-VLA](https://arxiv.org/abs/2603.22003) · [DeicticVLA](https://arxiv.org/abs/2608.28108)
