"""Read-only state publisher for XLerobot.

Runs *inside* the process that owns the serial buses (the teleoperation script)
and exposes the latest observation over ``http://127.0.0.1:<port>``.

Two ways to use it:

1. Wrapper (used by ``examples/xlerobot/7_xlerobot_teleop_joycon.py``)::

       robot = XLerobot(cfg); robot.connect()
       robot = RobotStateStream(robot, port=8765, fps=30)

   Every other attribute is proxied to the real robot, so the teleop loop does
   not change.

2. Runtime injection (for scripts you do not want to touch)::

       install(robot, port=8765, fps=30)

Design rules
------------
* The only source of ``action`` is the return value of ``send_action()``, i.e.
  the value actually written to the motors after ``max_relative_target``
  clipping. Actions are never inferred from observations.
* The capture timestamp is taken at the **start** of the motor read, so a slow
  camera cannot make a stale sample look fresh.
* The serial I/O lock is held only while reading motors; camera reads happen
  outside it so a stalled camera cannot block the control loop.
* Consumers are read-only: only GET, bound to loopback.
"""

from __future__ import annotations

import io
import json
import logging
import threading
import time
import types
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.request import urlopen

import numpy as np

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = 1
DEFAULT_PORT = 8765
DEFAULT_FPS = 30
# XLerobot.send_action writes zero base velocity when these keys are omitted.
BASE_VEL_KEYS = ("x.vel", "y.vel", "theta.vel")


class PublisherError(RuntimeError):
    """The publisher cannot serve a usable snapshot."""


