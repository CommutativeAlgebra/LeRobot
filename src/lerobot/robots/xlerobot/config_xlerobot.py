# Copyright 2024 The HuggingFace Inc. team. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import logging
from dataclasses import dataclass, field, fields
from pathlib import Path

from lerobot.cameras.configs import CameraConfig, Cv2Rotation
from lerobot.cameras.opencv.configuration_opencv import OpenCVCameraConfig

from ..config import RobotConfig

logger = logging.getLogger(__name__)

XLEROBOT_POSITION_MOTORS = (
    "left_arm_shoulder_pan",
    "left_arm_shoulder_lift",
    "left_arm_elbow_flex",
    "left_arm_wrist_flex",
    "left_arm_wrist_roll",
    "left_arm_gripper",
    "right_arm_shoulder_pan",
    "right_arm_shoulder_lift",
    "right_arm_elbow_flex",
    "right_arm_wrist_flex",
    "right_arm_wrist_roll",
    "right_arm_gripper",
    "head_motor_1",
    "head_motor_2",
)


def default_xlerobot_start_pose() -> dict[str, float]:
    """Return the default teleoperation start pose in calibrated motor coordinates."""
    pose = dict.fromkeys(XLEROBOT_POSITION_MOTORS, 0.0)
    pose["left_arm_gripper"] = 90.0
    pose["right_arm_gripper"] = 90.0
    pose["head_motor_1"] = 0.6055
    pose["head_motor_2"] = 41.2684
    return pose


def validate_xlerobot_start_pose(pose: dict[str, float]) -> dict[str, float]:
    if not isinstance(pose, dict):
        raise ValueError("XLerobotConfig.start_pose must be a JSON object.")

    expected = set(XLEROBOT_POSITION_MOTORS)
    missing = sorted(expected - set(pose))
    unknown = sorted(set(pose) - expected)
    if missing or unknown:
        raise ValueError(
            "XLerobotConfig.start_pose has invalid motor names: "
            f"missing={missing}, unknown={unknown}."
        )

    normalized = {}
    for motor in XLEROBOT_POSITION_MOTORS:
        value = pose[motor]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"XLerobotConfig.start_pose[{motor!r}] must be a number, got {value!r}.")
        normalized[motor] = float(value)
    return normalized


# Optional per-machine override file (gitignored). Copy xlerobot_config.json.example
# to xlerobot_config.json next to this module to override config fields without
# editing tracked source files.
XLEROBOT_LOCAL_CONFIG_PATH = Path(__file__).with_name("xlerobot_config.json")


def apply_xlerobot_local_overrides(cfg, path: Path = XLEROBOT_LOCAL_CONFIG_PATH) -> None:
    """Override dataclass fields of `cfg` with values from a local JSON file.

    Keys must match existing dataclass fields (`cameras` is not supported; use the
    `cam_*` fields instead). String values for Path-typed fields are converted
    (with `~` expansion). No-op if the file does not exist.
    """
    if not path.is_file():
        return
    try:
        overrides = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in {path}: {e}") from e
    if not isinstance(overrides, dict):
        raise ValueError(f"{path} must contain a JSON object of field overrides.")
    valid = {f.name for f in fields(cfg)} - {"cameras"}
    unknown = sorted(set(overrides) - valid)
    if unknown:
        raise ValueError(
            f"{path}: unknown field(s) {unknown}. Valid fields: {sorted(valid)} "
            f"('cameras' is built from the cam_* fields at runtime)."
        )
    for key, value in overrides.items():
        current = getattr(cfg, key)
        if isinstance(current, Path) and value is not None:
            value = Path(value).expanduser()
        elif key == "start_pose":
            if not isinstance(value, dict):
                raise ValueError(f"{path}: 'start_pose' must be a JSON object.")
            # Allow a local file to override only the joints that differ from defaults.
            value = {**current, **value}
        object.__setattr__(cfg, key, value)
    logger.info("Applied XLeRobot local overrides from %s: %s", path, sorted(overrides))


def make_xlerobot_opencv_cameras(
    left_index_or_path: str | int,
    right_index_or_path: str | int,
    head_index_or_path: str | int,
    *,
    fps: int = 30,
    width: int = 640,
    height: int = 480,
    fourcc: str | None = None,
    async_read_timeout_ms: float = 500.0,
) -> dict[str, CameraConfig]:
    """Three OpenCV cameras for XLerobot (left wrist, right wrist, head).

    `index_or_path` can be an integer device index (0, 1, 2) or a path string
    (e.g. ``/dev/video0`` on Linux or ``0`` on Windows as str).
    """
    return {
        "left_wrist": OpenCVCameraConfig(
            index_or_path=left_index_or_path,
            fps=fps,
            width=width,
            height=height,
            fourcc=fourcc,
            rotation=Cv2Rotation.NO_ROTATION,
            async_read_timeout_ms=async_read_timeout_ms,
        ),
        "right_wrist": OpenCVCameraConfig(
            index_or_path=right_index_or_path,
            fps=fps,
            width=width,
            height=height,
            fourcc=fourcc,
            rotation=Cv2Rotation.NO_ROTATION,
            async_read_timeout_ms=async_read_timeout_ms,
        ),
        "head": OpenCVCameraConfig(
            index_or_path=head_index_or_path,
            fps=fps,
            width=width,
            height=height,
            fourcc=fourcc,
            rotation=Cv2Rotation.NO_ROTATION,
            async_read_timeout_ms=async_read_timeout_ms,
        ),
    }


def xlerobot_cameras_config() -> dict[str, CameraConfig]:
    """Empty placeholder; use :class:`XLerobotConfig` camera port fields + ``__post_init__``."""
    return {}


