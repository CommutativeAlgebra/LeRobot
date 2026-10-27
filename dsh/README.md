# dsh — 工作时间文件

本目录存放分析/改造过程中的**中间文件**（调研笔记、转换脚本、临时产物），不属于 LeRobot 上游代码。

## 约定

- 代码产物（`*.py`）与文档（`*.md`）默认放这里。
- **例外**：状态发布器已提升为正式模块 —— `src/lerobot/robots/xlerobot/state_publisher.py`，
  并在 `xlerobot.py` / `config_xlerobot.py` / `7_xlerobot_teleop_joycon.py` 中接线。
- 生成物（数据集、npz、图）放 `dsh/out/`，可随时删除重建。
- 本目录**未加入 `.gitignore`**；如需跟随仓库提交请照常 `git add`，不想要就自行忽略。

## 索引

| 文件 | 内容 |
|---|---|
| [`raw_format.md`](raw_format.md) | 机器人侧两种数据通道的原始格式：ZMQ JSON 流、`state_stream` 的 npz 快照；字段/单位/已知缺陷 |
| [`model_contract.md`](model_contract.md) | openpi `Observation` 契约、pi0.5 的 state 离散化、变换链顺序、致命细节 |
| [`pipeline.md`](pipeline.md) | 现状盘点、目标决策、缺口清单与实施顺序 |
| [`raw_recorder.py`](raw_recorder.py) | 原始数据录制器（键盘控制、接入门控、列式缓冲、原子提交） |
| [`selftest_publisher.py`](selftest_publisher.py) | 发布器端到端自检（假机器人，无需硬件） |
| [`state_publisher.py`](state_publisher.py) | 兼容层，指向 `src/lerobot/robots/xlerobot/state_publisher.py` |

## 状态发布器

正式实现位于 [`src/lerobot/robots/xlerobot/state_publisher.py`](../src/lerobot/robots/xlerobot/state_publisher.py)，
由遥操脚本的 `--publish-state` 启用（本仓库改动，`XLeRobot` 未动）：

```bash
P=~/miniconda3/envs/lerobot/bin/python

# 终端 1：遥操 + 状态源
PYTHONPATH=src $P examples/xlerobot/7_xlerobot_teleop_joycon.py --publish-state

# 终端 2：录制
$P dsh/raw_recorder.py --task pick_and_place

# 无硬件自检
$P dsh/selftest_publisher.py            # 包装模式（遥操走这条）
$P dsh/selftest_publisher.py --install  # 实例注入模式
$P dsh/selftest_publisher.py --bus      # 总线读取回退分支
```

> ⚠️ `PYTHONPATH=src` 不能省：conda 环境的 `lerobot` 是 editable 安装，默认指向
> 另一份 checkout。不加会加载到那份代码，表现为"改了没生效"。

## 录制器

```bash
# 依赖 lerobot conda 环境（numpy + opencv）
P=~/miniconda3/envs/lerobot/bin/python

# 1) 只检查接入（不录制）
$P dsh/raw_recorder.py --probe-only

# 2) 正式录制
$P dsh/raw_recorder.py --task pick_and_place

# 3) 无硬件自检
$P dsh/raw_recorder.py --source fake --script "on;+1.0;off;quit"
```

键盘：`o` 开关 / `d` 丢弃 / `p` 状态 / `q` 退出（录制中按 `q` 会先保存）。

**前置条件**：遥操进程必须带 `--publish-state` 在跑（本仓库已支持）。
没有它就没有 `8765` 状态源，录制器会拒绝打开——因为拿不到真实 action。

输出：`dsh/out/raw/<session>/ep_XXXXXX/{meta.json, samples.npz, imgs/}`。

## 事实基准（核对日期 2026-10-26）

| 项 | 值 | 来源 |
|---|---|---|
| 本工作区 | `~/xhz/XLeRobot_wmf/LeRobot` | — |
| 并行 checkout | `~/xhz/XLeRobot/LeRobot`（数采系统在此，未提交） | — |
| 本仓库数据集格式 | `CODEBASE_VERSION = "v3.0"` | [`src/lerobot/datasets/lerobot_dataset.py:80`](../src/lerobot/datasets/lerobot_dataset.py#L80) |
| openpi 钉的 lerobot | commit `0cf8648` → `CODEBASE_VERSION = "v2.1"` | openpi `pyproject.toml` |
| openpi 数据加载入口 | `import lerobot.common.datasets.lerobot_dataset`（v2.1 布局） | openpi `training/data_loader.py` |
| pi0.5 配置 | `Pi0Config(pi05=True)` → `action_dim=32`, `action_horizon=50`, `max_token_len=200` | openpi `models/pi0_config.py` |

> 结论：**openpi 读不了本仓库产出的 v3.0 数据集**，训练前必须转换。
