# XLeRobot 采集与转换 · 操作手册

一条链：**手柄遥操 → 键盘录制原始层 → 转成 LeRobot v2.1 训练集**。

每个新终端先做这两行（后面用 `$P` 指代解释器）：

```bash
cd ~/xhz/XLeRobot_wmf/LeRobot
P=~/miniconda3/envs/lerobot/bin/python
```

> ⚠️ 必须用 `$P`，直接敲 `python` 会落到 `(base)` 环境，报 `No module named 'numpy'`。
> 确认命令：`$P -c "import numpy, cv2; print('ok')"`

---

> **每个新终端先执行**：`P=~/miniconda3/envs/lerobot/bin/python`
> 否则命令里的 `$P` 是空串，会报 `-c：未找到命令`。

## 一、手柄控制（终端 1）

```bash
PYTHONPATH=src $P examples/xlerobot/5_xlerobot_teleop_xbox.py --publish-state
```

**`PYTHONPATH=src` 不能省**，否则会 import 到另一份 checkout，`--publish-state` 参数不存在。

启动流程：

1. 打印 `Press ENTER to restore calibration from file...` → **直接按回车**（不要按 `c`，重标定会覆盖标定文件）
2. 机械臂移动到 `start_pose`
3. 看到这一行即成功：

```
[MAIN] Read-only state stream: http://127.0.0.1:8765  (30 Hz)
```

### 灵敏度调节

四个旋钮，都能用命令行改（默认值即原行为）：

| 参数 | 默认 | 作用 | 调大的效果 |
|---|---|---|---|
| `--degree-step` | `2.0` | 每帧关节增量（度） | 关节转得更快 |
| `--xy-step` | `0.005` | 每帧平面增量（米） | 手臂平移更快 |
| `--kp` | `1.0` | 跟随增益 | 更跟手；调小则更柔、有滞后 |
| `--stick-deadzone` | `0.1` | 摇杆死区 | 更迟钝、抗漂移 |
| `--stick-expo` | `0.0` | 摇杆曲线 | 中间段更细腻（推荐 `0.5~1.0`） |

**注意步长与帧率相关**：`degree_step=2` 在 30 Hz 下是 60°/s，在 60 Hz 下就是 120°/s。

**摇杆现在是按偏移量成比例的**（以前是 `>0.5` 二值，推一半就是全速）：

```
摇杆 0.3 → 增益 0.22     摇杆 0.5 → 增益 0.44
摇杆 0.75 → 增益 0.72    摇杆 1.0 → 增益 1.00
```

加 `--stick-expo 1.0` 后中间更细腻：

```
摇杆 0.3 → 0.01    摇杆 0.5 → 0.09
摇杆 0.75 → 0.38   摇杆 1.0 → 1.00
```

**常用组合**

```bash
# 精细操作（装配、插拔）
--degree-step 1.0 --xy-step 0.002 --stick-expo 1.0

# 快速搬运
--degree-step 4.0 --xy-step 0.010 --stick-expo 0

# 跟随偏软（不会一推就冲）
--kp 0.5 --degree-step 2.0
```

**底盘速度**不走这些参数，用 LB / RB 切三档（0.1 / 0.2 / 0.3 m/s）。

### 键位

| 操作 | 机器人动作 |
|---|---|
| 左摇杆 ↑↓←→ | 左臂在 XY 平面移动（IK 解算） |
| **按住**左摇杆 + ←→ | 左臂 `shoulder_pan` |
| **按住 LB** + 左摇杆 | 左臂 `pitch`（上下）/ `wrist_roll`（左右） |
| 左扳机 LT | 左夹爪：松开 = 0（夹紧），按到底 = 100（张开） |
| 右摇杆 / RS / RB / RT | 右臂，与左侧对称 |
| 十字键 ↑ | **后退**（反的） |
| 十字键 ↓ | **前进** |
| 十字键 ←→ | 左转 / 右转 |
| 单按 LB / RB | 底盘速度档 −1 / +1（1~3 档） |
| Back | 双臂 + 头部回到 `start_pose`（继续遥操） |
| **Start** | **中止：先复位到 `start_pose`，再退出进程** |
| **Ctrl-C** | 同 Start（先复位再退出） |
| A / B / X / Y | 未使用 |

> ⚠️ LB 有双重职责：按住推摇杆 = 调 pitch，单独按 = 降底盘档位。用 LB 调俯仰时会顺带把底盘速度压到最低档。

