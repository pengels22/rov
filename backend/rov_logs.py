import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


class RotatingLineLog:
    """Tiny persistent newest-N-lines log."""

    def __init__(self, path: str | Path, max_lines: int = 200):
        self.path = Path(path)
        self.max_lines = max(1, int(max_lines))
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)

    def append(self, record: Dict[str, Any]) -> None:
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            **record,
        }
        line = json.dumps(entry, separators=(",", ":"), default=str)

        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
            self._trim_locked()

    def read_lines(self, limit: Optional[int] = None) -> List[str]:
        with self._lock:
            lines = self.path.read_text(encoding="utf-8", errors="replace").splitlines()
        if limit is not None:
            return lines[-max(0, int(limit)):]
        return lines

    def read_records(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        for line in self.read_lines(limit):
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                records.append({"raw": line, "parse_error": True})
        return records

    def _trim_locked(self) -> None:
        lines = self.path.read_text(encoding="utf-8", errors="replace").splitlines()
        if len(lines) <= self.max_lines:
            return
        newest = lines[-self.max_lines:]
        self.path.write_text("\n".join(newest) + "\n", encoding="utf-8")


class RovLogs:
    def __init__(self, log_dir: str | Path, max_lines: int = 200):
        log_dir = Path(log_dir)
        self.command = RotatingLineLog(log_dir / "commands.log", max_lines)
        self.system = RotatingLineLog(log_dir / "system.log", max_lines)

    def command_event(self, event: str, **fields: Any) -> None:
        command = str(fields.get("command", "")).strip().upper()
        if command == "HB":
            return
        self.command.append({"event": event, **fields})

    def system_event(self, level: str, message: str, **fields: Any) -> None:
        self.system.append({
            "level": level,
            "message": message,
            **fields,
        })

    def _filter_repeated_serial_output(
        self,
        records: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        filtered: List[Dict[str, Any]] = []
        last_response_by_command: Dict[tuple[str, str], str] = {}
        pending_tx_by_command: Dict[tuple[str, str], Dict[str, Any]] = {}

        for record in records:
            event = record.get("event")
            device = str(record.get("device", ""))
            command = str(record.get("command", "")).strip()
            key = (device, command)

            if event == "tx" and device and command:
                pending_tx_by_command[key] = record
                continue

            if event == "rx" and device and command:
                response = str(record.get("response", ""))
                if last_response_by_command.get(key) == response:
                    pending_tx_by_command.pop(key, None)
                    continue
                last_response_by_command[key] = response
                pending_tx = pending_tx_by_command.pop(key, None)
                if pending_tx is not None:
                    filtered.append(pending_tx)
                filtered.append(record)
                continue

            filtered.append(record)

        return filtered

    def snapshot(self, limit: Optional[int] = None) -> Dict[str, Any]:
        commands = self._filter_repeated_serial_output(
            self.command.read_records(limit)
        )
        return {
            "commands": commands,
            "system": self.system.read_records(limit),
            "files": {
                "commands": str(self.command.path),
                "system": str(self.system.path),
            },
            "max_lines": self.command.max_lines,
        }
