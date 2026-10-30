# To Run on the host
'''
PYTHONPATH=src python -m lerobot.robots.xlerobot.xlerobot_host --robot.id=my_xlerobot
'''

# To Run the teleop:
'''
PYTHONPATH=src python -m examples.xlerobot.teleoperate_XBOX
'''

import signal
import time

import numpy as np
import pygame

from lerobot.model.SO101Robot import SO101Kinematics
from lerobot.robots.xlerobot import XLerobotConfig, XLerobot, default_xlerobot_start_pose
from lerobot.utils.visualization_utils import init_rerun, log_rerun_data

# Keymaps (semantic action: controller mapping) - Intuitive human control
LEFT_KEYMAP = {
    # Left stick controls left arm XY (when not pressed)
    'x+': 'left_stick_up', 'x-': 'left_stick_down',
    'y+': 'left_stick_right', 'y-': 'left_stick_left',
    # Left stick pressed controls left arm shoulder_pan
    'shoulder_pan+': 'left_stick_pressed_right', 'shoulder_pan-': 'left_stick_pressed_left',
    # LB pressed controls left arm pitch and wrist_roll
    'pitch+': 'lb_up', 'pitch-': 'lb_down',
    'wrist_roll+': 'lb_right', 'wrist_roll-': 'lb_left',
    # Left trigger controls left gripper
    'gripper+': 'left_trigger',
}
RIGHT_KEYMAP = {
    # Right stick controls right arm XY (when not pressed)
    'x+': 'right_stick_up', 'x-': 'right_stick_down',
    'y+': 'right_stick_right', 'y-': 'right_stick_left',
    # Right stick pressed controls right arm shoulder_pan
    'shoulder_pan+': 'right_stick_pressed_right', 'shoulder_pan-': 'right_stick_pressed_left',
    # RB pressed controls right arm pitch and wrist_roll
    'pitch+': 'rb_up', 'pitch-': 'rb_down',
    'wrist_roll+': 'rb_right', 'wrist_roll-': 'rb_left',
    # Right trigger controls right gripper
    'gripper+': 'right_trigger',
}

# Base control keymap - Only forward/backward and rotate left/right
BASE_KEYMAP = {
    'forward': 'dpad_down', 'backward': 'dpad_up',
    'rotate_left': 'dpad_left', 'rotate_right': 'dpad_right',
}

# Global reset key for all components
RESET_KEY = 'back'
ABORT_BUTTON = 7          # Start：中止（先复位再退出）

LEFT_JOINT_MAP = {
    "shoulder_pan": "left_arm_shoulder_pan",
    "shoulder_lift": "left_arm_shoulder_lift",
    "elbow_flex": "left_arm_elbow_flex",
    "wrist_flex": "left_arm_wrist_flex",
    "wrist_roll": "left_arm_wrist_roll",
    "gripper": "left_arm_gripper",
}
RIGHT_JOINT_MAP = {
    "shoulder_pan": "right_arm_shoulder_pan",
    "shoulder_lift": "right_arm_shoulder_lift",
    "elbow_flex": "right_arm_elbow_flex",
    "wrist_flex": "right_arm_wrist_flex",
    "wrist_roll": "right_arm_wrist_roll",
    "gripper": "right_arm_gripper",
}

HEAD_MOTOR_MAP = {
    "head_motor_1": "head_motor_1",
    "head_motor_2": "head_motor_2",
}

class SimpleHeadControl:
    def __init__(self, initial_obs, kp=1, start_pose=None):
        self.kp = kp
        configured_pose = start_pose or default_xlerobot_start_pose()
        self.zero_pos = {motor: configured_pose[motor] for motor in HEAD_MOTOR_MAP}
        self.target_positions = self.zero_pos.copy()

    def move_to_zero_position(self, robot, obs=None):
        print(f"[HEAD] Moving to fixed position: {self.zero_pos} ......")
        self.target_positions = self.zero_pos.copy()
        if obs is None:
            obs = robot.get_observation()
        action = self.p_control_action(obs)
        robot.send_action(action)

    def p_control_action(self, obs):
        action = {}
        for motor in self.target_positions:
            current = obs.get(f"{HEAD_MOTOR_MAP[motor]}.pos", 0.0)
            error = self.target_positions[motor] - current
            control = self.kp * error
            action[f"{HEAD_MOTOR_MAP[motor]}.pos"] = current + control
        return action