> **中止 vs Back 的区别**：Back 只复位、继续遥操；Start / Ctrl-C 会复位后**退出进程**（约 2 秒平滑回位，复位期间底盘锁零）。
>
> ⚠️ **录制中中止前，先按 `o` 结束当前段**，否则复位的那 2 秒动作会被录进数据。
>
> 中止时若看到 `[TxRxResult] Port is in use!`，说明 Ctrl-C 打断了一次串口事务，
> 使 SDK 的 `is_using` 卡在 True。现在已经自动清除（`release_serial_ports()`），
> 并且 Ctrl-C 改成**只置标志**、不再抛异常，从源头避免打断串口读写。

---

## 一·附、键盘遥操（没有手柄时用）

```bash
cd ~/xhz/XLeRobot_wmf/LeRobot
P=~/miniconda3/envs/lerobot/bin/python
PYTHONPATH=src $P examples/xlerobot/4_xlerobot_teleop_keyboard.py --publish-state
```

`--publish-state` 会同样把只读状态发布到 `127.0.0.1:8765`，所以**录制器完全不用改**，照常用
`dsh/raw_recorder.py` 采集。

### 键位

**左臂**

| 键 | 动作 |
|---|---|
| `w` `s` `a` `d` | 左臂 X/Y 平面移动 |
| `q` / `e` | `shoulder_pan` −/+ |
| `r` / `f` | `wrist_roll` +/− |
| `z` / `x` | `pitch` +/− |
| `t` / `g` | 夹爪 **张开 / 夹紧**（步进 3，夹在 0~100） |
| `c` | 左臂回 `start_pose`（继续遥操） |
| `y` | 左臂矩形轨迹（测试用） |

**右臂**（小键盘）

| 键 | 动作 |
|---|---|
| `8` `2` `4` `6` | 右臂 X/Y 平面移动 |
| `7` / `9` | `shoulder_pan` −/+ |
| `/` / `*` | `wrist_roll` +/− |
| `1` / `3` | `pitch` +/− |
| `+` / `-` | 夹爪 **张开 / 夹紧** |
| `0` | 右臂回 `start_pose` |

**头部**

| 键 | 动作 |
|---|---|
| `<` / `>` | `head_motor_1` +/− |
| `,` / `.` | `head_motor_2` +/− |
| `?` | 头部回 `start_pose` |

**底盘**

| 键 | 动作 |
|---|---|
| `i` / `k` | 前进 / 后退 |
| `j` / `l` | 左移 / 右移 |
| `u` / `o` | 左转 / 右转 |
| `n` / `m` | 速度档 +1 / −1（3 档） |

### 中止（与手柄一致：先复位再退出）

| 键 | 行为 |
|---|---|
| **`b`** | 中止：底盘锁零 → 双臂 + 头回 `start_pose`（约 2 秒）→ 退出进程 |
| **Ctrl-C** | 同上 |

> 手柄那边是 **Start**；键盘这边是 `b`（来自 `config.teleop_keys["quit"]`）。

### 和手柄的差异

| 项 | 手柄 | 键盘 |
|---|---|---|
| 夹爪 | 模拟量，连续 0~100 | **步进**，每帧 ±3，夹在 0~100 |
| 夹爪单侧方向 | 左右各自配置 | 同一套 `t/g`、`+/-`，方向由按键定义 |
| 中止 | Start / Ctrl-C | `b` / Ctrl-C |
| 灵敏度 | `--degree-step/--xy-step/--kp/--stick-*` | `--degree-step/--xy-step/--head-degree-step/--kp` |

### 录制（两种方式）

**方式 A：独立录制器（推荐，与手柄完全一致）**

终端 1 起键盘遥操并发布状态，终端 2 用 `dsh/raw_recorder.py`：

```bash
# 终端 1
PYTHONPATH=src $P examples/xlerobot/4_xlerobot_teleop_keyboard.py --publish-state
# 终端 2
$P dsh/raw_recorder.py --format v30 \
  --v30-out $PWD/data/serve/local/pick_cube \
  --repo-id local/pick_cube --task "pick up the cube"
```

**方式 B：脚本内嵌录制（一个终端搞定）**

键盘脚本的 `--record` 现在**直接调用 `dsh/raw_recorder.py` 的写入器**，可选 raw 或 v3.0：

