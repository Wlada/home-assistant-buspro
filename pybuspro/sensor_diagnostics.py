import asyncio
import json
import os
from collections import deque
from datetime import datetime, timezone
from pathlib import Path


class SensorDiagnosticCapture:
    """Write a bounded, privacy-scoped Buspro sensor diagnostic capture."""

    def __init__(self, path, max_records=500):
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

    def record_request(
        self,
        *,
        name,
        device,
        role,
        request_profile,
        operate_code,
    ):
        self._write(
            {
                "timestamp": self._timestamp(),
                "direction": "request",
                "name": name,
                "device": device,
                "role": role,
                "request_profile": request_profile,
                "operate_code": operate_code,
            }
        )

    def record_response(
        self,
        *,
        name,
        device,
        role,
        request_profile,
        operate_code,
        payload,
        temperature,
        illuminance,
        humidity,
        raw_motion,
        movement,
    ):
        payload_bytes = list(payload) if isinstance(payload, (list, tuple)) else []
        self._write(
            {
                "timestamp": self._timestamp(),
                "direction": "response",
                "name": name,
                "device": device,
                "role": role,
                "request_profile": request_profile,
                "operate_code": operate_code,
                "payload_length": len(payload_bytes),
                "payload": payload_bytes,
                "temperature": temperature,
                "illuminance": illuminance,
                "humidity": humidity,
                "raw_motion": raw_motion,
                "movement": movement,
            }
        )

    def record_dispatch(self, *, operate_code, candidates):
        safe_candidates = []
        for candidate in candidates:
            safe_candidates.append(
                {
                    "name": candidate.get("name"),
                    "device": candidate.get("device"),
                    "role": candidate.get("role"),
                    "matched_by": candidate.get("matched_by"),
                }
            )
        self._write(
            {
                "timestamp": self._timestamp(),
                "direction": "dispatch",
                "operate_code": operate_code,
                "candidates": safe_candidates,
            }
        )

    def record_raw_response(
        self,
        *,
        name,
        device,
        role,
        operate_code,
        payload,
    ):
        payload_bytes = (
            list(payload) if isinstance(payload, (list, tuple)) else []
        )
        self._write(
            {
                "timestamp": self._timestamp(),
                "direction": "raw_response",
                "name": name,
                "device": device,
                "role": role,
                "operate_code": operate_code,
                "payload_length": len(payload_bytes),
                "payload": payload_bytes,
            }
        )

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
