"""兼容层 —— 实现已移入正式模块。

真正的实现在 `src/lerobot/robots/xlerobot/state_publisher.py`，
由 `examples/xlerobot/7_xlerobot_teleop_joycon.py --publish-state` 使用。

保留本文件是为了让 `dsh/` 里的临时脚本（自检、调试）继续能用同一套符号，
并且强制把本仓库的 `src/` 放在 `sys.path` 最前面 —— 否则 conda 环境的
editable 安装可能指向另一份 checkout，导致"改了没生效"。
"""

from __future__ import annotations

import pathlib
import sys

_SRC = pathlib.Path(__file__).resolve().parents[1] / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from lerobot.robots.xlerobot.state_publisher import (  # noqa: E402,F401
    DEFAULT_FPS,
    DEFAULT_PORT,
    PROTOCOL_VERSION,
    PublisherError,
    RobotStateStream,
    StateStreamClient,
    install,
)