class SimpleTeleopArm:
    def __init__(self, kinematics, joint_map, initial_obs, prefix="left", kp=1, start_pose=None,
                 gripper_open=100.0, gripper_closed=0.0, gripper_deadzone=0.05,
                 degree_step=2.0, xy_step=0.005):
        self.kinematics = kinematics
        self.joint_map = joint_map
        self.prefix = prefix  # To distinguish left and right arm
        self.kp = kp
        # Initial joint positions
        self.joint_positions = {
            "shoulder_pan": initial_obs[f"{prefix}_arm_shoulder_pan.pos"],
            "shoulder_lift": initial_obs[f"{prefix}_arm_shoulder_lift.pos"],
            "elbow_flex": initial_obs[f"{prefix}_arm_elbow_flex.pos"],
            "wrist_flex": initial_obs[f"{prefix}_arm_wrist_flex.pos"],
            "wrist_roll": initial_obs[f"{prefix}_arm_wrist_roll.pos"],
            "gripper": initial_obs[f"{prefix}_arm_gripper.pos"],
        }
        # Set the degree step and xy step
        self.degree_step = float(degree_step)
        self.xy_step = float(xy_step)
        configured_pose = start_pose or default_xlerobot_start_pose()
        self.gripper_open = float(gripper_open)
        self.gripper_closed = float(gripper_closed)
        self.gripper_deadzone = float(gripper_deadzone)
        self._logged_gripper = None
        self.zero_pos = {joint: configured_pose[motor] for joint, motor in joint_map.items()}
        self.target_positions = self.zero_pos.copy()
        self._reset_kinematic_state()

    def _reset_kinematic_state(self):
        self.current_x, self.current_y = self.kinematics.forward_kinematics(
            self.zero_pos["shoulder_lift"], self.zero_pos["elbow_flex"]
        )
        self.pitch = (
            self.zero_pos["wrist_flex"]
            + self.zero_pos["shoulder_lift"]
            + self.zero_pos["elbow_flex"]
        )

    def move_to_zero_position(self, robot, obs=None):
        print(f"[{self.prefix}] Moving to configured start position: {self.zero_pos} ......")
        self.target_positions = self.zero_pos.copy()  # Use copy to avoid reference issues
        self._reset_kinematic_state()

        if obs is None:
            obs = robot.get_observation()
        action = self.p_control_action(obs)
        robot.send_action(action)

    def _log(self, msg: str) -> None:
        """连续动作按 5 Hz 打印，避免每个控制周期刷一行。"""
        now = time.monotonic()
        if now - getattr(self, "_last_log_t", 0.0) >= 0.2:
            self._last_log_t = now
            print(msg)

    def handle_keys(self, key_state):
        # 摇杆偏移量 → 步长缩放；键盘式数字输入没有 gain 时按 1.0 处理
        gx = key_state.get("gain_x", 1.0)
        gy = key_state.get("gain_y", 1.0)
        if gx == 0.0:
            gx = 1.0 if any(key_state.get(k) for k in
                            ('shoulder_pan+', 'shoulder_pan-', 'wrist_roll+', 'wrist_roll-')) else 0.0
        if gy == 0.0:
            gy = 1.0 if any(key_state.get(k) for k in ('pitch+', 'pitch-')) else 0.0

        # Joint increments
        if key_state.get('shoulder_pan+'):
            self.target_positions["shoulder_pan"] += self.degree_step * gx
            self._log(f"[{self.prefix}] shoulder_pan: {self.target_positions['shoulder_pan']:.1f}")
        if key_state.get('shoulder_pan-'):
            self.target_positions["shoulder_pan"] -= self.degree_step * gx
            self._log(f"[{self.prefix}] shoulder_pan: {self.target_positions['shoulder_pan']:.1f}")
        if key_state.get('wrist_roll+'):
            self.target_positions["wrist_roll"] += self.degree_step * gx
            self._log(f"[{self.prefix}] wrist_roll: {self.target_positions['wrist_roll']:.1f}")
        if key_state.get('wrist_roll-'):
            self.target_positions["wrist_roll"] -= self.degree_step * gx
            self._log(f"[{self.prefix}] wrist_roll: {self.target_positions['wrist_roll']:.1f}")
        
        # 夹爪：扳机模拟量 → 张开位(gripper_open) ~ 闭合位(gripper_closed) 的线性映射。
        # 左右方向可能相反，默认值在 CLI/main 里各自独立；如方向不对，
        # 把 --<side>-gripper-open/closed 两个值对调即可。
        open_pos = self.gripper_open
        analog = key_state.get("gripper_analog")
        if analog is None:                       # 手柄读不到扳机时退回"按着就夹"
            target = self.gripper_closed if key_state.get('gripper+') else open_pos
        else:
            dz = min(max(self.gripper_deadzone, 0.0), 0.9)
            v = 0.0 if analog <= dz else (analog - dz) / (1.0 - dz)
            target = open_pos + v * (self.gripper_closed - open_pos)
        self.target_positions["gripper"] = target
        if self._logged_gripper is None or abs(target - self._logged_gripper) >= 2.0:
            self._logged_gripper = target
            state = "CLOSED" if abs(target - self.gripper_closed) < 2.0 else f"{target:.0f}"
            print(f"[{self.prefix}] gripper: {state} (trigger {analog if analog is not None else 0:.2f})")
        
        if key_state.get('pitch+'):
            self.pitch += self.degree_step * gy
            self._log(f"[{self.prefix}] pitch: {self.pitch:.1f}")
        if key_state.get('pitch-'):
            self.pitch -= self.degree_step * gy
            self._log(f"[{self.prefix}] pitch: {self.pitch:.1f}")

        # XY plane (IK)：按摇杆偏移量缩放
        sx = self.xy_step * (gx if gx else 1.0)
        sy = self.xy_step * (gy if gy else 1.0)
        moved = False
        if key_state.get('x+'):
            self.current_x += sx
            moved = True
        if key_state.get('x-'):
            self.current_x -= sx
            moved = True
        if key_state.get('y+'):
            self.current_y += sy
            moved = True
        if key_state.get('y-'):
            self.current_y -= sy
            moved = True
        if moved:
            joint2, joint3 = self.kinematics.inverse_kinematics(self.current_x, self.current_y)
            self.target_positions["shoulder_lift"] = joint2
            self.target_positions["elbow_flex"] = joint3
            self._log(f"[{self.prefix}] xy: {self.current_x:.3f}, {self.current_y:.3f} "
                      f"(shoulder_lift {joint2:.1f}, elbow {joint3:.1f})")

        # Wrist flex is always coupled to pitch and the other two
        self.target_positions["wrist_flex"] = (
            -self.target_positions["shoulder_lift"]
            -self.target_positions["elbow_flex"]
            + self.pitch
        )
        # print(f"[{self.prefix}] wrist_flex: {self.target_positions['wrist_flex']}")

    def p_control_action(self, obs):
        current = {j: obs[f"{self.prefix}_arm_{j}.pos"] for j in self.joint_map}
        action = {}
        for j in self.target_positions:
            error = self.target_positions[j] - current[j]
            control = self.kp * error
            action[f"{self.joint_map[j]}.pos"] = current[j] + control
        return action
    

