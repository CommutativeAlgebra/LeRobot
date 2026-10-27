# 机器人侧原始数据格式

XLeRobot 有两条互不相同的数据通道，采集时必须先确认走的是哪一条。

---

## 通道 A：ZMQ 观测流（host / client 架构）

### 拓扑

| 方向 | socket | 端口 | 说明 |
|---|---|---|---|
| 指令 | `PULL`（host 绑定）→ client `PUSH` | 5555 | 客户端下发 action |
| 观测 | `PUSH`（host 绑定）→ client `PULL` | 5556 | host 上传 observation |

两端均设 `CONFLATE=1`：慢的一方只保留**最新一帧**，旧帧静默丢弃。

来源：[`src/lerobot/robots/xlerobot/xlerobot_host.py`](../src/lerobot/robots/xlerobot/xlerobot_host.py)（绑定与发送）、[`xlerobot_client.py`](../src/lerobot/robots/xlerobot/xlerobot_client.py)（接收与解码）。

### 报文：一条 JSON 字符串 = 一帧

```json
{
  "left_arm_shoulder_pan.pos": 5.4752,
  "left_arm_shoulder_lift.pos": -99.3068,
  "left_arm_elbow_flex.pos": 99.1881,
  "left_arm_wrist_flex.pos": 22.9579,
  "left_arm_wrist_roll.pos": 4.5199,
  "left_arm_gripper.pos": 2.7704,
  "right_arm_shoulder_pan.pos": 1.1944,
  "right_arm_shoulder_lift.pos": -98.5928,
  "right_arm_elbow_flex.pos": 99.4609,
  "right_arm_wrist_flex.pos": 16.0207,
  "right_arm_wrist_roll.pos": 7.3016,
  "right_arm_gripper.pos": 0.5795,
  "head_motor_1.pos": 0.4325,
  "head_motor_2.pos": 41.2684,
  "x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0,
  "left_wrist":  "<base64 JPEG>",
  "right_wrist": "<base64 JPEG>",
  "head":        "<base64 JPEG>"
}
```

### 字段规格

| 分组 | 键数 | 单位 / 值域 | 归一化模式 |
|---|---|---|---|
| 手臂 5 关节 ×2 | 10 | `-100 ~ 100` | `RANGE_M100_100` |
| 夹爪 ×2 | 2 | `0 ~ 100` | `RANGE_0_100` |
| 头部 ×2 | 2 | `-100 ~ 100` | `RANGE_M100_100` |
| 底盘 | 3 | `x,y`: m/s；`theta`: deg/s | 三轮里程计反解 |
| 相机 ×3 | 3 | base64(JPEG q90)，640×480 | `cv2.imencode`，BGR 源 |

合计 **17 维本体感觉 + 3 路图像**。关节值**不是弧度**，是总线归一化后的无量纲值。

### 已知缺陷

1. **无时间戳**：JSON 内没有任何时间字段，只能用收包时刻打点。
2. **不是稳定 30Hz**：host 循环是 `time.sleep(max(1/30 - elapsed, 0))`，是上限不是定时；实际 20~27Hz 且抖动。
3. **静默丢帧**：`CONFLATE` + `NOBLOCK` 循环读到空为止，没有序号，丢了无法察觉。
4. **图像与关节不同步**：相机 `async_read` 超时放宽到 1000ms，慢的那路可能是几十毫秒前的帧。
5. **没有 action**：观测流单向，action 在反向 5555 链路上，只有发送方知道。
6. **体积**：单帧 base64 约 200KB，30Hz ≈ 6MB/s ≈ **21GB/小时**。

来源：[`xlerobot.py`](../src/lerobot/robots/xlerobot/xlerobot.py) 的 `get_observation` / `_wheel_raw_to_body`；相机归一化见 `motors_bus.py` 的 `_normalize` / `_unnormalize`。

---

## 通道 B：`state_stream` 本地快照（当前数采方案）

位于并行 checkout `~/xhz/XLeRobot/LeRobot`，本工作区**没有**这套代码。

### 拓扑

```
遥操进程（独占串口+相机）
  ├─ 主线程：控制循环 → stream.send_action()
  ├─ reader 线程：按 state_fps 读电机（锁外读相机）
  └─ http 线程：127.0.0.1:8765  ├─ GET /schema
                                └─ GET /snapshot
                                      │ HTTP (npz)
数采进程（record_ui.py）───────────────┘
```

### 接口

`/schema`（静态，一次）：

```json
{"stream_id": "<uuid>", "robot_type": "xlerobot",
 "state_names": [...17...], "action_names": [...17...],
 "cameras": {"left_wrist": [480,640,3], "right_wrist": [...], "head": [...]}}
```

`/snapshot`（每帧一个 npz）：

| 键 | 类型 | 含义 |
|---|---|---|
| `metadata` | uint8[] (JSON) | `stream_id` / `sequence` / `state_age_s` / `action_age_s` |
| `state` | float32[17] | 实测关节 + 底盘速度 |
| `action` | float32[17] | **最近一次成功下发的目标（限幅后返回值）** |
| `image_left_wrist` 等 | uint8[H,W,3] | RGB（相机层已 BGR→RGB） |

### 关键性质

- **action 是真值**：`RobotStateStream.send_action` 捕获 `robot.send_action()` 的返回值，即经 `ensure_safe_goal_position` 限幅后真正写入电机的值。**永不从 state 推断**。
- **锁存语义（ZOH）**：`action` 是"最近一次下发"，未收到新指令时重复同一值，符合电机行为。
- **时间戳打在读取开始**：慢相机无法伪装成新鲜帧。
- **`sequence`**：状态源帧号，可识别重复采样。
- 遥操未发过完整指令时 `/snapshot` 返回 **503**，拒绝给出半截 action。
- 只绑 `127.0.0.1`，只读，`np.load(allow_pickle=False)`。

来源：并行 checkout 的 `src/lerobot/robots/xlerobot/state_stream.py`、`recording.py`、`examples/xlerobot/record_ui.py`。

### 落盘结果：LeRobot v3.0

```
recordings/<task>/
├── meta/info.json                  # codebase_version: v3.0
├── meta/tasks.parquet
├── meta/episodes/chunk-000/file-000.parquet
├── data/chunk-000/file-000.parquet
└── videos/observation.images.*/chunk-000/file-000.mp4   # 每路相机一个文件，含所有 episode
```

列：`observation.state[17]`、`action[17]`、三路 `observation.images.*`、`task`，外加四个扩展字段
`observation.sample_time_s` / `state_age_s` / `action_age_s` / `source_sequence`（均 float64）。

> 注意：v3.0 的视频是**每 chunk 一个 mp4**（多 episode 拼接），转 v2.1 时需要按 episode 切段。

---

## 选型对照

| | 通道 A（ZMQ） | 通道 B（state_stream） |
|---|---|---|
| action 可得性 | 仅客户端"意图"，无限幅 | ✅ 真实下发值 |
| 时间戳 | 无 | ✅ 读开始打点 |
| 丢帧检测 | ❌ | ✅ sequence |
| 适用 | 机器人与训练机分离 | 同一台机器 |

本机机器人直连同一台电脑，**用通道 B**。
