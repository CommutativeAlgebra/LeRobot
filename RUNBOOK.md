# XLeRobot 操作手册

一条链：**遥控 → 录制（raw / v3.0）→ 转 v2.1**。

| 角色 | 机器 | 地址 |
|---|---|---|
| 机器人 | Ubuntu 桌面机 `teai-g1` | `user@10.162.152.173` |
| 操作台 | Windows | 本机 |

---

## 0. 准备（一次性）

### 0.1 克隆仓库

```bash
git clone https://github.com/CommutativeAlgebra/LeRobot.git
cd LeRobot
```

### 0.2 Ubuntu 环境（机器人端 / Linux 操作台）

```bash
conda create -y -n lerobot python=3.10
conda activate lerobot
conda install ffmpeg=7.1.1 -c conda-forge -y
pip install -e ".[feetech]"
pip install pygame            # Xbox 遥操需要
```

**每个新终端**先做这两行（下面用 `$P` 指代解释器）：

```bash
cd ~/xhz/XLeRobot_wmf/LeRobot
P=~/miniconda3/envs/lerobot/bin/python
```

> 必须用 `$P`：直接敲 `python` 会落到 `(base)`，报 `No module named 'numpy'`。

### 0.3 Windows 环境（操作台）

```powershell
conda create -y -n lerobot python=3.10
conda activate lerobot
cd C:\Users\13402\Desktop\FDUROP\LeRobot
pip install -e ".[feetech]"
pip install pyzmq pygame      # client 需要 zmq
```

### 0.4 自检

```bash
$P -c "import numpy, cv2, zmq; print('ok')"
```

---

## 1. 远程遥控（推荐）

机器人和操作台分开时用这套。机器人那台只跑 host，这台跑客户端。

### 1.1 机器人端：起 host

```bash
ssh user@10.162.152.173
cd ~/xhz/XLeRobot_wmf/LeRobot
pkill -f 4_xlerobot_teleop_keyboard.py
tmux new -s robot
P=~/miniconda3/envs/lerobot/bin/python
PYTHONPATH=src $P -m lerobot.robots.xlerobot.xlerobot_host \
  --robot-id my_xlerobot_pc --connection-time-s 28800
```

> 要在操作台上录制，命令末尾再加 `--publish-state`（见 3.0）。

1. `Press ENTER to restore calibration` → **直接回车**（别按 `c`）
2. 反复打印 `No command available` + `Stopping the base.` → **正常**，在等客户端
3. `Ctrl-B` 再按 `D` 脱离（host 继续跑）；回来用 `tmux attach -t robot`

### 1.2 操作台：**新开一个窗口**起客户端

```powershell
conda activate lerobot
cd C:\Users\13402\Desktop\FDUROP\LeRobot
python examples\xlerobot\4_xlerobot_teleop_keyboard.py --mode client --remote-ip 10.162.152.173
```

- 看到 `[left]/[right] Moving to Zero Position` → **手臂会动**，先确认周围清空
- `b` 或 `Ctrl-C` 中止：先复位到 `start_pose` 再退出
- 客户端使用**全局键盘监听**：遥控期间别在别的窗口打字，否则会被当成机器人指令

> 只有键盘脚本支持 `--mode client`。`5_...xbox.py` / `7_...joycon.py` 传了会报 `unrecognized arguments`。

### 1.3 键盘键位

**左臂**

| 键 | 动作 |
|---|---|
| `w` `s` `a` `d` | X/Y 平面移动 |
| `q` / `e` | `shoulder_pan` −/+ |
| `r` / `f` | `wrist_roll` +/− |
| `z` / `x` | `pitch` +/− |
| `t` / `g` | 夹爪 张开 / 夹紧（步进 3） |
| `c` | 回 `start_pose` |
| `y` | 矩形轨迹（测试用） |

**右臂（小键盘）**

