# To Run on the host
# '''powershell
# cd C:\Users\32389\Desktop\lerobot\lerobot
# $env:PYTHONPATH = "src"
# python -m lerobot.robots.xlerobot.xlerobot_host --robot-id my_xlerobot_pc
# '''

# To Run the teleop:
# '''powershell
# cd C:\Users\32389\Desktop\lerobot\lerobot
# $env:PYTHONPATH = "src"

# Local (robot plugged into this PC):
# python .\examples\xlerobot\4_xlerobot_teleop_keyboard.py --mode local

# Remote (robot host on another PC):
# python .\examples\xlerobot\4_xlerobot_teleop_keyboard.py --mode client --remote-ip 192.168.1.123

# With recording (raw layer):
# python examples/xlerobot/4_xlerobot_teleop_keyboard.py --record --record-format raw \
#     --record-root dsh/out/raw --task-description "pick up the cube"
# With recording (LeRobot v3.0 dataset):
# python examples/xlerobot/4_xlerobot_teleop_keyboard.py --record --record-format v30 \
#     --record-out data/serve/local/pick_cube --task-description "pick up the cube"
# Old style (kept for reference):
# python .\examples\xlerobot\4_xlerobot_teleop_keyboard.py --mode client --remote-ip 192.168.1.123 --record --hf-repo-id myuser/my_dataset --task-description "Pick and place task"
# '''

import signal
import time
import argparse
import sys
from pathlib import Path
import numpy as np
import math

from lerobot.robots.xlerobot import XLerobotConfig, XLerobot
from lerobot.robots.xlerobot.config_xlerobot import XLerobotClientConfig
from lerobot.robots.xlerobot.xlerobot_client import XLerobotClient
from lerobot.utils.robot_utils import busy_wait
from lerobot.utils.visualization_utils import init_rerun, log_rerun_data
from lerobot.model.SO101Robot import SO101Kinematics
from lerobot.teleoperators.keyboard.teleop_keyboard import KeyboardTeleop, KeyboardTeleopConfig


# Keymaps (semantic action: key)
GRIPPER_MIN = 0.0      # 实测：0 = 夹紧
GRIPPER_MAX = 100.0    # 实测：100 = 张开


def release_serial_ports(robot) -> int:
    """清掉 Feetech SDK 里可能卡住的 is_using 标志（Ctrl-C 打断串口事务会置位）。"""
    cleared = 0
    for name in ("bus1", "bus2"):
        handler = getattr(getattr(robot, name, None), "port_handler", None)
        if handler is not None and getattr(handler, "is_using", False):
            handler.is_using = False
            cleared += 1
            print(f"[MAIN] Cleared stale port-busy flag on {name}")
    return cleared


def return_to_start_pose(robot, parts, duration_s: float = 2.0, fps: int = 30) -> None:
    """中止流程：把双臂和头逐帧送回起始姿态，底盘保持静止。"""
    print("[MAIN] Abort: returning to the start pose ...")
    release_serial_ports(robot)
    try:
        robot.stop_base()
    except Exception as e:
        release_serial_ports(robot)
        print(f"[MAIN] stop_base failed (ignored): {e}")

    for part in parts:
        part.target_positions = part.zero_pos.copy()

    deadline = time.monotonic() + max(0.2, duration_s)
    failed_writes = 0
    while time.monotonic() < deadline:
        action = {}
        for part in parts:
            action.update(part.p_control_action(robot))
        for name in getattr(robot, "base_motors", []) or []:
            action[f"{name}.vel"] = 0.0
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