@RobotConfig.register_subclass("xlerobot")
@dataclass
class XLerobotConfig(RobotConfig):

    port1: str = "COM4"  # port to connect to the bus (so101 + head camera)
    port2: str = "COM5"  # port to connect to the bus (same as lekiwi setup)
    calibration_dir: Path = Path(r"E:\FVL\EmbodiedAI\XLeRobot\LeRobot\data\calibration")
    
    disable_torque_on_disconnect: bool = True

    # `max_relative_target` limits the magnitude of the relative positional target vector for safety purposes.
    # Set this to a positive scalar to have the same value for all motors, or a list that is the same length as
    # the number of motors in your follower arms.
    max_relative_target: int | None = None

    # OpenCV camera device index or path (override per machine). Used when `cameras` is left empty.
    cam_left_wrist: str | int = 0
    cam_right_wrist: str | int = 2
    cam_head: str | int = 3
    cam_fps: int = 30
    cam_width: int = 640
    cam_height: int = 480
    # FOURCC pixel format. "MJPG" strongly advised when several USB cameras share one bus
    # (uncompressed YUYV saturates USB 2.0 bandwidth and frames start timing out).
    cam_fourcc: str | None = None
    # OpenCV async_read wait; raise if USB/MSMF is slow (e.g. 200 ms was too tight on Windows).
    cam_async_read_timeout_ms: float = 500.0

    # Joy-Con dataset recording (examples/xlerobot/7_xlerobot_teleop_joycon_record.py)
    record_data_root: Path = Path(r"E:\FVL\EmbodiedAI\XLeRobot\LeRobot\data\recordings")  # None -> HF_LEROBOT_HOME at runtime
    record_exp_name: str = "pick_and_place"
    record_task: str = "Pick up the block and place it in the plate."
    record_repo_id: str | None = None  # None -> "local/{record_exp_name}"
    record_resume: bool = True
    record_overwrite: bool = False
    record_push_to_hub: bool = False
    record_show_preview: bool = True
    record_preview_height: int = 240
    # Same idea as detect_cameras.py --live-max-width; 0 = no downscale.
    record_preview_max_width: int = 1920

    # Collision-free pose used when teleoperation starts. Values use the same
    # calibrated coordinate system returned by get_observation(). Base wheels are
    # intentionally absent because they run in velocity mode and start at 0 velocity.
    start_pose: dict[str, float] = field(default_factory=default_xlerobot_start_pose)

    cameras: dict[str, CameraConfig] = field(default_factory=xlerobot_cameras_config)

    # Set to `True` for backward compatibility with previous policies/dataset
    use_degrees: bool = False

    teleop_keys: dict[str, str] = field(
        default_factory=lambda: {
            # Movement
            "forward": "i",
            "backward": "k",
            "left": "j",
            "right": "l",
            "rotate_left": "u",
            "rotate_right": "o",
            # Speed control
            "speed_up": "n",
            "speed_down": "m",
            # quit teleop
            "quit": "b",
        }
    )

    def __post_init__(self):
        apply_xlerobot_local_overrides(self)
        object.__setattr__(self, "start_pose", validate_xlerobot_start_pose(self.start_pose))
        if not self.cameras:
            object.__setattr__(
                self,
                "cameras",
                make_xlerobot_opencv_cameras(
                    self.cam_left_wrist,
                    self.cam_right_wrist,
                    self.cam_head,
                    fps=self.cam_fps,
                    width=self.cam_width,
                    height=self.cam_height,
                    fourcc=self.cam_fourcc,
                    async_read_timeout_ms=self.cam_async_read_timeout_ms,
                ),
            )
        super().__post_init__()


@dataclass
class XLerobotHostConfig:
    # Network Configuration
    port_zmq_cmd: int = 5555
    port_zmq_observations: int = 5556

    # Duration of the application
    connection_time_s: int = 3600

    # Watchdog: stop the robot if no command is received for over 0.5 seconds.
    watchdog_timeout_ms: int = 500

    # If robot jitters decrease the frequency and monitor cpu load with `top` in cmd
    max_loop_freq_hz: int = 30

@RobotConfig.register_subclass("xlerobot_client")
@dataclass
class XLerobotClientConfig(RobotConfig):
    # Network Configuration
    remote_ip: str
    port_zmq_cmd: int = 5555
    port_zmq_observations: int = 5556

    teleop_keys: dict[str, str] = field(
        default_factory=lambda: {
            # Movement
            "forward": "i",
            "backward": "k",
            "left": "j",
            "right": "l",
            "rotate_left": "u",
            "rotate_right": "o",
            # Speed control
            "speed_up": "n",
            "speed_down": "m",
            # quit teleop
            "quit": "b",
        }
    )

    cam_left_wrist: str | int = 0
    cam_right_wrist: str | int = 2
    cam_head: str | int = 3
    cam_fps: int = 30
    cam_width: int = 640
    cam_height: int = 480
    # FOURCC pixel format. "MJPG" strongly advised when several USB cameras share one bus.
    cam_fourcc: str | None = None
    cam_async_read_timeout_ms: float = 500.0

    cameras: dict[str, CameraConfig] = field(default_factory=xlerobot_cameras_config)

    polling_timeout_ms: int = 15
    connect_timeout_s: int = 5

    def __post_init__(self):
        apply_xlerobot_local_overrides(self)
        if not self.cameras:
            object.__setattr__(
                self,
                "cameras",
                make_xlerobot_opencv_cameras(
                    self.cam_left_wrist,
                    self.cam_right_wrist,
                    self.cam_head,
                    fps=self.cam_fps,
                    width=self.cam_width,
                    height=self.cam_height,
                    fourcc=self.cam_fourcc,
                    async_read_timeout_ms=self.cam_async_read_timeout_ms,
                ),
            )
        super().__post_init__()
