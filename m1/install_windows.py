#!/usr/bin/env python3
"""Install the global bridge as one Windows per-user startup task.

Run: python m1\\install_windows.py install
Update: python m1\\install_windows.py install --replace
Remove: python m1\\install_windows.py uninstall

The task runs under the current user and starts at sign-in, matching what
``install_macos.py`` sets up with a LaunchAgent.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


TASK_NAME = "RotatingFlowerCodexMotor"


def locations() -> tuple[Path, Path]:
    local = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    support = local / "RotatingFlower"
    return support, support / "logs"


def schtasks(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["schtasks", *args], check=check, capture_output=True,
                          text=True, timeout=30)


def task_exists() -> bool:
    result = schtasks("/Query", "/TN", TASK_NAME, check=False)
    return result.returncode == 0


def install(replace: bool, sessions_root: Path, port: str) -> None:
    support, logs = locations()
    if task_exists() and not replace:
        raise RuntimeError(f"Already installed as {TASK_NAME}. Use install --replace to update.")
    if not sessions_root.is_dir():
        raise RuntimeError(f"Codex sessions directory does not exist: {sessions_root}")
    support.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    for name in ("global_bridge.py", "serial_link.py", "process_lock.py"):
        shutil.copy2(Path(__file__).with_name(name), support / name)
    command = (
        f'"{sys.executable}" "{support / "global_bridge.py"}" '
        f'--sessions-root "{sessions_root}" --port "{port}"'
    )
    schtasks("/Create", "/TN", TASK_NAME, "/TR", command, "/SC", "ONLOGON",
             "/RL", "LIMITED", "/F")
    print(f"Installed {TASK_NAME}")
    print(f"Logs: {logs}")
    print("The task starts at sign-in. Start it now with:")
    print(f'  schtasks /Run /TN {TASK_NAME}')


def uninstall() -> None:
    support, _ = locations()
    if task_exists():
        schtasks("/Delete", "/TN", TASK_NAME, "/F")
    for name in ("global_bridge.py", "serial_link.py", "process_lock.py"):
        target = support / name
        if target.exists():
            target.unlink()
    print(f"Removed {TASK_NAME}; log files were kept")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "uninstall"))
    parser.add_argument("--replace", action="store_true")
    default_root = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "sessions"
    parser.add_argument("--sessions-root", type=Path, default=default_root)
    parser.add_argument("--port", default="auto", help="COM port such as COM3, or auto")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("This installer supports Windows only")
    try:
        if args.action == "install":
            install(args.replace, args.sessions_root, args.port)
        else:
            uninstall()
    except (OSError, subprocess.CalledProcessError, RuntimeError) as exc:
        print(f"Install error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
