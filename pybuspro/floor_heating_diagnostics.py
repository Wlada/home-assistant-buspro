import asyncio
import json
import os
from collections import deque
from datetime import datetime, timezone
from pathlib import Path


_READ_STATUS_RESPONSE = "ReadFloorHeatingStatusResponse"
_CONTROL_STATUS = "ControlFloorHeatingStatus"
_CONTROL_STATUS_RESPONSE = "ControlFloorHeatingStatusResponse"
_PANEL_OPERATE_CODES = {
    "ControlPanelAC",
    "ControlPanelACResponse",
    "ReadPanelAC",
    "ReadPanelACResponse",
}


class FloorHeatingDiagnosticCapture:
    """Write bounded floor-heating diagnostics without Bus addresses."""

    def __init__(self, path, max_records=1000):
        self.path = Path(path)
        self.records = deque(maxlen=max_records)
        self._write_queue = None
        self._writer_task = None
        self._closed = False
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._load_existing_records()
        else:
            self._write_queue = asyncio.Queue()
            self._writer_task = loop.create_task(self._async_writer())

    def _load_existing_records(self):
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
            for line in lines[-self.records.maxlen :]:
                record = json.loads(line)
                if isinstance(record, dict):
                    self.records.append(record)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return

    @staticmethod
    def _timestamp():
        return datetime.now(timezone.utc).isoformat()

    def record_telegram(
        self,
        *,
        direction,
        operate_code,
        source_aliases,
        target_aliases,
        payload,
    ):
        payload_bytes = (
            list(payload) if isinstance(payload, (bytes, bytearray, list, tuple)) else []
        )
        record = {
            "timestamp": self._timestamp(),
            "direction": direction,
            "operate_code": operate_code,
            "source_aliases": self._safe_aliases(source_aliases),
            "target_aliases": self._safe_aliases(target_aliases),
            "payload_length": len(payload_bytes),
            "payload": payload_bytes,
        }
        record.update(self._decode(operate_code, payload_bytes))
        self._write(record)

    @staticmethod
    def _safe_aliases(aliases):
        safe_aliases = []
        for alias in aliases or []:
            if isinstance(alias, str):
                safe_aliases.append({"name": alias})
                continue
            if not isinstance(alias, dict):
                continue
            safe_alias = {"name": str(alias.get("name") or "unnamed")}
            channel = alias.get("configured_channel")
            if type(channel) is int:
                safe_alias["configured_channel"] = channel
            safe_aliases.append(safe_alias)
        return safe_aliases

    @staticmethod
    def _decode(operate_code, payload):
        if operate_code == _READ_STATUS_RESPONSE and len(payload) >= 8:
            return {
                "temperature_type": payload[0],
                "current_temperature": payload[1],
                "enabled": payload[2],
                "mode": payload[3],
                "normal_temperature": payload[4],
                "day_temperature": payload[5],
                "night_temperature": payload[6],
                "away_temperature": payload[7],
            }

        if operate_code == _CONTROL_STATUS and len(payload) >= 7:
            return {
                "temperature_type": payload[0],
                "enabled": payload[1],
                "mode": payload[2],
                "normal_temperature": payload[3],
                "day_temperature": payload[4],
                "night_temperature": payload[5],
                "away_temperature": payload[6],
            }

        if operate_code == _CONTROL_STATUS_RESPONSE and len(payload) >= 8:
            return {
                "result": payload[0],
                "temperature_type": payload[1],
                "enabled": payload[2],
                "mode": payload[3],
                "normal_temperature": payload[4],
                "day_temperature": payload[5],
                "night_temperature": payload[6],
                "away_temperature": payload[7],
            }

        if operate_code in _PANEL_OPERATE_CODES and payload:
            decoded = {"panel_command": payload[0]}
            if len(payload) > 1:
                decoded["panel_value"] = payload[1]
            if len(payload) > 2:
                decoded["panel_channel"] = payload[2]
            return decoded

        return {}

    def _write(self, record):
        if self._closed:
            return
        if self._writer_task is None:
            self._write_sync(record)
            return
        self._write_queue.put_nowait(record)

    async def _async_writer(self):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._load_existing_records)
        while True:
            record = await self._write_queue.get()
            try:
                if record is None:
                    return
                await loop.run_in_executor(None, self._write_sync, record)
            finally:
                self._write_queue.task_done()

    async def async_close(self):
        """Flush queued diagnostic records and stop the background writer."""
        if self._writer_task is None or self._closed:
            return
        self._closed = True
        self._write_queue.put_nowait(None)
        await self._writer_task
        self._writer_task = None

    def _write_sync(self, record):
        try:
            self.records.append(record)
            temporary_path = self.path.with_suffix(self.path.suffix + ".tmp")
            serialized = "\n".join(
                json.dumps(item, ensure_ascii=True, separators=(",", ":"))
                for item in self.records
            )
            temporary_path.write_text(serialized + "\n", encoding="utf-8")
            os.replace(temporary_path, self.path)
        except (OSError, TypeError, ValueError):
            return