| 键 | 动作 |
|---|---|
| `8` `2` `4` `6` | X/Y 平面移动 |
| `7` / `9` | `shoulder_pan` −/+ |
| `/` / `*` | `wrist_roll` +/− |
| `1` / `3` | `pitch` +/− |
| `+` / `-` | 夹爪 张开 / 夹紧 |
| `0` | 回 `start_pose` |

**头部**

| 键 | 动作 |
|---|---|
| `<` / `>` | `head_motor_1` +/− |
| `,` / `.` | `head_motor_2` +/− |
| `?` | 回 `start_pose` |

**底盘**

| 键 | 动作 |
|---|---|
| `i` / `k` | 前进 / 后退 |
| `j` / `l` | 左移 / 右移 |
| `u` / `o` | 左转 / 右转 |
| `n` / `m` | 速度档 +1 / −1（3 档） |

---

## 2. 本地遥控（机器人直接插本机）

### 2.1 手柄（Xbox）

```bash
PYTHONPATH=src $P examples/xlerobot/5_xlerobot_teleop_xbox.py --publish-state
```

| 操作 | 动作 |
|---|---|
| 左摇杆 ↑↓←→ | 左臂 XY 平面 |
| 按住左摇杆 + ←→ | 左臂 `shoulder_pan` |
| 按住 LB + 左摇杆 | `pitch`（上下）/ `wrist_roll`（左右） |
| LT / RT | 夹爪：松开 = 0（夹紧），按到底 = 100（张开） |
| 右摇杆 / RS / RB / RT | 右臂，与左侧对称 |
| 十字键 ↑ / ↓ | 后退 / 前进（↑ 是反的） |
| 十字键 ←→ | 左转 / 右转 |
| 单按 LB / RB | 底盘速度档 −1 / +1 |
| Back | 双臂 + 头部回 `start_pose`（继续遥操） |
| Start / `Ctrl-C` | 复位到 `start_pose` 后**退出** |

> ⚠️ 录制中中止前先按 `o` 结束当前段，否则复位的 2 秒会被录进数据。

### 2.2 键盘

```bash
PYTHONPATH=src $P examples/xlerobot/4_xlerobot_teleop_keyboard.py --publish-state
```

键位见 1.3；中止键是 `b`。

### 2.3 灵敏度（两套通用）

| 参数 | 默认 | 作用 |
|---|---|---|
| `--degree-step` | `2.0` | 每帧关节增量（度） |
| `--xy-step` | `0.005` | 每帧平面增量（米） |
| `--kp` | `1.0` | 跟随增益（小 = 更柔） |
| `--stick-deadzone` | `0.1` | 摇杆死区 |
| `--stick-expo` | `0.0` | 摇杆曲线（推荐 `0.5~1.0`） |

```bash
--degree-step 1.0 --xy-step 0.002 --stick-expo 1.0    # 精细（装配、插拔）
--degree-step 4.0 --xy-step 0.010 --stick-expo 0      # 快速搬运
```

> 步长与帧率相关：`degree_step=2` 在 30 Hz 下是 60°/s。底盘速度不走这些参数，用 LB/RB 切三档。

---

## 3. 录制

终端 1 起遥操（带 `--publish-state`），终端 2 起录制器。

> 录制器跑在**哪台**，数据就写在**哪台**。
> 同机录制直接用 3.1/3.2；录制器要放操作台时，先按 **3.0** 打通状态通道。

### 3.0 远程录制（录制器在操作台，走 SSH 隧道）

录制器只从 `127.0.0.1:8765` 拉状态，所以**不用改代码**：用 SSH 把机器人的端口映射到操作台本地即可。

**① 机器人端：起 host 时带 `--publish-state`**

```bash
PYTHONPATH=src $P -m lerobot.robots.xlerobot.xlerobot_host \
  --robot-id my_xlerobot_pc --connection-time-s 28800 --publish-state
```

看到 `Read-only state stream: http://127.0.0.1:8765` 即成功。
状态源只绑本机，局域网直接访问不到（要的就是这个）。

**② 操作台：开隧道（保持这个窗口不关）**

```bash
ssh -N -o ServerAliveInterval=30 -L 8765:127.0.0.1:8765 user@10.162.152.173
```