def axis_gain(axes, idx: int, deadzone: float = 0.1, expo: float = 0.0) -> float:
    """摇杆偏移量 → 0~1 的增益（死区外线性，可选 expo 曲线）。

    expo=0 线性；expo>0 时中间段更细腻（轻推慢、推到底仍到 1）。
    """
    if len(axes) <= idx:
        return 0.0
    v = abs(float(axes[idx]))
    if v <= deadzone:
        return 0.0
    v = min(1.0, (v - deadzone) / max(1e-6, 1.0 - deadzone))
    if expo > 0:
        v = v ** (1.0 + 2.0 * expo)
    return v


_TRIGGER_MIN: dict[int, float] = {}   # 每个轴见过的最小值，用来判断量程


def trigger_value(axes, idx: int) -> float:
    """把扳机轴归一化到 0~1。

    SDL 在不同平台把扳机报成 -1~1（松开 -1）或 0~1（松开 0），光看单帧分不清。
    这里记住每个轴出现过的最小值：一旦见过负值，就认定是 -1~1 量程。
    通常开机第一帧都是松开的，所以第一次调用就能判断对。
    """
    if len(axes) <= idx:
        return 0.0
    value = float(axes[idx])
    lo = _TRIGGER_MIN.get(idx)
    if lo is None or value < lo:
        lo = value
        _TRIGGER_MIN[idx] = lo
    if lo < -0.05:                      # 确认是 -1~1 量程
        value = (value + 1.0) / 2.0
    return max(0.0, min(1.0, value))