LEFT_KEYMAP = {
    'shoulder_pan+': 'q', 'shoulder_pan-': 'e',
    'wrist_roll+': 'r', 'wrist_roll-': 'f',
    'gripper+': 't', 'gripper-': 'g',
    'x+': 'w', 'x-': 's', 'y+': 'a', 'y-': 'd',
    'pitch+': 'z', 'pitch-': 'x',
    'reset': 'c',
    # For head motors
    "head_motor_1+": "<", "head_motor_1-": ">",
    "head_motor_2+": ",", "head_motor_2-": ".",
    
    'triangle': 'y',  # Rectangle trajectory key
}
RIGHT_KEYMAP = {
    'shoulder_pan+': '7', 'shoulder_pan-': '9',
    'wrist_roll+': '/', 'wrist_roll-': '*',
    'gripper+': '+', 'gripper-': '-',
    'x+': '8', 'x-': '2', 'y+': '4', 'y-': '6',
    'pitch+': '1', 'pitch-': '3',
    'reset': '0',

    'triangle': 'Y',  # Rectangle trajectory key
}

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

# Head motor mapping
HEAD_MOTOR_MAP = {
    "head_motor_1": "head_motor_1",
    "head_motor_2": "head_motor_2",
}

class RectangularTrajectory:
    """
    Generates a rectangular trajectory on the x-y plane with sinusoidal velocity profiles.
    The rectangle is divided into 4 line segments, each with smooth acceleration/deceleration.
    """
    def __init__(self, width=0.06, height=0.06, segment_duration=0.91):
        """
        Initialize rectangular trajectory parameters.
        
        Args:
            width: Rectangle width in meters
            height: Rectangle height in meters  
            segment_duration: Time for each line segment in seconds
        """
        self.width = width
        self.height = height
        self.segment_duration = segment_duration
        self.total_duration = 4 * segment_duration
        
    def get_trajectory_point(self, current_x, current_y, t):
        """
        Get the target x, y position at time t for the rectangular trajectory.
        
        Args:
            current_x: Starting x position
            current_y: Starting y position
            t: Time since trajectory start (0 to total_duration)
            
        Returns:
            tuple: (target_x, target_y)
        """
        # Determine which segment we're in
        segment = int(t / self.segment_duration)
        segment_t = t % self.segment_duration
        
        # Normalize segment time (0 to 1)
        normalized_t = segment_t / self.segment_duration
        
        # Sinusoidal velocity profile: smooth acceleration and deceleration
        # s(t) = 0.5 * (1 - cos(π * t)) gives smooth 0 to 1 transition
        smooth_t = 0.5 * (1 - math.cos(math.pi * normalized_t))
        
        # Define rectangle corners relative to starting position
        corners = [
            (current_x, current_y),                           # Start (bottom-left)
            (current_x + self.width, current_y),              # Bottom-right
            (current_x + self.width, current_y + self.height), # Top-right  
            (current_x, current_y + self.height),             # Top-left
            (current_x, current_y)                            # Back to start
        ]
        
        # Clamp segment to valid range
        segment = max(0, min(3, segment))
        
        # Interpolate between current corner and next corner
        start_corner = corners[segment]
        end_corner = corners[segment + 1]
        
        target_x = start_corner[0] + smooth_t * (end_corner[0] - start_corner[0])
        target_y = start_corner[1] + smooth_t * (end_corner[1] - start_corner[1])
        
        return target_x, target_y

