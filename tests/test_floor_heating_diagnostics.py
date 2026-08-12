import importlib.util
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = (
    Path(__file__).parents[1] / "pybuspro" / "floor_heating_diagnostics.py"
)
SPEC = importlib.util.spec_from_file_location(
    "floor_heating_diagnostics", MODULE_PATH
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
FloorHeatingDiagnosticCapture = MODULE.FloorHeatingDiagnosticCapture


class FloorHeatingDiagnosticCaptureTests(unittest.TestCase):
    def test_keeps_only_newest_configured_record_count(self):
        with tempfile.TemporaryDirectory() as directory:
            capture_path = Path(directory) / "capture.jsonl"
            capture = FloorHeatingDiagnosticCapture(
                capture_path, max_records=2
            )

            for value in (20, 21, 22):
                capture.record_telegram(
                    direction="incoming",
                    operate_code="ControlPanelACResponse",
                    source_aliases=["Enviro"],
                    target_aliases=["broadcast"],
                    payload=[25, value, 3],
                )

            records = [
                json.loads(line)
                for line in capture_path.read_text(
                    encoding="utf-8"
                ).splitlines()
            ]

        self.assertEqual(len(records), 2)
        self.assertEqual([record["panel_value"] for record in records], [21, 22])

    def test_malformed_existing_data_does_not_block_new_records(self):
        with tempfile.TemporaryDirectory() as directory:
            capture_path = Path(directory) / "capture.jsonl"
            capture_path.write_text("not-json\n", encoding="utf-8")

            capture = FloorHeatingDiagnosticCapture(capture_path)
            capture.record_telegram(
                direction="incoming",
                operate_code="ReadFloorHeatingStatusResponse",
                source_aliases=["Enviro"],
                target_aliases=["home_assistant"],
                payload=[0, 21, 1, 1, 22, 23, 20, 18],
            )
            record = json.loads(capture_path.read_text(encoding="utf-8"))

        self.assertEqual(record["current_temperature"], 21)
        self.assertEqual(record["normal_temperature"], 22)

    def test_decodes_floor_heating_and_panel_fields_without_addresses(self):
        with tempfile.TemporaryDirectory() as directory:
            capture_path = Path(directory) / "capture.jsonl"
            capture = FloorHeatingDiagnosticCapture(capture_path)

            capture.record_telegram(
                direction="incoming",
                operate_code="ControlFloorHeatingStatusResponse",
                source_aliases=[
                    {
                        "name": "Living floor",
                        "configured_channel": 3,
                        "address": "must not persist",
                    }
                ],
                target_aliases=["broadcast"],
                payload=[248, 0, 1, 1, 22, 23, 20, 18],
            )
            capture.record_telegram(
                direction="outgoing",
                operate_code="ControlPanelAC",
                source_aliases=["home_assistant"],
                target_aliases=[{"name": "Living floor"}],
                payload=[25, 24, 3],
            )
            records = [
                json.loads(line)
                for line in capture_path.read_text(
                    encoding="utf-8"
                ).splitlines()
            ]

        floor_record, panel_record = records
        self.assertEqual(floor_record["result"], 248)
        self.assertEqual(floor_record["enabled"], 1)
        self.assertEqual(floor_record["normal_temperature"], 22)
        self.assertEqual(panel_record["panel_command"], 25)
        self.assertEqual(panel_record["panel_value"], 24)
        self.assertEqual(panel_record["panel_channel"], 3)
        serialized = json.dumps(records).lower()
        self.assertNotIn("address", serialized)
        self.assertNotIn("udp", serialized)
        self.assertNotIn("datagram", serialized)

    def test_write_failure_does_not_escape_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            capture = FloorHeatingDiagnosticCapture(
                Path(directory) / "missing" / "capture.jsonl"
            )

            capture.record_telegram(
                direction="incoming",
                operate_code="ControlPanelACResponse",
                source_aliases=["Enviro"],
                target_aliases=["broadcast"],
                payload=[20, 1, 3],
            )


class FloorHeatingDiagnosticCaptureAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_event_loop_file_io_runs_off_main_thread_and_flushes(self):
        with tempfile.TemporaryDirectory() as directory:
            capture_path = Path(directory) / "capture.jsonl"
            event_loop_thread = threading.get_ident()
            io_threads = []
            original_read_text = Path.read_text
            original_write_text = Path.write_text

            def tracked_read_text(path, *args, **kwargs):
                io_threads.append(threading.get_ident())
                return original_read_text(path, *args, **kwargs)

            def tracked_write_text(path, *args, **kwargs):
                io_threads.append(threading.get_ident())
                return original_write_text(path, *args, **kwargs)

            with (
                patch.object(Path, "read_text", tracked_read_text),
                patch.object(Path, "write_text", tracked_write_text),
            ):
                capture = FloorHeatingDiagnosticCapture(capture_path)
                capture.record_telegram(
                    direction="outgoing",
                    operate_code="ReadPanelAC",
                    source_aliases=["home_assistant"],
                    target_aliases=[{"name": "Living room temperature"}],
                    payload=[20, 6, 6],
                )
                self.assertTrue(
                    hasattr(capture, "async_close"),
                    "async captures must expose a flush-and-close lifecycle",
                )
                await capture.async_close()
                completed_io_count = len(io_threads)
                capture.record_telegram(
                    direction="incoming",
                    operate_code="ReadPanelACResponse",
                    source_aliases=["Enviro"],
                    target_aliases=["home_assistant"],
                    payload=[25, 99, 6],
                )
                self.assertEqual(len(io_threads), completed_io_count)

            self.assertTrue(io_threads)
            self.assertNotIn(event_loop_thread, io_threads)
            record = json.loads(capture_path.read_text(encoding="utf-8"))
            self.assertEqual(record["panel_channel"], 6)


if __name__ == "__main__":
    unittest.main()