def release_serial_ports(robot) -> int:
    """清掉 Feetech SDK 里可能卡住的 is_using 标志。

    Ctrl-C 若正好打断一次 txRxPacket，SDK 的 PortHandler.is_using 会停在 True，
    之后所有读写都返回 COMM_PORT_BUSY（"Port is in use!"）。
    """
    cleared = 0
    for name in ("bus1", "bus2"):
        bus = getattr(robot, name, None)
        handler = getattr(bus, "port_handler", None)
        if handler is not None and getattr(handler, "is_using", False):
            handler.is_using = False
            cleared += 1
            print(f"[MAIN] Cleared stale port-busy flag on {name}")
    return cleared


def return_to_start_pose(robot, left_arm, right_arm, head_control,
                         duration_s: float = 2.0, fps: int = 30) -> None:
    """中止流程：把双臂和头平滑送回起始姿态，底盘保持静止。

    逐帧发 P 控制而不是一次性跳到目标，避免复位时甩臂；
    复位过程中再按 Ctrl-C 也会继续走完。
    """
    print("[MAIN] Abort: returning to the start pose ...")
    release_serial_ports(robot)
    try:
        robot.stop_base()
    except Exception as e:
        release_serial_ports(robot)
        print(f"[MAIN] stop_base failed (ignored): {e}")

    parts = [left_arm, right_arm, head_control]
    for part in parts:
        part.target_positions = part.zero_pos.copy()

    deadline = time.monotonic() + max(0.2, duration_s)
    failed_writes = 0
    while time.monotonic() < deadline:
        try:
            obs = robot.get_observation()
        except KeyboardInterrupt:
            continue                        # 复位期间不响应第二次 Ctrl-C
        action = {}
        for part in parts:
            action.update(part.p_control_action(obs))
        for name in getattr(robot, "base_motors", []) or []:
            action[f"{name}.vel"] = 0.0     # 底盘停住
        try:
            robot.send_action(action)
            failed_writes = 0
        except Exception as e:
            failed_writes += 1
            release_serial_ports(robot)
            if failed_writes == 1:
                print(f"[MAIN] Reset write failed ({e}); retrying after clearing port flag")
            if failed_writes >= 10:
                print(f"[MAIN] Reset aborted: {failed_writes} consecutive failed writes")
                break
        time.sleep(1.0 / fps)
    print("[MAIN] Start pose reached; exiting.")