```bash
# 写 raw 母版（需再跑 convert_to_v21.py）
PYTHONPATH=src $P examples/xlerobot/4_xlerobot_teleop_keyboard.py \
  --record --record-format raw --record-root dsh/out/raw \
  --task-description "pick up the cube"

# 直接写 LeRobot v3.0（可直接训练）
PYTHONPATH=src $P examples/xlerobot/4_xlerobot_teleop_keyboard.py \
  --record --record-format v30 \
  --record-out $PWD/data/serve/local/pick_cube \
  --record-repo-id local/pick_cube \
  --task-description "pick up the cube"
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `--record` | 关 | 开启内嵌录制 |
| `--record-format` | `raw` | `raw` = npz+JPEG 母版；`v30` = 直接写数据集 |
| `--record-root` | `dsh/out/raw` | raw 模式输出根目录 |
| `--record-out` | — | v30 模式数据集目录（**必填**） |
| `--record-repo-id` | `local/<目录名>` | v30 的 repo_id |
| `--task-description` | `My task description` | 任务描述 → 训练 prompt，**务必改写** |
| `--episode-time-sec` | **0** | 自动分段间隔（秒）。`0` = 不切，用空格手动控制；>0 时到点会**保存当前段并立即开下一段**（不会停止录制，但任务会被切在任意位置） |

**录制中的按键**

| 键 | 作用 |
|---|---|
| `空格` | 开始 / 结束一段（结束即保存，≥15 帧才保留） |
| `Backspace` | 丢弃当前段 |

> 启动时会自动开始第一段。
> 内嵌录制复用同一套写入器，所以**格式、12 维裁剪、相机缺失标记都与独立录制器一致**。

---

## 一·附2、另一台电脑遥控（host / client）

机器人和操作台分开时用这套：机器人那台只跑 host，另一台跑遥操客户端。
两台都要有**同版本**的仓库和环境。

### 架构

```
机器人电脑                            操作台电脑
xlerobot_host                         4_xlerobot_teleop_keyboard.py --mode client
  bind 0.0.0.0:5555   ←── 指令 ──────  connect <机器人IP>:5555
  bind 0.0.0.0:5556   ── 观测 ──────→  3 路 JPEG + 本体感觉
```

host 绑定的是 `0.0.0.0`，所以默认就支持远程，不需要改绑定地址。

### 网络前提：不要求同一个 WiFi

两台机器**不必连同一个 SSID**，真正的要求是 **IP 层能互通**（能 ping 通、能建 TCP 连接）。

| 情形 | 能否连通 | 说明 |
|---|---|---|
| 同一路由器的同一网段（同 SSID，或一台有线一台无线） | ✅ | 最常见 |
| 不同 SSID / 不同 AP，但在同一 VLAN、同一子网 | ✅ 通常可以 | 校园网常见；本例是 `10.162.128.0/17` |
| 不同私有网段、彼此无路由 | ❌ | 需要路由器或 VPN 打通 |

**先判断（在操作台上跑）**：

```bash
ip -4 addr show | grep inet          # 自己是不是也在 10.162.x.x
ping -c 3 10.162.152.173
nc -vz 10.162.152.173 5555
```

- 同网段且 `ping` 通 → 直接连
- 不通、但 `ping 网关` 通 → **大概率是校园网的 AP 隔离**（同一个 WiFi 也互相看不见），换方案

**不在同一网络时的三种办法**（按推荐度排）：

| 方案 | 说明 | 注意 |
|---|---|---|
| **手机热点**（最省事） | 两台都连热点，自动同网段（通常 `192.168.43.x`），5 GHz 带宽够 | 无需任何配置 |
| **两端挂同一个 VPN** | 用 VPN 分配地址，例如机器人端 `utun7` 的 `2.0.0.1`，客户端 `--remote-ip 2.0.0.1` | 两端必须在同一 VPN，且 `ping` 通 |
| 端口转发 / 内网穿透 | 把 5555/5556 暴露到公网 | ⚠️ **不建议**：ZMQ 无认证无加密。要用就走 SSH 隧道 `ssh -L 5555:localhost:5555 -L 5556:localhost:5556 <机器人>`，顺便加密 |

> ⚠️ **目前只有键盘脚本支持 `--mode client`**。`5_xlerobot_teleop_xbox.py` /
> `7_xlerobot_teleop_joycon.py` 还没有 `--mode` / `--remote-ip` 参数，给它们传会直接报
> `unrecognized arguments`。手柄/Joy-Con 远程要先把 client 分支补进这两个脚本。

### ① 机器人电脑：起 host

```bash
PYTHONPATH=src $P -m lerobot.robots.xlerobot.xlerobot_host \
  --robot-id my_xlerobot_pc --connection-time-s 28800
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `--port-zmq-cmd` | 5555 | 指令端口，两端必须一致 |
| `--port-zmq-observations` | 5556 | 观测端口，两端必须一致 |
| `--connection-time-s` | 3600 | host 存活时长（秒），长任务调大 |
| `--watchdog-timeout-ms` | 500 | 超过就主动停底盘（安全兜底） |
| `--max-loop-freq-hz` | 30 | 主循环上限 |