class SimpleHeadControl:
    def __init__(self, initial_obs, kp=0.81, degree_step=1.0):
        self.kp = kp
        self.degree_step = float(degree_step)
        # Initialize head motor positions
        self.target_positions = {
            "head_motor_1": initial_obs.get("head_motor_1.pos", 0.0),
            "head_motor_2": initial_obs.get("head_motor_2.pos", 0.0),
        }
        self.zero_pos = {"head_motor_1": 0.0, "head_motor_2": 0.0}

    def move_to_zero_position(self, robot):
        self.target_positions = self.zero_pos.copy()
        action = self.p_control_action(robot)
        robot.send_action(action)

    def handle_keys(self, key_state):
        if key_state.get('head_motor_1+'):
            self.target_positions["head_motor_1"] += self.degree_step
            print(f"[HEAD] head_motor_1: {self.target_positions['head_motor_1']}")
        if key_state.get('head_motor_1-'):
            self.target_positions["head_motor_1"] -= self.degree_step
            print(f"[HEAD] head_motor_1: {self.target_positions['head_motor_1']}")
        if key_state.get('head_motor_2+'):
            self.target_positions["head_motor_2"] += self.degree_step
            print(f"[HEAD] head_motor_2: {self.target_positions['head_motor_2']}")
        if key_state.get('head_motor_2-'):
            self.target_positions["head_motor_2"] -= self.degree_step
            print(f"[HEAD] head_motor_2: {self.target_positions['head_motor_2']}")

    def p_control_action(self, robot):
        obs = robot.get_observation()
        action = {}
        for motor in self.target_positions:
            current = obs.get(f"{HEAD_MOTOR_MAP[motor]}.pos", 0.0)
            error = self.target_positions[motor] - current
            control = self.kp * error
            action[f"{HEAD_MOTOR_MAP[motor]}.pos"] = current + control
        return action