# --- XBOX Controller Mapping ---
def get_xbox_key_state(joystick, keymap, axes_xy=(0, 1), stick_deadzone=0.1, stick_expo=0.0):
    """
    Map XBOX controller state to semantic action booleans using the provided keymap.
    """
    # Read axes, buttons, hats
    axes = [joystick.get_axis(i) for i in range(joystick.get_numaxes())]
    buttons = [joystick.get_button(i) for i in range(joystick.get_numbuttons())]
    hats = joystick.get_hat(0) if joystick.get_numhats() > 0 else (0, 0)
    
    # Get stick pressed states
    left_stick_pressed = bool(buttons[9]) if len(buttons) > 9 else False
    right_stick_pressed = bool(buttons[10]) if len(buttons) > 10 else False
    lb_pressed = bool(buttons[4]) if len(buttons) > 4 else False
    rb_pressed = bool(buttons[5]) if len(buttons) > 5 else False

    # 摇杆增益：0 = 回中，1 = 推到底；handle_keys 用它缩放每帧步长
    state = {
        "gain_x": axis_gain(axes, axes_xy[0], stick_deadzone, stick_expo),
        "gain_y": axis_gain(axes, axes_xy[1], stick_deadzone, stick_expo),
    }
    for action, control in keymap.items():
        if control == 'left_trigger':
            analog = trigger_value(axes, 2)
            state[action] = analog > 0.5
            state['gripper_analog'] = analog      # 连续量交给 handle_keys 做映射
        elif control == 'right_trigger':
            analog = trigger_value(axes, 5)
            state[action] = analog > 0.5
            state['gripper_analog'] = analog
        elif control == 'a':
            state[action] = bool(buttons[0])
        elif control == 'b':
            state[action] = bool(buttons[1])
        elif control == 'x':
            state[action] = bool(buttons[2])
        elif control == 'y':
            state[action] = bool(buttons[3])
        elif control == 'back':
            state[action] = bool(buttons[6])
        elif control == 'dpad_up':
            state[action] = hats[1] == 1
        elif control == 'dpad_down':
            state[action] = hats[1] == -1
        elif control == 'dpad_left':
            state[action] = hats[0] == -1
        elif control == 'dpad_right':
            state[action] = hats[0] == 1
        # Left stick controls (when not pressed)
        elif control == 'left_stick_up':
            state[action] = (not left_stick_pressed) and (not lb_pressed) and (axes[1] < -0.5) if len(axes) > 1 else False
        elif control == 'left_stick_down':
            state[action] = (not left_stick_pressed) and (not lb_pressed) and (axes[1] > 0.5) if len(axes) > 1 else False
        elif control == 'left_stick_left':
            state[action] = (not left_stick_pressed) and (not lb_pressed) and (axes[0] < -0.5) if len(axes) > 0 else False
        elif control == 'left_stick_right':
            state[action] = (not left_stick_pressed) and (not lb_pressed) and (axes[0] > 0.5) if len(axes) > 0 else False
        # Right stick controls (when not pressed) - Fixed axis mapping
        elif control == 'right_stick_up':
            state[action] = (not right_stick_pressed) and (not rb_pressed) and (axes[4] < -0.5) if len(axes) > 4 else False
        elif control == 'right_stick_down':
            state[action] = (not right_stick_pressed) and (not rb_pressed) and (axes[4] > 0.5) if len(axes) > 4 else False
        elif control == 'right_stick_left':
            state[action] = (not right_stick_pressed) and (not rb_pressed) and (axes[3] < -0.5) if len(axes) > 3 else False
        elif control == 'right_stick_right':
            state[action] = (not right_stick_pressed) and (not rb_pressed) and (axes[3] > 0.5) if len(axes) > 3 else False
        # Left stick pressed controls
        elif control == 'left_stick_pressed_right':
            state[action] = left_stick_pressed and (not lb_pressed) and (axes[0] > 0.5) if len(axes) > 0 else False
        elif control == 'left_stick_pressed_left':
            state[action] = left_stick_pressed and (not lb_pressed) and (axes[0] < -0.5) if len(axes) > 0 else False
        # Right stick pressed controls - Fixed axis mapping
        elif control == 'right_stick_pressed_right':
            state[action] = right_stick_pressed and (not rb_pressed) and (axes[3] > 0.5) if len(axes) > 3 else False
        elif control == 'right_stick_pressed_left':
            state[action] = right_stick_pressed and (not rb_pressed) and (axes[3] < -0.5) if len(axes) > 3 else False
        # LB pressed controls (only when stick is moved)
        elif control == 'lb_up':
            state[action] = lb_pressed and (abs(axes[1]) > 0.5) and (axes[1] < -0.5) if len(axes) > 1 else False
        elif control == 'lb_down':
            state[action] = lb_pressed and (abs(axes[1]) > 0.5) and (axes[1] > 0.5) if len(axes) > 1 else False
        elif control == 'lb_right':
            state[action] = lb_pressed and (abs(axes[0]) > 0.5) and (axes[0] > 0.5) if len(axes) > 0 else False
        elif control == 'lb_left':
            state[action] = lb_pressed and (abs(axes[0]) > 0.5) and (axes[0] < -0.5) if len(axes) > 0 else False
        # RB pressed controls (only when stick is moved)
        elif control == 'rb_up':
            state[action] = rb_pressed and (abs(axes[4]) > 0.5) and (axes[4] < -0.5) if len(axes) > 4 else False
        elif control == 'rb_down':
            state[action] = rb_pressed and (abs(axes[4]) > 0.5) and (axes[4] > 0.5) if len(axes) > 4 else False
        elif control == 'rb_right':
            state[action] = rb_pressed and (abs(axes[3]) > 0.5) and (axes[3] > 0.5) if len(axes) > 3 else False
        elif control == 'rb_left':
            state[action] = rb_pressed and (abs(axes[3]) > 0.5) and (axes[3] < -0.5) if len(axes) > 3 else False
        else:
            state[action] = False
    return state