启动后：

1. `Press ENTER to restore calibration from file` → **直接回车**（别按 `c`）
2. 周期打印 `WARNING: No command available` + `Stopping the base.`
   → **正常**，说明串口通了、标定加载了、正在等客户端；底盘被 watchdog 停住是预期行为

> host **不会自己让机器人动**。它只是把状态发出去、把指令收进来；只有客户端连上、开始发指令，
> 机械臂才会回 `start_pose`。所以看到满屏 `No command available` 不是故障，是"待机"。

### ①·附、用 SSH 在机器人那台起 host（推荐）

操作台连着机器人时，不必跑到机器人电脑跟前——SSH 进去起即可。但**别在 SSH 前台直接跑**：
关窗口 / 断网 / 合盖休眠都会杀掉 host（SIGHUP），机器人立刻失去指令。

**方式 A：tmux（推荐）**

```bash
ssh user@10.162.152.173
cd ~/xhz/XLeRobot_wmf/LeRobot
pkill -f 4_xlerobot_teleop_keyboard.py          # 清掉可能占着串口的残留
fuser -v /dev/ttyACM0 /dev/ttyACM1              # 没输出 = 串口空闲

tmux new -s robot
P=~/miniconda3/envs/lerobot/bin/python
PYTHONPATH=src $P -m lerobot.robots.xlerobot.xlerobot_host \
  --robot-id my_xlerobot_pc --connection-time-s 28800
# 见到 restore calibration 提示按回车；Ctrl-B 再按 D 脱离
```

| 命令 | 作用 |
|---|---|
| `tmux new -s robot` | 建会话并进入 |
| `Ctrl-B` 然后 `D` | 脱离（人走了，host 继续跑） |
| `tmux ls` | 看有哪些会话 |
| `tmux attach -t robot` | 接回去看日志 / 按回车 |
| `tmux kill-session -t robot` | 连里面的 host 一起杀掉 |

**方式 B：nohup（不想装 tmux）**

```bash
cd ~/xhz/XLeRobot_wmf/LeRobot
P=~/miniconda3/envs/lerobot/bin/python
echo "" | nohup env PYTHONPATH=src $P -m lerobot.robots.xlerobot.xlerobot_host \
  --robot-id my_xlerobot_pc --connection-time-s 28800 \
  > /tmp/xlerobot_host.log 2>&1 &

tail -f /tmp/xlerobot_host.log     # 看状态
pkill -f xlerobot_host             # 停掉
```

> `echo "" |` 是给那句 `input()` 喂一个回车（**不能**用 `< /dev/null`：`input()` 直接 EOFError 崩掉）。

> ⚠️ **同一时刻只能有一个 host**。重复起会抢串口报 `Port is in use`，先 `pgrep -af xlerobot_host` 确认。

### ② 查机器人 IP（别拿 VPN 地址）

```bash
hostname -I
ip -4 addr show | grep inet
```

`hostname -I` 会把所有地址列出来，**只取局域网网卡那个 IPv4**：

```
inet 2.0.0.1/24      dev utun7       ← VPN 隧道，别用
inet 10.162.152.173/17 dev wlp131s0  ← 用这个
```

### ③ 操作台：先测连通

```bash
ping -c 3 10.162.152.173
nc -vz 10.162.152.173 5555
nc -vz 10.162.152.173 5556
```

两个端口都通再往下走。

### ④ 操作台：起客户端

连上后 host 端的 `No command available` 停止刷屏，客户端按原键位操作。

**Linux 操作台**

```bash
cd <仓库目录>
P=~/miniconda3/envs/lerobot/bin/python
PYTHONPATH=src $P examples/xlerobot/4_xlerobot_teleop_keyboard.py \
  --mode client --remote-ip 10.162.152.173
```

**Windows 操作台**（一次性装环境）

```powershell
conda create -y -n lerobot python=3.10
conda activate lerobot
cd C:\Users\13402\Desktop\FDUROP\LeRobot
pip install -e ".[feetech]"
pip install pyzmq pygame        # 基础依赖里没有这两个，client 需要 zmq
```

