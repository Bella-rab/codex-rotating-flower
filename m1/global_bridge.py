#!/usr/bin/env python3
"""Drive one ESP32 motor from all active local Codex tasks on this computer.

This adapter reads Codex's local rollout files. Their format is internal and may
change; the rest of the bridge only sees (session id, active, effort) states.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import process_lock
import serial_link


EFFORT_SPEED = {
    "none": "LOW", "minimal": "LOW", "low": "LOW",
    "medium": "MEDIUM", "high": "HIGH", "xhigh": "XHIGH",
    "max": "ULTRA", "ultra": "ULTRA",
}
SPEED_RANK = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "XHIGH": 4, "ULTRA": 5}
INTERESTING_EVENTS = (
    b"thread_settings_applied", b"task_started", b"task_complete",
    b"turn_aborted", b"turn_context"
)


def event_time(record: dict) -> float:
    value = record.get("timestamp")
    if isinstance(value, str):
        try:
            return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return 0.0


@dataclass
class TaskState:
    session_id: str
    effort: str | None = None
    active: bool = False
    turn_id: str | None = None
    started_at: float = 0.0
    pending_context: tuple[str, str] | None = None

    @property
    def speed(self) -> str | None:
        if not self.active:
            return None
        return EFFORT_SPEED.get(self.effort or "", "MEDIUM")

    def consume(self, record: dict) -> bool:
        if record.get("type") == "turn_context":
            context = record.get("payload") or {}
            turn_id = context.get("turn_id")
            effort = context.get("effort")
            if isinstance(turn_id, str) and isinstance(effort, str):
                self.pending_context = (turn_id, effort.lower())
                if self.active and self.turn_id == turn_id:
                    changed = self.effort != effort.lower()
                    self.effort = effort.lower()
                    return changed
            return False
        if record.get("type") != "event_msg":
            return False
        event = record.get("payload") or {}
        kind = event.get("type")
        before = (self.effort, self.active, self.turn_id)
        if kind == "thread_settings_applied":
            settings = event.get("thread_settings") or {}
            value = settings.get("reasoning_effort")
            if isinstance(value, str):
                self.effort = value.lower()
        elif kind == "task_started":
            self.active = True
            self.turn_id = event.get("turn_id")
            self.started_at = event_time(record)
            if self.pending_context and self.pending_context[0] == self.turn_id:
                self.effort = self.pending_context[1]
        elif kind in ("task_complete", "turn_aborted"):
            if self.active and (not event.get("turn_id") or event.get("turn_id") == self.turn_id):
                self.active = False
        return before != (self.effort, self.active, self.turn_id)


def is_root_meta(record: dict) -> bool:
    if record.get("type") != "session_meta":
        return False
    meta = record.get("payload") or {}
    source = meta.get("source")
    return (not meta.get("parent_thread_id")
            and meta.get("thread_source") in ("user", "voice_chat")
            and not (isinstance(source, dict) and "subagent" in source)
            and isinstance(meta.get("id"), str))


class RolloutFile:
    """Track one rollout file by byte offset.

    The file is opened for each poll instead of staying open: Windows refuses
    to delete a file that another process still holds open, and Codex removes
    old rollout files while the bridge is running.
    """

    def __init__(self, path: Path):
        self.path = path
        self.state = TaskState("")
        self.pending = b""
        self.skip_long_line = False
        self.size = 0
        self.inode = None
        self.mtime = 0.0
        self.is_root = False
        self._read_header()

    def _read_header(self) -> None:
        stat = self.path.stat()
        self.inode = stat.st_ino or None
        self.mtime = stat.st_mtime
        with self.path.open("rb") as file:
            first = file.readline()
            self.size = file.tell()
        try:
            meta = json.loads(first)
        except (UnicodeDecodeError, json.JSONDecodeError):
            meta = {}
        self.is_root = is_root_meta(meta)
        self.state = TaskState((meta.get("payload") or {}).get("id", ""))
        if self.is_root:
            self.poll()

    def close(self) -> None:
        """Nothing stays open between polls; kept for the adapter's cleanup."""

    def _was_replaced(self, stat) -> bool:
        if stat.st_size < self.size:
            return True
        return bool(stat.st_ino) and bool(self.inode) and stat.st_ino != self.inode

    def poll(self) -> bool:
        # Let the adapter remove this task immediately if its rollout vanishes.
        # Keeping its old active state would continue refreshing the motor watchdog.
        stat = self.path.stat()
        self.mtime = stat.st_mtime
        if self._was_replaced(stat):
            self.pending = b""
            self.skip_long_line = False
            self.size = 0
            self.state = TaskState(self.state.session_id)
            self.inode = stat.st_ino or None
        changed = False
        with self.path.open("rb") as file:
            file.seek(self.size)
            while True:
                chunk = file.read(65536)
                if not chunk:
                    break
                self.size += len(chunk)
                data = self.pending + chunk
                parts = data.split(b"\n")
                self.pending = parts.pop()
                for line in parts:
                    if self.skip_long_line:
                        self.skip_long_line = False
                        continue
                    if not any(marker in line for marker in INTERESTING_EVENTS):
                        continue
                    try:
                        changed = self.state.consume(json.loads(line)) or changed
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        continue
                if len(self.pending) > 8_000_000:
                    self.pending = b""
                    self.skip_long_line = True
        return changed


