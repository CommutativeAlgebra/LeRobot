# LeRobot for XLeRobot project

> Customized for XLeRobot project on Linux (Ubuntu).

## 代码下载与环境配置

### LeRobot

```bash
conda create -y -n lerobot python=3.10
conda activate lerobot
conda install ffmpeg=7.1.1 -c conda-forge -y
git clone https://github.com/fduTristin/LeRobot.git
cd LeRobot
pip install -e ".[feetech]"
```

### 手柄依赖（pygame，Xbox 遥操需要）

```bash
conda activate lerobot    # 之后默认所有代码都在 lerobot 环境中运行
cd LeRobot
pip install pygame
```

## 使用 Xbox 手柄遥操作

### 连接手柄

Xbox Series X|S、Xbox One S（Model 1708 及之后）以及各类有线授权手柄（XInput 协议）均可。
**推荐直接用 USB 数据线有线连接**：Linux 内核自带 `xpad` 驱动，即插即用、无需配对。
插上后出现 `/dev/input/js0` 即识别成功：

```bash
ls /dev/input/js*
```

> 不建议走蓝牙或官方无线适配器：蓝牙需额外安装 `xpadneo` 驱动且稳定性一般；
> 官方适配器需第三方 `xone` 驱动（要求关闭 Secure Boot），都不如一根线省心。

### 测试连接

```bash
python examples/xlerobot/xbox_test_read_CN.py
```

成功示例（Xbox Series X 手柄有线连接）：

```text
[OK] 已连接手柄: Xbox Series X Controller
     axes=6  buttons=12  hats=1

输入说明（字面与遥操 keymap 一致）:
  左/右摇杆: 手臂 XY 平面移动 | 摇杆按下(L3/R3)+左右推: shoulder_pan
  按住 LB/RB + 摇杆: pitch / wrist_roll | LT/RT: 夹爪
  头部保持启动姿态 | 十字键: 底盘 | Back: 恢复启动姿态
按 Ctrl+C 停止

LS=(+0.02,+0.02)  RS=(+0.00,-0.05)  LT=-1.00  RT=-1.00  十字键=- 按键=-
```

测试脚本实时刷新手柄状态：推动摇杆/扳机/十字键或按下按钮，对应数值会即时变化
（扳机松开约为 -1.0、按到底约为 +1.0，数值后带 `*` 表示超过遥操阈值 0.5）。

> 标准 XInput 布局为 6 个轴 / 11 个按键 / 1 个 hat（十字键，SDL 会把内核的 dpad 轴
> 转换成 hat）。Series X|S 手柄多一个 Share 键，显示 `buttons=12` 属正常。
> `5_xlerobot_teleop_xbox.py` 中 `get_xbox_key_state` 的按键/轴索引按此布局硬编码；
> 若脚本启动时提示 ⚠️ 布局异常（如第三方手柄键位不同），需相应修改该函数。

### 相机检测

多路 USB 相机同时出流前，先运行 `detect_cameras.py` 确认设备索引与分辨率：

```bash
python examples/xlerobot/detect_cameras.py --probe-resolutions
python examples/xlerobot/detect_cameras.py --live --live-width 640 --live-height-cap 480
```

相机配置的两点经验：

- **索引漂移**：三路相机型号相同，`/dev/videoN` 编号会随插拔/启动顺序漂移
  （本机就发生过 `video0`→`video1`)。推荐填稳定的 by-path 路径
  （`ls -l /dev/v4l/by-path/` 查看，按物理 USB 口固定）,
  本机配置即采用此方式。
- **USB 带宽**：三路 640x480@30 非压缩（YUYV）流会打满 USB 2.0 总线导致读帧超时，
  务必设置 `"cam_fourcc": "MJPG"`（摄像头硬压缩，带宽约降低一个数量级）,
  必要时还可把 `cam_async_read_timeout_ms` 放宽到 1000。

本机相关的相机索引/串口等配置写在 `src/lerobot/robots/xlerobot/xlerobot_config.json`
（已被 gitignore，模板见同目录 `xlerobot_config.json.example`），无需再改代码里的配置文件。

> 注意 `id` 字段必须填写且与校准文件名一致：校准文件按
> `{calibration_dir}/{id}.json` 查找（如 `id: "my_xlerobot"` 对应
> `data/calibration/my_xlerobot.json`）。不填会去找 `None.json`，
> 启动时就会反复提示重新校准。

### 查找电机端口

机器人有两路电机总线（左右机械臂各一块 USB 转串口板），对应配置里的 `port1` / `port2`。
用 lerobot 自带的 `lerobot-find-port` 交互式命令识别：每次拔掉**其中一块**板子的 USB，按提示回车，脚本会比对拔线前后的 `/dev/tty*` 列表找出端口：

```bash
lerobot-find-port
# 按提示拔掉一路电机板 USB -> 回车 ->
# 输出 "The port of this MotorsBus is '/dev/ttyACM0'" -> 重新插回
# 对另一路重复一次，得到两个端口
```

把识别结果写进 `src/lerobot/robots/xlerobot/xlerobot_config.json`：

```json
{
  "port1": "/dev/ttyACM0",
  "port2": "/dev/ttyACM1"
}
```

> 若 USB 重新插拔后 `/dev/ttyACMx` 编号会漂移（如两个同类转串口板同时在线），
> 可用 `ls -l /dev/serial/by-id/` 查看稳定的按设备 ID 路径并直接填入 port 字段，
> 例如 `/dev/serial/by-id/usb-1a86_USB_Single_Serial_xxx-if00`（本机配置即已采用此方式）。

**权限问题**：Linux 下串口设备默认属 `root:dialout`，如果连接时报
`Permission denied`，说明当前用户不在 `dialout` 组，执行（二选一）：

```bash
# 方案一（常规）：加入 dialout 组，需注销重新登录后生效
sudo usermod -aG dialout $USER

# 方案二（免重登）：给这类转串口板加 udev 规则，立即生效
echo 'KERNEL=="ttyACM*", ATTRS{idVendor}=="1a86", MODE="0666"' | sudo tee /etc/udev/rules.d/99-xlerobot-motor.rules
sudo udevadm control --reload-rules && sudo udevadm trigger
```

### 遥操

直接运行 Xbox 遥操脚本（双臂 + 固定头部 + 底盘）。该脚本会直接连接两路电机串口，
因此不要同时启动 `xlerobot_host`：

```bash
PYTHONPATH=src python examples/xlerobot/5_xlerobot_teleop_xbox.py
```

控制方式（语义映射见脚本内的 keymap）：

- 左/右摇杆：左/右臂 XY 平面移动（IK 解算）
- 左/右摇杆按下 + 左右推：左/右臂 shoulder_pan
- 按住 LB/RB + 摇杆：左/右臂 pitch（上下）、wrist_roll（左右）
- 左/右扳机：对应夹爪闭合，松开自动张开
- 头部：启动后保持 `xlerobot_config.json` 中的 `start_pose`，不接受手柄控制
- 十字键：底盘前进/后退/左转/右转；单按 LB/RB：底盘降速/升速档位
- Back：双臂与头部恢复到配置的 `start_pose`

### 记录启动姿态

先支撑好机械臂和头部，再运行：

```bash
PYTHONPATH=src python examples/xlerobot/record_start_pose.py
```

脚本会停止底盘、释放左右机械臂和头部力矩，让操作者手动摆放关节；确认后将位置写入本机的
`src/lerobot/robots/xlerobot/xlerobot_config.json` 的 `start_pose` 字段。底盘轮子采用速度模式，
因此不记录绝对位置，启动时只将速度设为零。