> 国内慢就加 `-i https://pypi.tuna.tsinghua.edu.cn/simple`；torch 会装 CPU 版，约 216 MB。

之后每次遥控：

```powershell
conda activate lerobot
cd C:\Users\13402\Desktop\FDUROP\LeRobot
python examples\xlerobot\4_xlerobot_teleop_keyboard.py --mode client --remote-ip 10.162.152.173
```

> 🔴 **必须先 `conda activate lerobot`**。rerun 查看器在 `envs\lerobot\Scripts\rerun.exe`，
> 不在 PATH 里就只会打印 `Rerun viewer unavailable ... Visualization disabled`：
> 遥控**仍能正常用**，但三路相机画面看不到。激活后查看器正常弹出。

> 客户端**不需要**串口、标定文件、本机相机——状态和画面全部从 host 那台经 ZMQ 过来。
> 所以操作台上 `COM*` / `/dev/ttyACM*` 是什么都没有也不影响。

> ⚠️ **Windows 操作台上不要放 `src/lerobot/robots/xlerobot/xlerobot_config.json`**。
> 那个覆盖文件由 host 和 client **共用**，而 `XLerobotClientConfig` 没有 `port1/port2` 字段，
> 写了会报 `unknown field(s)`；`calibration_dir` 写成字符串还会在 client 侧报
> `'str' object has no attribute 'mkdir'`（client 配置里该字段默认 None，不会被转成 Path）。
> 本地模式（机器人直连本机）时才需要这个文件。

> `connect_timeout_s` 只有 **5 秒**：host 还没完全起来时连会报
> `Timeout waiting for LeKiwi Host to connect expired`，隔几秒重跑一次即可。

启动后先看到 `[left]/[right] Moving to Zero Position` —— **这时手臂就会动**（回 `start_pose`），
确认周围清空再继续。

### 注意事项

| 项 | 说明 |
|---|---|
| **手柄位置** | 插在**操作台**，不是机器人那台 |
| **录制位置** | `--record` 的数据写在**操作台**本地；host 占着两个串口，机器人那台不能再跑 `raw_recorder.py` / `state_publisher.py` |
| **`--publish-state`** | client 模式无效（状态源只绑 `127.0.0.1:8765`），脚本会打印"已忽略" |
| **全局键盘钩子** | 客户端用的是 pynput **全局**监听：遥控期间在**任何窗口**敲 `i/j/k/l/w/a/s/d` 都会被当成机器人指令。别切去打字，也别忘了 `b` 是中止 |
| **host 别被顺手关掉** | host 跑在 SSH 前台时，关窗口 / 断网 / 合盖都会杀掉它 → 客户端立刻 `Timeout`。用 tmux 或 nohup（见 ①·附） |
| **带宽** | 3 路 640×480 JPEG(q90) × 30 Hz ≈ **35~40 Mbps**；优先有线，其次 5 GHz。两个 socket 都是 `CONFLATE=1`，带宽不够只会掉帧，不会积压延迟 |
| **防火墙** | 机器人端 `sudo ufw allow 5555/tcp`、`sudo ufw allow 5556/tcp` |

### 连不上怎么查

| 现象 | 原因 | 处理 |
|---|---|---|
| `ping` 不通，但 `ping 网关` 通 | **校园网 AP 隔离** | 手机热点 / 网线直连 / 两台挂同一 VPN |
| `nc` 报 refused | 防火墙 | ufw 放行 5555/5556 |
| 客户端 5 秒连接超时 | IP 错 / 不同网段 / 流量被 VPN 劫持 | 客户端跑 `ip route get <机器人IP>` 看出口网卡 |
| 连上但没有画面 | 只通了 5555 | 放行 5556 |
| 画面一顿一顿、车也卡 | 带宽不足 | 机器人端调小 `cam_fps` 或 `cam_width/height` |
| 画面在、车不动 | 客户端主循环被阻塞（如 Rerun 渲染），超过 500 ms 触发 watchdog | 关掉 Rerun / 降预览帧率 |
| 客户端报 `Timeout waiting for LeKiwi Host to connect expired` | host 没在跑（被 Ctrl-C / 关窗口 / 串口被别的进程占着） | 机器人端 `pgrep -af xlerobot_host`、`fuser -v /dev/ttyACM0 /dev/ttyACM1`；按 ①·附 重起 host |
| host 起来了但一直刷 `No command available` | **不是故障**，host 在等客户端 | 起客户端即可 |
| SSH 里跑 `4_...keyboard.py` 报 `KeyboardTeleop is not connected` | SSH 没有 `DISPLAY`，pynput 被主动跳过 | 键盘遥操只能在有图形界面的机器上跑；远程请用 client 模式 |
| Windows 客户端报 `Rerun viewer unavailable` | 没 `conda activate lerobot` | 激活环境重跑；不影响遥控，只是没画面 |
| `[TxRxResult] Port is in use!` | 有另一个进程占着 `/dev/ttyACM*` | `pkill -f 4_xlerobot_teleop_keyboard.py`、`fuser -v /dev/ttyACM*`，只留一个 host |

