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

> 键盘脚本自带 `--record` 参数，会自己建数据集（旧的那套流程）。
> **推荐仍然用 `dsh/raw_recorder.py` 采集**：统一格式、能续写、能标记缺相机。

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