class RobotStateStream:
    """Proxy around an already connected robot that publishes read-only state."""

    def __init__(
        self,
        robot: Any,
        *,
        port: int = DEFAULT_PORT,
        fps: int = DEFAULT_FPS,
        max_observation_age_s: float = 1.0,
        camera_reconnect_interval_s: float = 2.0,
        camera_read_timeout_ms: float = 250.0,
    ):
        if fps <= 0:
            raise ValueError("fps must be positive")
        self.robot = robot
        self.fps = fps
        self.port = port
        self.max_observation_age_s = max_observation_age_s
        self.camera_reconnect_interval_s = camera_reconnect_interval_s
        # 单路读取超时：正常 30fps 下 250ms 足够；设小是为了"坏相机只卡一次"
        self.camera_read_timeout_ms = camera_read_timeout_ms

        self._io_lock = threading.RLock()
        self._condition = threading.Condition()
        self._stop = threading.Event()
        self._snapshot: dict | None = None
        self._error = "Waiting for first observation"
        self._action: dict[str, float] = {}
        self._action_time = 0.0
        self._sequence = 0
        # 相机可单独掉线/恢复：失败集合与"正在重连"标记
        self._camera_lock = threading.Lock()
        self._camera_errors: dict[str, str] = {}
        self._camera_busy: set[str] = set()

        self.schema = self._build_schema()
        self._server: ThreadingHTTPServer | None = None
        self._threads: list[threading.Thread] = []

    # -- contract ----------------------------------------------------------
    def _build_schema(self) -> dict:
        features = self.robot.observation_features
        return {
            "protocol_version": PROTOCOL_VERSION,
            "stream_id": uuid.uuid4().hex,
            "robot_type": getattr(self.robot, "name", "xlerobot"),
            "state_names": [k for k, v in features.items() if v is float],
            "action_names": list(self.robot.action_features),
            "cameras": {k: list(v) for k, v in features.items() if isinstance(v, tuple)},
        }

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> "RobotStateStream":
        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), _make_handler(self))
        self.port = self._server.server_port
        self._threads = [
            threading.Thread(target=self._read_loop, name="xlerobot-state-reader", daemon=True),
            threading.Thread(target=self._server.serve_forever, name="xlerobot-state-http", daemon=True),
            threading.Thread(target=self._camera_watchdog, name="xlerobot-camera-watchdog", daemon=True),
        ]
        for thread in self._threads:
            thread.start()
        return self

    def close(self) -> None:
        """Stop the threads. Must run before the underlying robot disconnects."""
        self._stop.set()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        for thread in self._threads:
            thread.join(timeout=2.0)
        self._threads = []

    def __enter__(self) -> "RobotStateStream":
        if self._server is None:
            self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __getattr__(self, name: str):
        # Everything not defined here is proxied, so the teleop loop is unchanged.
        if name == "robot":  # guard against recursion before self.robot exists
            raise AttributeError(name)
        return getattr(self.robot, name)

    # -- proxied robot API -------------------------------------------------
    def get_observation(self) -> dict[str, Any]:
        """Return the latest published observation.

        The teleop loop reads this instead of touching the buses again: one
        reader thread owns the hardware, which keeps the sample rate
        independent from the control rate.
        """
        with self._condition:
            self._condition.wait_for(
                lambda: self._snapshot is not None or self._stop.is_set(), timeout=3.0
            )
            snapshot = self._snapshot
            if snapshot is None:
                raise RuntimeError(self._error or "Robot state is stale")
            age = time.monotonic() - snapshot["captured_at"]
            if age > self.max_observation_age_s:
                raise RuntimeError(f"Robot state is stale ({age:.2f}s)")
            return dict(snapshot["observation"])

    def _latch(self, sent: dict[str, Any]) -> None:
        """Record the value actually written to the motors (post-clipping)."""
        self._action.update(dict.fromkeys(BASE_VEL_KEYS, 0.0))
        self._action.update(sent)
        self._action_time = time.monotonic()

    def send_action(self, action: dict[str, Any]) -> dict[str, Any]:
        with self._io_lock:
            sent = self.robot.send_action(action)
            self._latch(sent)
            return sent

    def _send_action_via(self, forward, action: dict[str, Any]) -> dict[str, Any]:
        """Same as ``send_action`` but through a captured bound method.

        Needed by ``install()``: there the robot's own ``send_action`` is
        replaced by this class, so calling ``self.robot.send_action`` would
        recurse.
        """
        with self._io_lock:
            sent = forward(action)
            self._latch(sent)
            return sent

    def stop_base(self) -> None:
        with self._io_lock:
            self.robot.stop_base()
            self._latch({})

    def _stop_base_via(self, forward) -> None:
        with self._io_lock:
            forward()
            self._latch({})

    def disconnect(self) -> None:
        self.close()
        self.robot.disconnect()

    # -- state reading -----------------------------------------------------
    def read_state(self) -> dict[str, Any]:
        """Read motor feedback. The caller must hold ``_io_lock``."""
        get_state = getattr(self.robot, "get_state_observation", None)
        if callable(get_state):
            return get_state()
        return self._read_state_from_buses()

    def _read_state_from_buses(self) -> dict[str, Any]:
        """Fallback when the robot has no ``get_state_observation``."""
        r = self.robot
        left = r.bus1.sync_read("Present_Position", r.left_arm_motors)
        head = r.bus1.sync_read("Present_Position", r.head_motors)
        right = r.bus2.sync_read("Present_Position", r.right_arm_motors)
        wheel = r.bus2.sync_read("Present_Velocity", r.base_motors)
        base_velocity = r._wheel_raw_to_body(
            wheel["base_left_wheel"], wheel["base_back_wheel"], wheel["base_right_wheel"]
        )
        state: dict[str, Any] = {}
        for part in (left, right, head):
            state.update({f"{motor}.pos": value for motor, value in part.items()})
        state.update(base_velocity)
        return state

    def _read_cameras(self) -> tuple[dict[str, Any], dict[str, str]]:
        """Read each camera in isolation.

        One unplugged camera must not invalidate the whole snapshot: motor state
        and the remaining views stay publishable, and the failure is reported in
        ``metadata["cameras_failed"]``.
        """
        cameras = getattr(self.robot, "cameras", None)
        if not cameras:
            try:
                return self.robot.get_camera_observation(), {}
            except Exception as exc:
                reason = f"{type(exc).__name__}: {exc}"
                return {}, dict.fromkeys(self.schema["cameras"], reason)

        with self._camera_lock:
            busy = set(self._camera_busy)
            failed_before = dict(self._camera_errors)
        frames: dict[str, Any] = {}
        errors: dict[str, str] = {}
        for name, camera in cameras.items():
            if name in busy:            # 重连期间不要和看门狗抢同一个设备
                errors[name] = failed_before.get(name, "reconnecting")
                continue
            if name in failed_before:
                # 已知故障：交给看门狗，读取线程不再等它（否则每轮卡满超时）
                errors[name] = failed_before[name]
                continue
            try:
                frames[name] = camera.async_read(timeout_ms=self.camera_read_timeout_ms)
            except Exception as exc:
                errors[name] = f"{type(exc).__name__}: {exc}"
        with self._camera_lock:
            self._camera_errors = dict(errors)
        return frames, errors

    def _camera_watchdog(self) -> None:
        """Try to bring back cameras that dropped off the USB bus.

        A UVC camera that re-enumerates gets a new /dev/videoN; because the
        config uses stable by-path links, reopening works. Without this the
        process would stay blind until the teleop restarted.
        """
        while not self._stop.wait(self.camera_reconnect_interval_s):
            with self._camera_lock:
                failed = list(self._camera_errors)
            cameras = getattr(self.robot, "cameras", {}) or {}
            for name in failed:
                camera = cameras.get(name)
                if camera is None or not hasattr(camera, "connect"):
                    continue
                with self._camera_lock:
                    self._camera_busy.add(name)
                try:
                    if getattr(camera, "is_connected", False):
                        camera.disconnect()
                    camera.connect(warmup=False)
                    camera.async_read(timeout_ms=1000)      # 验证真的出帧
                    with self._camera_lock:
                        self._camera_errors.pop(name, None)
                    logger.warning("Camera %s reconnected", name)
                except Exception as exc:
                    logger.debug("Camera %s reconnect failed: %s", name, exc)
                finally:
                    with self._camera_lock:
                        self._camera_busy.discard(name)

    def _read_loop(self) -> None:
        deadline = time.monotonic()
        last_camera_warning = 0.0
        while not self._stop.is_set():
            try:
                with self._io_lock:
                    captured_at = time.monotonic()  # timestamp at read START
                    observation = self.read_state()
                    action, action_time = dict(self._action), self._action_time
                frames, camera_errors = self._read_cameras()  # outside the lock
                observation.update(frames)
                now = time.monotonic()
                if camera_errors and now - last_camera_warning > 5.0:
                    last_camera_warning = now
                    logger.warning("Camera(s) failing: %s", ", ".join(sorted(camera_errors)))

                # Decouple the snapshot from reused bus/camera buffers.
                observation = {
                    key: (value.copy() if isinstance(value, np.ndarray) else value)
                    for key, value in observation.items()
                }
                self._sequence += 1
                with self._condition:
                    self._snapshot = {
                        "observation": observation,
                        "action": action,
                        "action_time": action_time,
                        "captured_at": captured_at,
                        "sequence": self._sequence,
                        "cameras_failed": camera_errors,
                    }
                    self._error = ""
                    self._condition.notify_all()
            except Exception as exc:  # a failed motor read must invalidate, not reuse
                with self._condition:
                    self._snapshot = None
                    self._error = f"{type(exc).__name__}: {exc}"
                    self._condition.notify_all()

            deadline += 1.0 / self.fps
            if deadline < time.monotonic():
                deadline = time.monotonic()  # never burst to catch up
            self._stop.wait(max(0.0, deadline - time.monotonic()))

    # -- serialization -----------------------------------------------------
    def encode_snapshot(self) -> bytes:
        with self._condition:
            snapshot, error = self._snapshot, self._error
        if snapshot is None:
            raise PublisherError(error or "No snapshot available")
        missing = set(self.schema["action_names"]) - snapshot["action"].keys()
        if missing:
            raise PublisherError("Waiting for teleoperation to send all joint targets")

        now = time.monotonic()
        cameras_failed = snapshot.get("cameras_failed") or {}
        metadata = {
            "protocol_version": PROTOCOL_VERSION,
            "stream_id": self.schema["stream_id"],
            "sequence": snapshot["sequence"],
            "state_age_s": now - snapshot["captured_at"],
            "action_age_s": now - snapshot["action_time"],
            "cameras_failed": cameras_failed,
        }
        # Only include views that are actually available; consumers decide whether
        # a missing view is fatal (the recorder treats it as such).
        images = {k: snapshot["observation"][k] for k in self.schema["cameras"]
                  if k in snapshot["observation"]}
        buffer = io.BytesIO()
        np.savez(
            buffer,
            metadata=np.frombuffer(json.dumps(metadata).encode(), dtype=np.uint8),
            state=np.asarray(
                [snapshot["observation"][k] for k in self.schema["state_names"]], dtype=np.float32
            ),
            action=np.asarray(
                [snapshot["action"][k] for k in self.schema["action_names"]], dtype=np.float32
            ),
            **{f"image_{k}": value for k, value in images.items()},
        )
        return buffer.getvalue()