class SimpleTeleopArm:
    def __init__(self, kinematics, joint_map, initial_obs, prefix="left", kp=0.81,
                 degree_step=3.0, xy_step=0.0081):
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
        # Set initial x/y to fixed values
        self.current_x = 0.1629
        self.current_y = 0.1131
        self.pitch = 0.0
        # Set the degree step and xy step
        self.degree_step = float(degree_step)
        self.xy_step = float(xy_step)
        # Set target positions to zero for P control
        self.target_positions = {
            "shoulder_pan": 0.0,
            "shoulder_lift": 0.0,
            "elbow_flex": 0.0,
            "wrist_flex": 0.0,
            "wrist_roll": 0.0,
            "gripper": 0.0,
        }
        self.zero_pos = {
            'shoulder_pan': 0.0,
            'shoulder_lift': 0.0,
            'elbow_flex': 0.0,
            'wrist_flex': 0.0,
            'wrist_roll': 0.0,
            'gripper': 0.0
        }
        
        # Rectangular trajectory instance
        self.rectangular_trajectory = RectangularTrajectory(
            width=0.06,          # 6cm wide rectangle
            height=0.06,         # 4cm tall rectangle  
            segment_duration=1.01 # 3 seconds per line segment
        )

    def move_to_zero_position(self, robot):
        print(f"[{self.prefix}] Moving to Zero Position: {self.zero_pos} ......")
        self.target_positions = self.zero_pos.copy()  # Use copy to avoid reference issues
        
        # Reset kinematic variables to their initial state
        self.current_x = 0.1629
        self.current_y = 0.1131
        self.pitch = 0.0
        
        # Don't let handle_keys recalculate wrist_flex - set it explicitly
        self.target_positions["wrist_flex"] = 0.0
        
        action = self.p_control_action(robot)
        robot.send_action(action)

    def execute_rectangular_trajectory(self, robot, fps=30):
        """
        Execute a blocking rectangular trajectory on the x-y plane.
        
        Args:
            robot: Robot instance to send actions to
            fps: Control loop frequency
        """
        print(f"[{self.prefix}] Starting rectangular trajectory...")
        print(f"[{self.prefix}] Rectangle: {self.rectangular_trajectory.width:.3f}m x {self.rectangular_trajectory.height:.3f}m")
        print(f"[{self.prefix}] Duration: {self.rectangular_trajectory.total_duration:.3f}s total")
        
        # Store starting position
        start_x = self.current_x
        start_y = self.current_y
        
        # Execute trajectory
        start_time = time.time()
        dt = 1.0 / fps
        
        while True:
            current_time = time.time()
            elapsed_time = current_time - start_time
            
            # Check if trajectory is complete
            if elapsed_time >= self.rectangular_trajectory.total_duration:
                print(f"[{self.prefix}] Rectangular trajectory completed!")
                break
                
            # Get target position from trajectory
            target_x, target_y = self.rectangular_trajectory.get_trajectory_point(
                start_x, start_y, elapsed_time
            )
            
            # Update current position
            self.current_x = target_x
            self.current_y = target_y
            
            # Calculate inverse kinematics
            try:
                joint2, joint3 = self.kinematics.inverse_kinematics(self.current_x, self.current_y)
                self.target_positions["shoulder_lift"] = joint2
                self.target_positions["elbow_flex"] = joint3
                
                # Update wrist_flex coupling
                self.target_positions["wrist_flex"] = (
                    -self.target_positions["shoulder_lift"]
                    -self.target_positions["elbow_flex"]
                    + self.pitch
                )
                
                # Get action
                action = self.p_control_action(robot)
                
                # Determine which arm is executing and send appropriate action structure
                if self.prefix == "left":
                    # Send left arm action with empty actions for other components
                    robot_action = {**action, **{}, **{}, **{}}
                elif self.prefix == "right":
                    # Send right arm action with empty actions for other components
                    robot_action = {**{}, **action, **{}, **{}}
                
                # Send action to robot
                robot.send_action(robot_action)
                
                # Get observation and log data
                obs = robot.get_observation()
                log_rerun_data(obs, robot_action)
                
            except Exception as e:
                print(f"[{self.prefix}] IK failed at x={self.current_x:.4f}, y={self.current_y:.4f}: {e}")
                break
                
            # Maintain control frequency
            # busy_wait(dt)
        
        print(f"[{self.prefix}] Trajectory execution finished.")

    def handle_keys(self, key_state):
        # Joint increments
        if key_state.get('shoulder_pan+'):
            self.target_positions["shoulder_pan"] += self.degree_step
            print(f"[{self.prefix}] shoulder_pan: {self.target_positions['shoulder_pan']}")
        if key_state.get('shoulder_pan-'):
            self.target_positions["shoulder_pan"] -= self.degree_step
            print(f"[{self.prefix}] shoulder_pan: {self.target_positions['shoulder_pan']}")
        if key_state.get('wrist_roll+'):
            self.target_positions["wrist_roll"] += self.degree_step
            print(f"[{self.prefix}] wrist_roll: {self.target_positions['wrist_roll']}")
        if key_state.get('wrist_roll-'):
            self.target_positions["wrist_roll"] -= self.degree_step
            print(f"[{self.prefix}] wrist_roll: {self.target_positions['wrist_roll']}")
        # 夹爪：0 = 夹紧，100 = 张开（实测）；步进加减并夹在量程内
        if key_state.get('gripper+'):
            self.target_positions["gripper"] = min(
                GRIPPER_MAX, self.target_positions["gripper"] + self.degree_step)
            print(f"[{self.prefix}] gripper: {self.target_positions['gripper']:.0f}（张开方向）")
        if key_state.get('gripper-'):
            self.target_positions["gripper"] = max(
                GRIPPER_MIN, self.target_positions["gripper"] - self.degree_step)
            print(f"[{self.prefix}] gripper: {self.target_positions['gripper']:.0f}（夹紧方向）")
        if key_state.get('pitch+'):
            self.pitch += self.degree_step
            print(f"[{self.prefix}] pitch: {self.pitch}")
        if key_state.get('pitch-'):
            self.pitch -= self.degree_step
            print(f"[{self.prefix}] pitch: {self.pitch}")

        # XY plane (IK)
        moved = False
        if key_state.get('x+'):
            self.current_x += self.xy_step
            moved = True
            print(f"[{self.prefix}] x+: {self.current_x:.4f}, y: {self.current_y:.4f}")
        if key_state.get('x-'):
            self.current_x -= self.xy_step
            moved = True
            print(f"[{self.prefix}] x-: {self.current_x:.4f}, y: {self.current_y:.4f}")
        if key_state.get('y+'):
            self.current_y += self.xy_step
            moved = True
            print(f"[{self.prefix}] x: {self.current_x:.4f}, y+: {self.current_y:.4f}")
        if key_state.get('y-'):
            self.current_y -= self.xy_step
            moved = True
            print(f"[{self.prefix}] x: {self.current_x:.4f}, y-: {self.current_y:.4f}")
        if moved:
            joint2, joint3 = self.kinematics.inverse_kinematics(self.current_x, self.current_y)
            self.target_positions["shoulder_lift"] = joint2
            self.target_positions["elbow_flex"] = joint3
            print(f"[{self.prefix}] shoulder_lift: {joint2}, elbow_flex: {joint3}")

        # Wrist flex is always coupled to pitch and the other two
        self.target_positions["wrist_flex"] = (
            -self.target_positions["shoulder_lift"]
            -self.target_positions["elbow_flex"]
            + self.pitch
        )
        # print(f"[{self.prefix}] wrist_flex: {self.target_positions['wrist_flex']}")

    def p_control_action(self, robot):
        obs = robot.get_observation()
        current = {j: obs[f"{self.prefix}_arm_{j}.pos"] for j in self.joint_map}
        action = {}
        for j in self.target_positions:
            error = self.target_positions[j] - current[j]
            control = self.kp * error
            action[f"{self.joint_map[j]}.pos"] = current[j] + control
        return action
    