def get_base_action(joystick, robot):
    """
    Get base action from XBOX controller input - simplified to only forward/backward and rotate.
    """
    # Read controller state
    buttons = [joystick.get_button(i) for i in range(joystick.get_numbuttons())]
    hats = joystick.get_hat(0) if joystick.get_numhats() > 0 else (0, 0)
    
    # Get pressed keys for base control
    pressed_keys = set()
    
    # Map controller inputs to keyboard-like keys for base control
    if hats[1] == 1:   # D-pad up
        pressed_keys.add('k')  # Forward
    if hats[1] == -1:  # D-pad down
        pressed_keys.add('i')  # Backward
    if hats[0] == -1:  # D-pad left
        pressed_keys.add('u')  # Rotate left
    if hats[0] == 1:   # D-pad right
        pressed_keys.add('o')  # Rotate right
    
    # Convert to numpy array and get base action
    keyboard_keys = np.array(list(pressed_keys))
    base_action = robot._from_keyboard_to_base_action(keyboard_keys) or {}
    
    return base_action

def get_base_speed_control(joystick):
    """
    Get base speed control from XBOX controller - LB for speed decrease, RB for speed increase.
    Returns speed multiplier (1.0, 2.0, or 3.0) and prints current speed level.
    """
    # Read controller state
    buttons = [joystick.get_button(i) for i in range(joystick.get_numbuttons())]
    
    # Get LB and RB states
    lb_pressed = bool(buttons[4]) if len(buttons) > 4 else False
    rb_pressed = bool(buttons[5]) if len(buttons) > 5 else False
    
    # Get current speed level from global variable
    global current_base_speed_level
    if 'current_base_speed_level' not in globals():
        current_base_speed_level = 1  # Default speed level
    
    # Speed control logic
    if lb_pressed and not rb_pressed:
        # LB pressed alone - decrease speed
        if current_base_speed_level > 1:
            current_base_speed_level -= 1
            print(f"[BASE] Speed decreased to level {current_base_speed_level}")
    elif rb_pressed and not lb_pressed:
        # RB pressed alone - increase speed
        if current_base_speed_level < 3:
            current_base_speed_level += 1
            print(f"[BASE] Speed increased to level {current_base_speed_level}")
    
    # Map speed level to multiplier
    speed_multiplier = float(current_base_speed_level)
    
    return speed_multiplier