# ---------------------------------------------------------------------------
# HTTP endpoint
# ---------------------------------------------------------------------------


def _make_handler(stream: RobotStateStream):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - stdlib naming
            if self.path == "/schema":
                payload, content_type = json.dumps(stream.schema).encode(), "application/json"
            elif self.path == "/snapshot":
                try:
                    payload = stream.encode_snapshot()
                except PublisherError as exc:
                    self.send_error(503, str(exc))
                    return
                content_type = "application/octet-stream"
            else:
                self.send_error(404)
                return

            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *args):
            pass

    return Handler


# ---------------------------------------------------------------------------
# Runtime injection (optional)
# ---------------------------------------------------------------------------


def install(robot: Any, *, port: int = DEFAULT_PORT, fps: int = DEFAULT_FPS) -> RobotStateStream:
    """Attach a publisher to an already connected robot without wrapping it.

    Useful for scripts that construct the robot themselves: patching an
    instance keeps ``isinstance`` intact and leaves the class untouched.
    """
    existing = getattr(robot, "_state_stream", None)
    if existing is not None:
        return existing

    stream = RobotStateStream(robot, port=port, fps=fps)
    orig_get_observation = robot.get_observation
    orig_send_action = robot.send_action
    orig_stop_base = getattr(robot, "stop_base", None)
    orig_disconnect = robot.disconnect

    robot._state_stream = stream
    robot.get_observation = types.MethodType(lambda self: stream.get_observation(), robot)
    # 用捕获的原方法转发，避免"替换后又被自己调用"的递归
    robot.send_action = types.MethodType(
        lambda self, action: stream._send_action_via(orig_send_action, action), robot
    )
    if callable(orig_stop_base):
        robot.stop_base = types.MethodType(
            lambda self: stream._stop_base_via(orig_stop_base), robot
        )

    def _disconnect(self, *args, **kwargs):
        stream.close()
        return orig_disconnect(*args, **kwargs)

    robot.disconnect = types.MethodType(_disconnect, robot)
    try:
        stream.start()
    except Exception:
        stream.close()
        robot.get_observation = orig_get_observation
        robot.send_action = orig_send_action
        if callable(orig_stop_base):
            robot.stop_base = orig_stop_base
        robot.disconnect = orig_disconnect
        del robot._state_stream
        raise
    return stream