### 键盘脚本为什么不能在 SSH 里跑

`teleop_keyboard.py` 开头有这段判断：

```python
if ("DISPLAY" not in os.environ) and ("linux" in sys.platform):
    raise ImportError("pynput blocked intentionally due to no display.")
```

SSH 会话没有 `DISPLAY` → pynput 根本不导入 → `connect()` 什么都不做、`listener=None`
→ 第一次 `get_action()` 就抛 `DeviceNotConnectedError: KeyboardTeleop is not connected`，
`finally` 里的 `keyboard.disconnect()` 还会再抛一次（次生错误，可忽略）。

所以两个正确姿势只有：

1. **远程**：操作台上跑 `--mode client`（键盘插在操作台）；
2. **本地**：坐在机器人那台的物理显示器前，在它自己的桌面终端里跑（`ssh -X` 不行，
   pynput 抓的是 X server 那边的键，你本地按的传不过去）。

---

## 二、启动录制器（终端 2）

先探活（不录制）：

```bash
$P dsh/raw_recorder.py \
  --root ~/xhz/XLeRobot_wmf/LeRobot/dsh/out/raw \
  --probe-only
```

期望看到：

```
✅ 已连接：robot_type=xlerobot state=17 action=17 cameras=[...] seq=123
```

正式录制：

```bash
# --task 每次采集都要按实际任务改写，规则见下方提醒
$P dsh/raw_recorder.py \
  --root ~/xhz/XLeRobot_wmf/LeRobot/dsh/out/raw \
  --task "你的任务描述"
```

> 🔴 **`--task` 必须自己按任务改写，不要照抄示例。**
>
> 这个字符串是**最终喂给模型的指令（prompt）**，不是备注、不是标签。
> 写错或写得含糊，模型就会学到"这句话对应这套动作"，事后改不了，只能重录重转。
>
> **规则**：
> - 描述**任务目标 + 对象**，不描述实现：`push the block to the right` ✅ / `move the left arm` ❌
> - **一句话、10 个词以内**（state 的 12 个数字已占 20~40 token，总预算只有 200）
> - **同一个任务的所有 episode 必须用完全相同的字符串**
> - 不同任务用不同字符串，可以放在同一个数据集的 `tasks.jsonl` 里
>
> 示例：`"pick up the red block and put it in the plate"`

看到 `== 录制器就绪 ==` 后：

| 键 | 作用 |
|---|---|
| `o` | 打开 / 关闭开关（开 = 开始录一段，关 = 保存） |
| `d` | 丢弃当前段 |
| `p` | 打印当前帧数 / 滞后 |
| `q` | 退出（正在录会先保存） |

**操作节奏**：按 `o` → 手放回手柄做动作 → 做完按 `o`。

> 录制器会等数据源最多 15 秒（`--wait-ready`），所以两个终端**先后顺序无所谓**。
> 但 `o` 必须送到终端 2，按键前先点一下那个窗口。

输出：

```
dsh/out/raw/<时间戳>/ep_000001/
├── meta.json      # 帧数、时长、实际 fps、status
├── samples.npz    # t_recv / seq / state[N,17] / action[N,17]
└── imgs/          # 每帧 3 张 640×480 JPEG
```

**录制中不要 Ctrl-C**（会留下 `.tmp`，状态和动作丢失）。要中断按 `d` 或 `o`。

### 另一种输出：直接写 LeRobot v3.0

```bash
# your_task / "你的任务描述" 都要换成自己的（--task 是进模型的 prompt）
$P dsh/raw_recorder.py \
  --format v30 \
  --v30-out ~/xhz/XLeRobot_wmf/LeRobot/data/serve/local/your_task \
  --repo-id local/your_task \
  --task "你的任务描述" \
  --overwrite
```

> ⚠️ **不要在 `\` 续行的中间写 `#` 注释**——它会把命令截断，后面的参数会被当成新命令执行
> （典型报错：`--overwrite：未找到命令`）。注释只能整行写在命令之前。

启动后终端只打印三行：