def main(*, publish_state: bool = False, state_port: int | None = None, state_fps: int | None = None,
         left_gripper_open: float = 0.0, left_gripper_closed: float = 100.0,
         right_gripper_open: float = 0.0, right_gripper_closed: float = 100.0,
         gripper_deadzone: float = 0.05,
         degree_step: float = 2.0, xy_step: float = 0.005, kp: float = 1.0,
         stick_deadzone: float = 0.1, stick_expo: float = 0.0):
    robot_config = XLerobotConfig()
    robot = XLerobot(robot_config)
    try:
        robot.connect()
        print(f"[MAIN] Successfully connected to robot")
    except Exception as e:
        print(f"[MAIN] Failed to connect to robot: {e}")
        print(robot_config)
        print(robot)
        return

    if publish_state:
        # Read-only state source for other local processes (e.g. dsh/raw_recorder.py).
        # Wrapped after connect() so the serial buses are already open.
        from lerobot.robots.xlerobot.state_publisher import RobotStateStream

        try:
            robot = RobotStateStream(
                robot,
                port=state_port if state_port is not None else robot_config.record_state_port,
                fps=state_fps if state_fps is not None else robot_config.record_state_fps,
                max_observation_age_s=robot_config.record_state_max_observation_age_s,
            ).start()
        except Exception as e:
            print(f"[MAIN] Failed to start state publisher: {e}")
            robot.disconnect()
            return
        print(f"[MAIN] Read-only state stream: http://127.0.0.1:{robot.port}  ({robot.fps} Hz)")

    init_rerun(session_name="xlerobot_teleop_xbox")

    # Init XBOX controller
    pygame.init()
    pygame.joystick.init()
    if pygame.joystick.get_count() == 0:
        print("No XBOX controller detected!")
        return
    joystick = pygame.joystick.Joystick(0)
    joystick.init()
    print(f"[MAIN] Using controller: {joystick.get_name()}")

    # Init the arm and head instances
    obs = robot.get_observation()
    kin_left = SO101Kinematics()
    kin_right = SO101Kinematics()
    left_arm = SimpleTeleopArm(
        kin_left, LEFT_JOINT_MAP, obs, prefix="left", start_pose=robot_config.start_pose,
        gripper_open=left_gripper_open, gripper_closed=left_gripper_closed,
        gripper_deadzone=gripper_deadzone,
        degree_step=degree_step, xy_step=xy_step, kp=kp,
    )
    right_arm = SimpleTeleopArm(
        kin_right, RIGHT_JOINT_MAP, obs, prefix="right", start_pose=robot_config.start_pose,
        gripper_open=right_gripper_open, gripper_closed=right_gripper_closed,
        gripper_deadzone=gripper_deadzone,
        degree_step=degree_step, xy_step=xy_step, kp=kp,
    )
    head_control = SimpleHeadControl(obs, start_pose=robot_config.start_pose)

    # 夹爪行程自检（左右方向可能相反，分别检查）
    for side, opn, cls in (("左", left_gripper_open, left_gripper_closed),
                           ("右", right_gripper_open, right_gripper_closed)):
        span = abs(cls - opn)
        if span < 10:
            print(f"[WARN] {side}夹爪行程只有 {span:.1f}（张开 {opn:.0f} → 闭合 {cls:.0f}），"
                  f"按扳机几乎看不到动作。检查 --left-gripper-open/closed 或 "
                  f"--right-gripper-open/closed。")
        else:
            print(f"[MAIN] {side}夹爪：扳机松开 = {opn:.0f}（张开），按到底 = {cls:.0f}（闭合）")

    # Move both arms and head to the configured start pose.
    left_arm.move_to_zero_position(robot, obs)
    right_arm.move_to_zero_position(robot, obs)
    head_control.move_to_zero_position(robot, obs)

    # Ctrl-C 只置标志：直接抛 KeyboardInterrupt 可能正好打断一次串口事务，
    # 让 SDK 的 is_using 卡住，之后复位写不进去。
    stop_requested = {"flag": False}

    def _on_sigint(_signum, _frame):
        if not stop_requested["flag"]:
            print("\n[MAIN] Ctrl-C received: returning to start pose, then exiting.")
        stop_requested["flag"] = True

    signal.signal(signal.SIGINT, _on_sigint)

    aborted = False
    try:
        while True:
            if stop_requested["flag"]:
                aborted = True
                break
            pygame.event.pump()
            left_key_state = get_xbox_key_state(
                joystick, LEFT_KEYMAP, axes_xy=(0, 1),
                stick_deadzone=stick_deadzone, stick_expo=stick_expo)
            right_key_state = get_xbox_key_state(
                joystick, RIGHT_KEYMAP, axes_xy=(3, 4),
                stick_deadzone=stick_deadzone, stick_expo=stick_expo)
            
            # Check for global reset (back button)
            buttons = [joystick.get_button(i) for i in range(joystick.get_numbuttons())]
            global_reset = bool(buttons[6]) if len(buttons) > 6 else False

            # Abort (Start button, or Ctrl-C): return to start pose, then exit
            if len(buttons) > ABORT_BUTTON and buttons[ABORT_BUTTON]:
                aborted = True
                break
            
            # Handle global reset for all components
            if global_reset:
                print("[MAIN] Restoring configured start pose!")
                obs = robot.get_observation()
                left_arm.move_to_zero_position(robot, obs)
                right_arm.move_to_zero_position(robot, obs)
                head_control.move_to_zero_position(robot, obs)
                continue

            # Handle both arms separately and simultaneously
            left_arm.handle_keys(left_key_state)
            right_arm.handle_keys(right_key_state)

            # One shared observation keeps arm control and visualization in sync while avoiding
            # repeated waits for fresh frames from every camera in the same control cycle.
            obs = robot.get_observation()
            left_action = left_arm.p_control_action(obs)
            right_action = right_arm.p_control_action(obs)
            head_action = head_control.p_control_action(obs)

            # Get base action and speed control from controller
            base_action = get_base_action(joystick, robot)
            speed_multiplier = get_base_speed_control(joystick)
            
            # Apply speed multiplier to base actions if they exist
            if base_action:
                for key in base_action:
                    if 'vel' in key or 'velocity' in key:  # Apply to velocity commands
                        base_action[key] *= speed_multiplier

            # Merge all actions
            action = {**left_action, **right_action, **head_action, **base_action}
            robot.send_action(action)

            log_rerun_data(obs, action)
    except KeyboardInterrupt:
        print("[MAIN] Ctrl-C received: aborting.")
        aborted = True
    finally:
        if aborted:
            try:
                return_to_start_pose(robot, left_arm, right_arm, head_control)
            except Exception as e:
                print(f"[MAIN] Reset before exit failed: {e}")
        release_serial_ports(robot)     # 断开前再清一次，避免 disconnect 也被忙标志挡住
        robot.disconnect()
        print("Teleoperation ended.")

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Xbox teleoperation, optional read-only state publisher")
    parser.add_argument("--publish-state", action="store_true",
                        help="serve read-only state on 127.0.0.1 (for raw_recorder.py)")
    parser.add_argument("--state-port", type=int, help="override record_state_port")
    parser.add_argument("--state-fps", type=int, help="override record_state_fps")
    parser.add_argument("--left-gripper-open", type=float, default=0.0,
                        help="左夹爪张开位（当前设为 0，可对调试试）")
    parser.add_argument("--left-gripper-closed", type=float, default=100.0,
                        help="左夹爪闭合位（当前设为 100，可对调试试）")
    parser.add_argument("--right-gripper-open", type=float, default=0.0,
                        help="右夹爪张开位（本机与左相反：0 = 全开）")
    parser.add_argument("--right-gripper-closed", type=float, default=100.0,
                        help="右夹爪闭合位（本机 100 = 夹紧）")
    parser.add_argument("--gripper-deadzone", type=float, default=0.05,
                        help="扳机死区（0~1），避免松开时的噪声导致夹爪抖动")
    parser.add_argument("--degree-step", type=float, default=2.0,
                        help="每帧关节增量（度）；越大越快")
    parser.add_argument("--xy-step", type=float, default=0.005,
                        help="每帧平面增量（米）；越大越快")
    parser.add_argument("--kp", type=float, default=1.0,
                        help="跟随增益：1.0 = 直接打到目标，越小越柔")
    parser.add_argument("--stick-deadzone", type=float, default=0.1,
                        help="摇杆死区（0~1），低于此值视为回中")
    parser.add_argument("--stick-expo", type=float, default=0.0,
                        help="摇杆曲线：0 = 线性，越大中间越细腻")
    args = parser.parse_args()
    main(publish_state=args.publish_state, state_port=args.state_port, state_fps=args.state_fps,
         left_gripper_open=args.left_gripper_open, left_gripper_closed=args.left_gripper_closed,
         right_gripper_open=args.right_gripper_open, right_gripper_closed=args.right_gripper_closed,
         gripper_deadzone=args.gripper_deadzone)