def main():
    parser = argparse.ArgumentParser(description="XLerobot keyboard teleop (local or remote via ZMQ).")
    parser.add_argument("--mode", choices=["local", "client"], default="local")
    parser.add_argument(
        "--remote-ip",
        default="localhost",
        help="Host IP when --mode client (e.g. 192.168.1.123).",
    )
    parser.add_argument("--port-zmq-cmd", type=int, default=5555)
    parser.add_argument("--port-zmq-observations", type=int, default=5556)
    parser.add_argument("--robot-id", default="my_xlerobot_pc")
    parser.add_argument("--fps", type=int, default=50)
    # Add recording options
    parser.add_argument("--record", action="store_true", help="Enable dataset recording")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="If the dataset directory already exists, delete it and recreate without prompting.",
    )
    parser.add_argument("--task-description", default="My task description",
                        help="任务描述，写进数据集并作为训练 prompt")
    parser.add_argument("--record-format", choices=("raw", "v30"), default="raw",
                        help="raw = npz+JPEG 母版（需再转换）；v30 = 直接写 LeRobot v3.0 数据集")
    parser.add_argument("--record-root", default="dsh/out/raw",
                        help="raw 模式的输出根目录")
    parser.add_argument("--record-out", default=None,
                        help="v30 模式的数据集目录（如 data/serve/local/pick_cube）")
    parser.add_argument("--record-repo-id", default=None,
                        help="v30 模式的 repo_id（默认 local/<目录名>）")
    parser.add_argument("--degree-step", type=float, default=3.0,
                        help="每帧关节增量（度）；键盘默认 3，觉得太快就调小")
    parser.add_argument("--xy-step", type=float, default=0.0081,
                        help="每帧平面增量（米）；默认 0.0081")
    parser.add_argument("--head-degree-step", type=float, default=1.0,
                        help="每帧头部增量（度）")
    parser.add_argument("--kp", type=float, default=0.81,
                        help="跟随增益：越大越跟手，越小越柔")
    parser.add_argument("--episode-time-sec", type=int, default=0,
                        help="自动分段间隔（秒）；0 = 不自动切，用空格手动控制。"
                             "注意：>0 时到点会保存当前段并立即开始下一段，不会停止录制")
    parser.add_argument("--publish-state", action="store_true",
                        help="只读状态发布到 127.0.0.1（供 dsh/raw_recorder.py 采集），仅 local 模式")
    parser.add_argument("--state-port", type=int, help="覆盖 record_state_port")
    parser.add_argument("--state-fps", type=int, help="覆盖 record_state_fps")
    args = parser.parse_args()

    # Teleop parameters
    FPS = args.fps

    if args.mode == "client":
        robot_config = XLerobotClientConfig(
            remote_ip=args.remote_ip,
            port_zmq_cmd=args.port_zmq_cmd,
            port_zmq_observations=args.port_zmq_observations,
            id=args.robot_id,
        )
        robot = XLerobotClient(robot_config)
    else:
        robot_config = XLerobotConfig(id=args.robot_id)
        robot = XLerobot(robot_config)
    
    try:
        robot.connect()
        print(f"[MAIN] Successfully connected to robot")
    except Exception as e:
        print(f"[MAIN] Failed to connect to robot: {e}")
        print(robot_config)
        print(robot)
        return
        
    if args.publish_state:
        if args.mode != "local":
            print("[MAIN] --publish-state 只支持 local 模式（client 模式走 ZMQ），已忽略")
        else:
            from lerobot.robots.xlerobot.state_publisher import RobotStateStream

            try:
                robot = RobotStateStream(
                    robot,
                    port=args.state_port if args.state_port is not None else robot_config.record_state_port,
                    fps=args.state_fps if args.state_fps is not None else robot_config.record_state_fps,
                    max_observation_age_s=robot_config.record_state_max_observation_age_s,
                ).start()
                print(f"[MAIN] Read-only state stream: http://127.0.0.1:{robot.port}  ({robot.fps} Hz)")
            except Exception as e:
                print(f"[MAIN] Failed to start state publisher: {e}")
                robot.disconnect()
                return

    init_rerun(session_name="xlerobot_teleop_v2")

    #Init the keyboard instance
    keyboard_config = KeyboardTeleopConfig()
    keyboard = KeyboardTeleop(keyboard_config)
    keyboard.connect()

    # Init the arm and head instances
    obs = robot.get_observation()
    kin_left = SO101Kinematics()
    kin_right = SO101Kinematics()
    left_arm = SimpleTeleopArm(kin_left, LEFT_JOINT_MAP, obs, prefix="left",
                               kp=args.kp, degree_step=args.degree_step, xy_step=args.xy_step)
    right_arm = SimpleTeleopArm(kin_right, RIGHT_JOINT_MAP, obs, prefix="right",
                                kp=args.kp, degree_step=args.degree_step, xy_step=args.xy_step)
    head_control = SimpleHeadControl(obs, kp=args.kp, degree_step=args.head_degree_step)

    # Move both arms and head to zero position at start
    left_arm.move_to_zero_position(robot)
    right_arm.move_to_zero_position(robot)

    # 录制：复用 dsh/raw_recorder.py 的写入器（raw 或 v3.0），不再另建一套数据集
    recorder = None
    record_source = None
    segment_start = 0.0
    if args.record:
        if args.record_format == "v30" and not args.record_out:
            raise SystemExit("--record-format v30 需要同时给 --record-out <数据集目录>")
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "dsh"))
        from raw_recorder import DirectSource, Recorder

        record_source = DirectSource(robot)
        rec_args = argparse.Namespace(
            source="direct",
            format=args.record_format,
            root=args.record_root,
            v30_out=args.record_out,
            repo_id=args.record_repo_id,
            task=args.task_description,
            fps=FPS,
            max_state_age_s=0.5,
            min_frames=15,
            cooldown_s=0.0,
            overwrite=args.overwrite,
            verbose=False,
            vcodec="h264",
        )
        recorder = Recorder(rec_args, record_source)
        target = recorder.v30_root if args.record_format == "v30" else recorder.session
        print(f"[MAIN] 录制输出（{args.record_format}）：{target}")
        print("[MAIN] 空格 = 开始/结束一段，Backspace = 丢弃当前段")
        recorder.toggle()
        segment_start = time.monotonic()

    # Ctrl-C 只置标志：直接抛异常可能打断串口事务，让 SDK 的 is_using 卡住
    stop_requested = {"flag": False}

    def _on_sigint(_signum, _frame):
        if not stop_requested["flag"]:
            print("\n[MAIN] Ctrl-C received: returning to start pose, then exiting.")
        stop_requested["flag"] = True

    signal.signal(signal.SIGINT, _on_sigint)

    aborted = False
    prev_keys: set[str] = set()
    try:
        while True:
            if stop_requested["flag"]:
                aborted = True
                break
            pressed_keys = set(keyboard.get_action().keys())

            # 中止键（默认 b，来自 config.teleop_keys["quit"]）：先复位再退出
            quit_key = (getattr(robot_config, "teleop_keys", None) or {}).get("quit", "b")
            if quit_key in pressed_keys:
                print(f"[MAIN] Quit key '{quit_key}' pressed: returning to start pose, then exiting.")
                aborted = True
                break

            left_key_state = {action: (key in pressed_keys) for action, key in LEFT_KEYMAP.items()}
            right_key_state = {action: (key in pressed_keys) for action, key in RIGHT_KEYMAP.items()}

            # Handle rectangular trajectory for left arm (y key)
            if left_key_state.get('triangle'):
                print("[MAIN] Left arm rectangular trajectory triggered!")
                left_arm.execute_rectangular_trajectory(robot, fps=FPS)
                continue

            # Handle rectangular trajectory for right arm (Y key)  
            if right_key_state.get('triangle'):
                print("[MAIN] Right arm rectangular trajectory triggered!")
                right_arm.execute_rectangular_trajectory(robot, fps=FPS)
                continue

            # Handle reset for left arm
            if left_key_state.get('reset'):
                left_arm.move_to_zero_position(robot)
                continue  

            # Handle reset for right arm
            if right_key_state.get('reset'):
                right_arm.move_to_zero_position(robot)
                continue

            # Handle reset for head motors with '?'
            if '?' in pressed_keys:
                head_control.move_to_zero_position(robot)
                continue

            left_arm.handle_keys(left_key_state)
            right_arm.handle_keys(right_key_state)
            head_control.handle_keys(left_key_state)  # Head controlled by left arm keymap

            left_action = left_arm.p_control_action(robot)
            right_action = right_arm.p_control_action(robot)
            head_action = head_control.p_control_action(robot)

            # Base action
            keyboard_keys = np.array(list(pressed_keys))
            base_action = robot._from_keyboard_to_base_action(keyboard_keys) or {}

            action = {**left_action, **right_action, **head_action, **base_action}
            robot.send_action(action)

            obs = robot.get_observation()
            # print(f"[MAIN] Observation: {obs}")
            log_rerun_data(obs, action)

            # 录制：把这一帧交给内嵌录制器
            if recorder is not None:
                new_keys = pressed_keys - prev_keys
                if "space" in new_keys:
                    print("[MAIN] 录制开关")
                    recorder.toggle()
                    segment_start = time.monotonic()
                if "backspace" in new_keys:
                    print("[MAIN] 丢弃当前段")
                    recorder.discard()
                    segment_start = time.monotonic()
                prev_keys = set(pressed_keys)

                record_source.update(obs, action)
                recorder.tick()

                if (args.episode_time_sec > 0 and recorder.writer is not None
                        and time.monotonic() - segment_start >= args.episode_time_sec):
                    print("[MAIN] 到达分段时长，保存并开始下一段")
                    recorder.rotate()
                    segment_start = time.monotonic()

            # busy_wait(1.0 / FPS)
    finally:
        if aborted:
            try:
                return_to_start_pose(robot, [left_arm, right_arm, head_control], fps=FPS)
            except Exception as e:
                print(f"[MAIN] Reset before exit failed: {e}")
        release_serial_ports(robot)
        if recorder is not None:
            if recorder.writer is not None:
                recorder._close(save=True)
            recorder.close()
        robot.disconnect()
        keyboard.disconnect()
        print("Teleoperation ended.")

if __name__ == "__main__":
    main()