```
  ✅ 已连接 · 17 维 · 3 路相机 · seq=1934
  输出 → .../local/your_task  (v3.0 · 12 维 · 你的任务描述)
  o 开关 / d 丢弃 / p 状态 / q 退出
```

第三方库的日志、进度条、设备枚举默认都关闭；排查问题时加 `--verbose` 查看设备列表与数据集详情。

> 🔴 **`--v30-out` / `--repo-id` / `--task` 三处都要换成你自己的。**
> `your_task` 只是标识（目录名 / 数据集 id），`--task` 才是进模型的指令。
> 提醒规则同上一节：任务目标 + 对象、10 词以内、同任务同句。

| | `--format raw`（默认） | `--format v30` |
|---|---|---|
| 落盘 | `<session>/ep_*/` 的 npz + JPEG | 一个 v3.0 数据集目录 |
| 维度 | 17 维全存 | **12 维**（去头/底盘） |
| 诊断字段 | 无 | **不写**（避免被当成 STATE 拼进输入） |
| 视频 | 不编码 | 录制时直接 h264 编码 |
| 转换 | 需要 `convert_to_v21.py` | **不需要**，直接可训 |
| 采样率 | 记录真实时间戳，可重采样 | 数据集 fps 固定 |
| 退出 | 直接退出 | **必须正常 `q` 退出**（否则 parquet footer 写不出去） |

- 目录已存在且是数据集时会**自动续写**（追加 episode）
- 用了 `--overwrite` 且目录非数据集时会先清空
- 内部强制使用本仓库 `src/` 的 `LeRobotDataset`，不依赖其他 checkout
- v3.0 模式下丢掉相机 = 该段无法写帧，会直接停止并保存已录部分

---

## 三、启动转换器

```bash
# --task 必须和录制时用的是同一句
$P dsh/convert_to_v21.py \
  --raw dsh/out/raw \
  --out data/serve/local/your_task \
  --task "你的任务描述" \
  --overwrite
```

> 🔴 **`--task` 要和录制时**（`raw_recorder.py --task`）**完全一致**。
>
> 转换后的这句话会写进 `meta/tasks.jsonl`，训练时直接作为 prompt。
> 录制时写了什么就传什么，否则等于给这批数据换了指令。
> 如果录的时候没记下来，可以回头查：`cat dsh/out/raw/*/ep_*/meta.json | grep task`

| 参数 | 说明 |
|---|---|
| `--raw` | 原始层根目录（含 `<session>/ep_*/`） |
| `--out` | 输出的 v2.1 数据集目录（建议放 `data/serve/local/<任务名>`） |
| `--task` | **任务描述 → 训练 prompt**，必须与录制时一致，见上方提醒 |
| `--fps` | 默认 30 |
| `--crf` | h264 质量，默认 18（越小越清晰） |
| `--overwrite` | 覆盖已存在的输出目录 |

转换内容：17 维 → 12 维、重采样到严格 30Hz、JPEG 一次编码成 h264、写全套 v2.1 meta。

成功输出：

```
  [1/1] ep_000001: 350 帧 → 350 帧 | 11.63s | status=ok
写入完成：.../dsh/out/v21test（6.8s）
  ✅ 校验通过：1 集 / 350 帧 / 30fps
```

产物：

```
dsh/out/v21test/
├── meta/{info.json, tasks.jsonl, episodes.jsonl, episodes_stats.jsonl, stats.json}
├── data/chunk-000/episode_000000.parquet
└── videos/chunk-000/observation.images.{head,left_wrist,right_wrist}/episode_000000.mp4
```

---

## 四、录完先检查，再决定要不要转换

```bash
$P - <<'PY'
import glob, json, numpy as np
eps = sorted(glob.glob("dsh/out/raw/*/ep_*"))
if not eps:
    print("没有可用的 episode")
for ep in eps:
    m = json.load(open(f"{ep}/meta.json")); d = np.load(f"{ep}/samples.npz")
    span = d["state"].max(0) - d["state"].min(0)
    print(f"{ep}\n  {m['frame_count']} 帧 | {m['duration_s']:.1f}s | {m['nominal_fps']:.1f}fps | {m['status']}")
    print("  动过的关节:", {n: round(float(s), 1) for n, s in zip(m["state_names"], span) if s > 5})
PY
```

判断标准：

- `nominal_fps` 应 ≈ 30，明显低于 25 说明读取被拖慢
- **"动过的关节"至少有几个 > 10**，否则这段录的是静止画面，没有训练价值

---

