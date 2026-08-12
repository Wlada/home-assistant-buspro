import importlib.util
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

MODULE_PATH = Path(__file__).parents[1] / 'pybuspro' / 'sensor_diagnostics.py'
SPEC = importlib.util.spec_from_file_location('sensor_diagnostics', MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
SensorDiagnosticCapture = MODULE.SensorDiagnosticCapture


class SensorDiagnosticCaptureTests(unittest.TestCase):
    def test_keeps_only_newest_500_request_records(self):
        with tempfile.TemporaryDirectory() as directory:
            capture_path = Path(directory) / "capture.jsonl"
            capture = SensorDiagnosticCapture(capture_path)

            for index in range(502):
                capture.record_request(
                    name=f"Sensor {index}",
                    device="pir",
                    role="motion",
                    request_profile="motion",
                    operate_code="ReadMotionSensorStatus",
                )

            records = [
                json.loads(line)
                for line in capture_path.read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(len(records), 500)
        self.assertEqual(records[0]["name"], "Sensor 2")
        self.assertEqual(records[-1]["name"], "Sensor 501")
        self.assertEqual(
            set(records[-1]),
            {
                "timestamp",
                "direction",
                "name",
                "device",
                "role",
                "request_profile",
                "operate_code",
            },
        )

    def test_response_record_contains_only_parser_input_and_output(self):
        with tempfile.TemporaryDirectory() as directory:
            capture_path = Path(directory) / "capture.jsonl"
            capture = SensorDiagnosticCapture(capture_path)

            capture.record_response(
                name="Outdoor temperature",
                device="sensors_in_one",
                role="temperature",
                request_profile=None,
                operate_code="ReadSensorsInOneStatusResponse",
                payload=[0, 61, 1, 44, 70, 0, 0, 1],
                temperature=41,
                illuminance=300,
                humidity=70,
                raw_motion=1,
                movement=True,
            )
            record = json.loads(capture_path.read_text(encoding="utf-8"))

        self.assertEqual(
            set(record),
            {
                "timestamp",
                "direction",
                "name",
                "device",
                "role",
                "request_profile",
                "operate_code",
                "payload_length",
                "payload",
                "temperature",
                "illuminance",
                "humidity",
                "raw_motion",
                "movement",
            },
        )
        self.assertEqual(record["payload"], [0, 61, 1, 44, 70, 0, 0, 1])
        self.assertEqual(record["temperature"], 41)
        self.assertNotIn("address", json.dumps(record).lower())
        self.assertNotIn("udp", json.dumps(record).lower())

    def test_raw_response_record_contains_only_safe_protocol_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            capture_path = Path(directory) / "capture.jsonl"
            capture = SensorDiagnosticCapture(capture_path)

            capture.record_raw_response(
                name="Basement temperature",
                device="pir",
                role="temperature",
                operate_code="D993",
                payload=[0, 123],
            )
            record = json.loads(capture_path.read_text(encoding="utf-8"))

        self.assertEqual(
            set(record),
            {
                "timestamp",
                "direction",
                "name",
                "device",
                "role",
                "operate_code",
                "payload_length",
                "payload",
            },
        )
        self.assertEqual(record["direction"], "raw_response")
        self.assertEqual(record["operate_code"], "D993")
        self.assertEqual(record["payload"], [0, 123])
        serialized = json.dumps(record).lower()
        for forbidden in (
            "address",
            "udp",
            "datagram",
            "entity_id",
            "device_id",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_dispatch_record_contains_only_safe_callback_match_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            capture_path = Path(directory) / "capture.jsonl"
            capture = SensorDiagnosticCapture(capture_path)

            capture.record_dispatch(
                operate_code="ReadSensorsInOneStatusResponse",
                candidates=[
                    {
                        "name": "Closet temperature",
                        "device": "sensors_in_one",
                        "role": "temperature",
                        "matched_by": "source",
                        "address": "must not be persisted",
                    },
                    {
                        "name": "Closet Motion",
                        "device": "sensors_in_one",
                        "role": "motion",
                        "matched_by": None,
                    },
                ],
            )
            record = json.loads(capture_path.read_text(encoding="utf-8"))

        self.assertEqual(
            set(record),
            {
                "timestamp",
                "direction",
                "operate_code",
                "candidates",
            },
        )
        self.assertEqual(record["direction"], "dispatch")
        self.assertEqual(record["candidates"][0]["matched_by"], "source")
        self.assertIsNone(record["candidates"][1]["matched_by"])
        serialized = json.dumps(record).lower()
        for forbidden in (
            "address",
            "udp",
            "datagram",
            "entity_id",
            "device_id",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_write_failure_does_not_escape_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            capture = SensorDiagnosticCapture(
                Path(directory) / "missing" / "capture.jsonl"
            )
            capture.record_request(
                name="Basement motion",
                device="pir",
                role="motion",
                request_profile="motion",
                operate_code="ReadMotionSensorStatus",
            )


class SensorDiagnosticCaptureAsyncTests(unittest.IsolatedAsyncioTestCase):
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
                capture = SensorDiagnosticCapture(capture_path)
                capture.record_request(
                    name="Outdoor temperature",
                    device="sensors_in_one",
                    role="temperature",
                    request_profile=None,
                    operate_code="ReadSensorsInOneStatus",
                )
                self.assertTrue(
                    hasattr(capture, "async_close"),
                    "async captures must expose a flush-and-close lifecycle",
                )
                await capture.async_close()
                completed_io_count = len(io_threads)
                capture.record_request(
                    name="Must not persist after close",
                    device="pir",
                    role="motion",
                    request_profile="motion",
                    operate_code="ReadMotionSensorStatus",
                )
                self.assertEqual(len(io_threads), completed_io_count)

            self.assertTrue(io_threads)
            self.assertNotIn(event_loop_thread, io_threads)
            record = json.loads(capture_path.read_text(encoding="utf-8"))
            self.assertEqual(record["name"], "Outdoor temperature")


if __name__ == "__main__":
    unittest.main()