**③ 操作台：另开一个窗口，先探活再录**

```bash
$P dsh/raw_recorder.py --probe-only
# 期望：✅ 已连接：robot_type=xlerobot state=17 action=17 cameras=[...]

$P dsh/raw_recorder.py --format v30 \
  --v30-out ./data/serve/local/your_task \
  --repo-id local/your_task --task "你的任务描述" --overwrite
```

Windows 操作台等价写法（PowerShell）：

```powershell
ssh -N -L 8765:127.0.0.1:8765 user@10.162.152.173
conda activate lerobot
python dsh\raw_recorder.py --probe-only
python dsh\raw_recorder.py --format v30 --v30-out data\serve\local\your_task `
  --repo-id local/your_task --task "pick up the cube" --overwrite
```

| 项 | 说明 |
|---|---|
| 端口 | 隧道两端都用默认 `8765`，一般不用改 `--port` |
| 数据落盘 | **写在运行录制器的那台**（操作台）；机器人端磁盘不占 |
| 带宽 | 三路 JPEG ≈ 35~40 Mbps，与 ZMQ 遥控同量级；两台都建议 5 GHz 或有线 |
| 抖动 | 状态年龄超过 `--max-state-age-s`（默认 0.5 s）会被丢弃；隧道卡顿先跑 `--probe-only` 和 `ping` |
| 同时遥控 | 隧道 + ZMQ 遥控可以并存，但两条流都吃带宽 |
| 安全性 | 隧道默认只绑操作台 `127.0.0.1`；**不要**改成 `-L 0.0.0.0:8765:...`，那等于把相机画面暴露给整个局域网 |
| Windows 操作台 | 系统自带 OpenSSH 客户端；PowerShell 里把 `$P` 换成 `python`，探活用 `python dsh\raw_recorder.py --probe-only` |

> 隧道断了会表现为"状态过期"丢帧或录制停止；重连隧道后**重新 `--probe-only`** 再开始录。

### 3.1 raw 母版（默认，需再转 v2.1）

先探活：

```bash
$P dsh/raw_recorder.py --root ~/xhz/XLeRobot_wmf/LeRobot/dsh/out/raw --probe-only
```

期望：`✅ 已连接：robot_type=xlerobot state=17 action=17 cameras=[...]`

正式录：

```bash
$P dsh/raw_recorder.py \
  --root ~/xhz/XLeRobot_wmf/LeRobot/dsh/out/raw \
  --task "你的任务描述"
```

输出：`dsh/out/raw/<时间戳>/ep_000001/{meta.json, samples.npz, imgs/}`

### 3.2 直接写 LeRobot v3.0（可直训，不用转换）

```bash
$P dsh/raw_recorder.py \
  --format v30 \
  --v30-out ~/xhz/XLeRobot_wmf/LeRobot/data/serve/local/your_task \
  --repo-id local/your_task \
  --task "你的任务描述" \
  --overwrite
```

> ⚠️ 不要在 `\` 续行的中间写 `#` 注释，会把命令截断。

### 3.3 录制按键（两种格式相同）

| 键 | 作用 |
|---|---|
| `o` | 开始 / 结束一段（结束即保存，≥15 帧才保留） |
| `d` | 丢弃当前段 |
| `p` | 打印帧数 / 滞后 |
| `q` | 退出（正在录会先保存） |

> 节奏：按 `o` → 做动作 → 按 `o`。录制中**不要 `Ctrl-C`**（会留 `.tmp`）。
> v3.0 模式**必须正常 `q` 退出**，否则 parquet footer 写不出去。

### 3.4 `--task` 规则（两种格式都适用）

- 描述**任务目标 + 对象**，不描述实现：`push the block to the right` ✅ / `move the left arm` ❌
- 一句话、10 个词以内
- 同一任务的所有 episode 用**完全相同**的字符串
- 示例：`"pick up the red block and put it in the plate"`