class CodexRolloutAdapter:
    def __init__(self, root: Path, stale_seconds: float = 600):
        self.root = root
        self.stale_seconds = stale_seconds
        self.files: dict[Path, RolloutFile] = {}

    def poll(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        changed = False
        if self.root.exists():
            for path in self.root.rglob("rollout-*.jsonl"):
                try:
                    recent = now - path.stat().st_mtime <= self.stale_seconds
                except OSError:
                    continue
                if recent and path not in self.files:
                    try:
                        entry = RolloutFile(path)
                    except OSError:
                        continue
                    if entry.is_root:
                        self.files[path] = entry
                        changed = True
                    else:
                        entry.close()
        for path, entry in list(self.files.items()):
            try:
                changed = entry.poll() or changed
            except OSError:
                entry.close()
                del self.files[path]
                changed = True
                continue
            if now - entry.mtime > self.stale_seconds:
                entry.close()
                del self.files[path]
                changed = True
        return changed

    def selected(self, now: float | None = None) -> tuple[str, str | None, int]:
        now = time.time() if now is None else now
        active = [entry.state for entry in self.files.values()
                  if entry.state.speed is not None and now - entry.mtime <= self.stale_seconds]
        if not active:
            return "STOP", None, 0
        selected = max(active, key=lambda s: (SPEED_RANK[s.speed], s.started_at))
        return "RUN " + selected.speed, selected.session_id, len(active)

    def close(self) -> None:
        for entry in self.files.values():
            entry.close()
        self.files.clear()


def serial_candidates() -> list[str]:
    return serial_link.list_serial_ports()


class MotorSerial:
    def __init__(self, requested_port: str = "auto"):
        self.requested_port = requested_port
        self.port: serial_link.SerialPort | None = None
        self.path: str | None = None
        self.last_command: str | None = None
        self.last_sent_at = 0.0
        self.last_probe_at = -float("inf")

    @property
    def connected(self) -> bool:
        return self.port is not None

    def close(self) -> None:
        if self.port is not None:
            try:
                self.port.close()
            except OSError:
                pass
        self.port = None
        self.path = None
        self.last_command = None

    def _probe(self, path: str) -> bool:
        port = None
        try:
            port = serial_link.open_port(path)
            time.sleep(0.6)
            port.write(b"PING\n")
            deadline = time.monotonic() + 1.5
            reply = b""
            while time.monotonic() < deadline:
                time.sleep(0.05)
                chunk = port.read()
                if chunk:
                    reply += chunk
                    if b"PONG" in reply:
                        self.port = port
                        self.path = path
                        self.last_command = None
                        print(f"Motor connected: {path}", flush=True)
                        return True
        except OSError:
            pass
        if port is not None:
            try:
                port.close()
            except OSError:
                pass
        return False

    def connect(self) -> bool:
        if self.port is not None and self.path and serial_link.port_still_present(self.path):
            return True
        if self.port is not None:
            self.close()
        now = time.monotonic()
        if now - self.last_probe_at < 2:
            return False
        self.last_probe_at = now
        paths = ([self.requested_port] if self.requested_port != "auto"
                 else serial_candidates())
        for path in paths:
            if self._probe(path):
                return True
        return False

    def send(self, command: str) -> bool:
        if not self.connect():
            return False
        now = time.monotonic()
        if command == self.last_command and now - self.last_sent_at < 2:
            return True
        try:
            self.port.write((command + "\n").encode("ascii"))
            if command != self.last_command:
                print(f"Motor: {command}", flush=True)
            self.last_command = command
            self.last_sent_at = now
            self.port.read()
            return True
        except OSError:
            print("Motor disconnected; scanning USB ports", flush=True)
            self.close()
            return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    default_root = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "sessions"
    parser.add_argument("--sessions-root", type=Path, default=default_root)
    parser.add_argument("--port", default="auto", help="Serial device or auto (default)")
    parser.add_argument("--stale-seconds", type=float, default=600)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--once", action="store_true", help="Show aggregate state without opening USB")
    args = parser.parse_args(argv)
    lock_file = None
    if not args.once:
        lock_path = process_lock.singleton_path("codex-motor-bridge")
        lock_file = process_lock.acquire(lock_path)
        if lock_file is None:
            print("Another global motor bridge is already running", file=sys.stderr)
            return 2
    adapter = CodexRolloutAdapter(args.sessions_root, args.stale_seconds)
    motor = MotorSerial(args.port)
    try:
        adapter.poll()
        previous = None
        while True:
            command, _, count = adapter.selected()
            state = (command, count)
            if state != previous:
                print(f"Codex active: {count}; motor: {command}", flush=True)
                previous = state
            if args.once:
                return 0
            motor.send(command)
            time.sleep(max(0.1, args.poll_seconds))
            adapter.poll()
    except KeyboardInterrupt:
        print("Bridge stopped", flush=True)
        return 0
    finally:
        if motor.connected:
            motor.send("STOP")
        motor.close()
        adapter.close()
        if lock_file is not None:
            lock_file.close()


if __name__ == "__main__":
    sys.exit(main())