## 五、常见问题

| 现象 | 原因 / 处理 |
|---|---|
| `No module named 'numpy'` | 用了 base 环境，改用 `$P` |
| `No such file or directory: .../examples/...` | 当前目录不对，先 `cd ~/xhz/XLeRobot_wmf/LeRobot` |
| `数据源 8765 不可用` | 终端 1 没起、没带 `--publish-state`，或超过 15 秒等待 |
| `相机缺失 ['right_wrist']` | 该相机从 USB 掉线；看 `ls /dev/v4l/by-path/`、`sudo dmesg -T \| tail -30` |
| 按 `o` 没有任何输出 | 终端 2 没有键盘焦点，先点一下窗口；或进程已退出 |
| `■ 已保存 ...（3 帧）` 被丢弃 | 低于 `--min-frames 15`，属于误触保护 |
| 遥操报 `Torque_Enable ... no status packet` | 舵机瞬时掉包；已内置 3 次重试，仍失败用 `$P dsh/diag_servos.py` 查 |
| 串口被占用 | 同一时刻只能一个进程持有 `ttyACM0/1`；`fuser -v /dev/ttyACM0` 查 |

---

## 六、已知问题（会影响数据可用性）

1. ~~**Xbox 夹爪几乎不动**~~ **已修（2026-10-30）**：原来扳机被当按钮，闭合目标硬编码成 `2`，
   而张开位是 `2.77`，行程只有 0.77%。现在改成扳机**模拟量**线性映射：
   **0 = 夹紧，100 = 张开**；当前两侧配置相同：

   | 侧 | 扳机松开 | 扳机按到底 | start_pose | 启动后 |
   |---|---|---|---|---|
   | 左 | **0（夹紧）** | 100（张开） | 2.77 | 闭合 |
   | 右 | **0（夹紧）** | 100（张开） | 0.58 | 闭合 |

   - 覆盖参数：`--left-gripper-open/closed`、`--right-gripper-open/closed`
   - 想改成“按下夹紧”，把同一侧两个值**对调**
   - 启动会打印两侧行程；行程 < 10 时打印 `[WARN]`
   - 启动会打印 `[MAIN] 夹爪行程：...`；行程 < 10 时打印 `[WARN]`
   - 已录的旧数据夹爪维度是常量，**不能用于抓取任务**
2. **十字键上下反向**：↑ 是后退（代码注释也写反了）。
3. **`shoulder_pan` 要按住摇杆帽才动**，不按就只有 XY 平面运动，横向覆盖会不足。
4. **USB 相机间歇性掉线**：三路相机轮流从总线上消失（`errno=19 No such device`），
   特征是"不同相机轮流掉"，指向供电/带宽而非单根线。
   当前代码已能扛住：
   - 遥操**不会崩**（发指令只看电机总线，见 `motors_connected`）
   - `--format raw`：继续录，`meta.json` 标记 `degraded: true` 与 `cameras_missing`
   - `--format v30`：**跳过**缺相机的那几帧并告警（时间轴会压缩），不会丢整段
   - `convert_to_v21.py`：自动跳过 `degraded` 段
   
   排查：`sudo dmesg -T | tail -80 | grep -iE "over-current|reset|disconnect"`，
   有 `over-current` 就换带独立供电的 USB hub，或把三路相机分到不同控制器。
5. **标定与数据根目录指向另一份 checkout**：`xlerobot_config.json` 里
   `calibration_dir` / `record_data_root` 都指向 `~/xhz/XLeRobot/...`。
   标定文件在那份仓库里，别删它。

---

## 七、整条链一览

```bash
# 终端 1：手柄遥操 + 状态源
PYTHONPATH=src $P examples/xlerobot/5_xlerobot_teleop_xbox.py --publish-state

# 终端 2：键盘录制（--task 每次按实际任务改写）
$P dsh/raw_recorder.py \
  --root ~/xhz/XLeRobot_wmf/LeRobot/dsh/out/raw \
  --task "你的任务描述"

# 事后：转换（不需要机器人，可反复跑；--task 与上面完全一致）
$P dsh/convert_to_v21.py \
  --raw dsh/out/raw \
  --out data/serve/local/your_task \
  --task "你的任务描述" \
  --overwrite
```

> 🔴 **两处 `--task` 必须写成同一句，并且每次都按你的实际任务改写。**
> 它最终会成为 `"Task: {这句话}, State: ...;\nAction: "` 里的指令，直接决定模型学到什么。
> 示例：`"pick up the red block and put it in the plate"`