| | `raw` | `v30` |
|---|---|---|
| 落盘 | npz + JPEG | 一个 v3.0 数据集 |
| 维度 | 17 维全存 | 12 维（去头/底盘） |
| 视频 | 不编码 | 录制时直接 h264 |
| 转换 | 需要 `convert_to_v21.py` | **不需要** |

---

## 4. 转 v2.1

```bash
$P dsh/convert_to_v21.py \
  --raw dsh/out/raw \
  --out data/serve/local/your_task \
  --task "你的任务描述" \
  --overwrite
```

> 🔴 `--task` 必须和录制时**完全一致**（它直接写进 `meta/tasks.jsonl` 当训练 prompt）。
> 忘了就查：`cat dsh/out/raw/*/ep_*/meta.json | grep task`

产物：`meta/` + `data/chunk-000/episode_000000.parquet` + `videos/chunk-000/observation.images.{head,left_wrist,right_wrist}/`。

录完先检查：

```bash
$P - <<'PY'
import glob, json, numpy as np
for ep in sorted(glob.glob("dsh/out/raw/*/ep_*")):
    m = json.load(open(f"{ep}/meta.json")); d = np.load(f"{ep}/samples.npz")
    span = d["state"].max(0) - d["state"].min(0)
    print(f"{ep}\n  {m['frame_count']} 帧 | {m['duration_s']:.1f}s | {m['nominal_fps']:.1f}fps | {m['status']}")
    print("  动过的关节:", {n: round(float(s), 1) for n, s in zip(m["state_names"], span) if s > 5})
PY
```

`nominal_fps` 应 ≈ 30；"动过的关节"至少几个 > 10，否则这段是静止画面。

---

## 5. 常见问题

| 现象 | 处理 |
|---|---|
| `No module named 'numpy'` | 用了 base 环境，改用 `$P` |
| `No such file or directory: .../examples/...` | 当前目录不对，先 `cd` 到仓库根 |
| 客户端 `Timeout waiting for LeKiwi Host to connect expired` | host 没在跑；机器人端 `pgrep -af xlerobot_host`，重起 host |
| host 一直刷 `No command available` | 正常，在等客户端 |
| `ping` 不通 | 不同网段 / 校园网 AP 隔离 → 换手机热点，或两端挂同一 VPN |
| `nc` 报 refused | 机器人端 `sudo ufw allow 5555/tcp`、`sudo ufw allow 5556/tcp` |
| 连上但没画面 | 只通了 5555，放行 5556 |
| 画面卡、车也卡 | 带宽不足，机器人端调小 `cam_fps` 或 `cam_width/height` |
| `[TxRxResult] Port is in use!` | 有进程占着 `/dev/ttyACM*`，`pkill -f xlerobot_host` 后只留一个 |
| 遥操报 `Torque_Enable ... no status packet` | 舵机掉包；`$P dsh/diag_servos.py --ports /dev/ttyACM0,/dev/ttyACM1` |
| 相机缺失 `['right_wrist']` | 相机从 USB 掉线；`ls /dev/v4l/by-path/`、`sudo dmesg -T \| tail -30` |
| `数据源 8765 不可用` | 终端 1 没起或没带 `--publish-state` |
| 按 `o` 没反应 | 终端 2 没有键盘焦点，先点一下窗口 |
| SSH 里跑键盘遥操报 `KeyboardTeleop is not connected` | SSH 无 `DISPLAY`，pynput 被跳过；远程请用 `--mode client` |

---

## 6. 已知问题

1. **十字键上下反向**：↑ 是后退。
2. **`shoulder_pan` 要按住摇杆帽才动**，不按只有 XY 平面运动。
3. **USB 相机间歇掉线**：三路轮流消失（供电/带宽问题）。raw 会标 `degraded`，v30 跳过缺帧，转换器跳过 `degraded` 段。排查 `sudo dmesg -T | grep -i over-current`，有就换带供电的 hub。
4. **标定/数据目录**：`xlerobot_config.json` 里 `calibration_dir`、`record_data_root` 指向 `~/xhz/XLeRobot/...`，标定文件在那份 checkout 里，别删。