class StateStreamClient:
    """Minimal client for the publisher (used by tests and tooling)."""

    def __init__(self, port: int = DEFAULT_PORT, timeout: float = 1.0):
        self.url = f"http://127.0.0.1:{port}"
        self.timeout = timeout
        with urlopen(f"{self.url}/schema", timeout=timeout) as response:
            self.schema = json.load(response)

    def read(self, max_age_s: float = 0.5) -> dict:
        started = time.monotonic()
        with urlopen(f"{self.url}/snapshot", timeout=self.timeout) as response:
            payload = response.read()
        with np.load(io.BytesIO(payload), allow_pickle=False) as data:
            metadata = json.loads(data["metadata"].tobytes())
            if metadata["stream_id"] != self.schema["stream_id"]:
                raise RuntimeError("Publisher restarted, reconnect")
            age = metadata["state_age_s"] + (time.monotonic() - started)
            if age > max_age_s:
                raise RuntimeError(f"State is stale ({age:.2f}s)")
            state, action = data["state"], data["action"]
            if state.shape != (len(self.schema["state_names"]),):
                raise ValueError("state shape mismatch")
            if action.shape != (len(self.schema["action_names"]),):
                raise ValueError("action shape mismatch")
            # Cameras that failed are omitted by the publisher, not padded.
            images = {
                key: data[f"image_{key}"]
                for key in self.schema["cameras"]
                if f"image_{key}" in data
            }
        return {
            "observation.state": state,
            "action": action,
            "state_age_s": age,
            "action_age_s": metadata["action_age_s"],
            "sequence": metadata["sequence"],
            "cameras_failed": metadata.get("cameras_failed") or {},
            **{f"observation.images.{key}": value for key, value in images.items()},
        }
