import importlib.util
import logging
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock


COMPONENT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PREFIX = "custom_components.buspro.pybuspro"


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_sensor_module():
    pybuspro = types.ModuleType(MODULE_PREFIX)
    pybuspro.__path__ = []
    devices = types.ModuleType(f"{MODULE_PREFIX}.devices")
    devices.__path__ = []
    helpers = types.ModuleType(f"{MODULE_PREFIX}.helpers")
    helpers.__path__ = []
    control = types.ModuleType(f"{MODULE_PREFIX}.devices.control")
    for name in (
        "_ReadSensorStatus",
        "_ReadStatusOfUniversalSwitch",
        "_ReadStatusOfChannels",
        "_ReadFloorHeatingStatus",
        "_ReadDryContactStatus",
        "_ReadSensorsInOneStatus",
        "_ReadMotionSensorStatus",
        "_ReadTemperature",
    ):
        setattr(control, name, type(name, (), {}))
    device = types.ModuleType(f"{MODULE_PREFIX}.devices.device")
    device.Device = object

    sys.modules.update(
        {
            MODULE_PREFIX: pybuspro,
            f"{MODULE_PREFIX}.devices": devices,
            f"{MODULE_PREFIX}.helpers": helpers,
            f"{MODULE_PREFIX}.devices.control": control,
            f"{MODULE_PREFIX}.devices.device": device,
        }
    )
    enums = _load_module(
        f"{MODULE_PREFIX}.helpers.enums",
        COMPONENT_ROOT / "pybuspro" / "helpers" / "enums.py",
    )
    sensor_module = _load_module(
        f"{MODULE_PREFIX}.devices.sensor",
        COMPONENT_ROOT / "pybuspro" / "devices" / "sensor.py",
    )
    return sensor_module, enums.OperateCode


SensorModule, OperateCode = _load_sensor_module()
Sensor = SensorModule.Sensor
SensorModule._LOGGER.addHandler(logging.NullHandler())
SensorModule._LOGGER.propagate = False


class RecordingCapture:
    def __init__(self):
        self.requests = []
        self.responses = []
        self.raw_responses = []

    def record_request(self, **record):
        self.requests.append(record)

    def record_response(self, **record):
        self.responses.append(record)

    def record_raw_response(self, **record):
        self.raw_responses.append(record)


def make_sensor(device, diagnostic_role="temperature", capture=None):
    sensor = Sensor.__new__(Sensor)
    sensor._device = device
    sensor._diagnostic_role = diagnostic_role
    sensor._buspro = SimpleNamespace(sensor_diagnostic_capture=capture)
    sensor._device_address = (10, 20)
    sensor._name = "Test PIR temperature"
    sensor._request_profile = None
    sensor._current_temperature = None
    sensor._brightness = None
    sensor._humidity = None
    sensor._motion_sensor = None
    sensor._sonic = None
    sensor._dry_contact_1_status = None
    sensor._dry_contact_2_status = None
    sensor._universal_switch_number = None
    sensor._channel_number = None
    sensor._switch_number = None
    sensor._call_device_updated = Mock()
    return sensor


class SensorMotionTests(unittest.TestCase):
    def test_motion_is_unavailable_until_a_valid_response_arrives(self):
        sensor = make_sensor("pir")
        sensor._request_profile = "motion"

        self.assertFalse(sensor.motion_available)

        sensor._telegram_received_cb(
            SimpleNamespace(
                operate_code=OperateCode.ReadMotionSensorStatusResponse,
                payload=[0, 0, 0, 1],
            )
        )

        self.assertTrue(sensor.motion_available)
        self.assertIs(sensor.movement, True)

class SensorTelegramTests(unittest.TestCase):
    def test_sensor_in_one_broadcast_opcode_is_known(self):
        self.assertIn(b"\x16\x30", {item.value for item in OperateCode})

    def test_sensor_in_one_read_decodes_all_exposed_measurements(self):
        sensor = make_sensor("sensors_in_one")
        sensor._telegram_received_cb(
            SimpleNamespace(
                operate_code=OperateCode.ReadSensorsInOneStatusResponse,
                payload=[0xF8, 49, 0x03, 0xE8, 64, 0, 0, 1, 0, 0],
            )
        )

        self.assertEqual(sensor.temperature, 29)
        self.assertEqual(sensor.brightness, 1000)
        self.assertEqual(sensor._humidity, 64)
        self.assertIs(sensor.movement, True)
        sensor._call_device_updated.assert_called_once_with()


    def test_temperature_role_captures_sensor_in_one_parser_input_and_output(self):
        capture = RecordingCapture()
        sensor = make_sensor("sensors_in_one", capture=capture)
        payload = [0xF8, 61, 0x01, 0x2C, 70, 0, 0, 1]

        sensor._telegram_received_cb(
            SimpleNamespace(
                operate_code=OperateCode.ReadSensorsInOneStatusResponse,
                payload=payload,
            )
        )

        self.assertEqual(
            capture.responses,
            [
                {
                    "name": "Test PIR temperature",
                    "device": "sensors_in_one",
                    "role": "temperature",
                    "request_profile": None,
                    "operate_code": "ReadSensorsInOneStatusResponse",
                    "payload": payload,
                    "temperature": 41,
                    "illuminance": 300,
                    "humidity": 70,
                    "raw_motion": 1,
                    "movement": True,
                }
            ],
        )

    def test_non_temperature_role_does_not_duplicate_response_capture(self):
        capture = RecordingCapture()
        sensor = make_sensor(
            "sensors_in_one",
            diagnostic_role="illuminance",
            capture=capture,
        )

        sensor._telegram_received_cb(
            SimpleNamespace(
                operate_code=OperateCode.ReadSensorsInOneStatusResponse,
                payload=[0xF8, 61, 0, 0, 70, 0, 0, 0],
            )
        )

        self.assertEqual(capture.responses, [])

    def test_motion_role_captures_response_for_availability_diagnostic(self):
        capture = RecordingCapture()
        sensor = make_sensor(
            "sensors_in_one",
            diagnostic_role="motion",
            capture=capture,
        )
        payload = [0xF8, 49, 0, 81, 255, 255, 255, 0]

        sensor._telegram_received_cb(
            SimpleNamespace(
                operate_code=OperateCode.ReadSensorsInOneStatusResponse,
                payload=payload,
            )
        )

        self.assertEqual(len(capture.responses), 1)
        self.assertEqual(capture.responses[0]["name"], "Test PIR temperature")
        self.assertEqual(capture.responses[0]["role"], "motion")
        self.assertEqual(capture.responses[0]["payload"], payload)
        self.assertEqual(capture.responses[0]["raw_motion"], 0)
        self.assertIs(capture.responses[0]["movement"], False)
        self.assertTrue(sensor.motion_available)

    def test_temperature_role_captures_unknown_pir_opcode_without_decoding(
        self,
    ):
        capture = RecordingCapture()
        sensor = make_sensor("pir", capture=capture)
        datagram = bytearray(25)
        datagram[21:23] = b"\xD9\x93"

        sensor._telegram_received_cb(
            SimpleNamespace(
                operate_code=None,
                payload=[0, 123],
                udp_data=bytes(datagram),
            )
        )

        self.assertEqual(
            capture.raw_responses,
            [
                {
                    "name": "Test PIR temperature",
                    "device": "pir",
                    "role": "temperature",
                    "operate_code": "D993",
                    "payload": [0, 123],
                }
            ],
        )
        sensor._call_device_updated.assert_not_called()

    def test_non_temperature_role_does_not_duplicate_unknown_pir_capture(
        self,
    ):
        capture = RecordingCapture()
        sensor = make_sensor(
            "pir",
            diagnostic_role="motion",
            capture=capture,
        )
        datagram = bytearray(25)
        datagram[21:23] = b"\xD9\x93"

        sensor._telegram_received_cb(
            SimpleNamespace(
                operate_code=None,
                payload=[0, 123],
                udp_data=bytes(datagram),
            )
        )

        self.assertEqual(capture.raw_responses, [])

    def test_known_or_malformed_telegram_does_not_create_raw_capture(self):
        capture = RecordingCapture()
        sensor = make_sensor("pir", capture=capture)

        sensor._telegram_received_cb(
            SimpleNamespace(
                operate_code=OperateCode.ReadMotionSensorStatusResponse,
                payload=[0, 0, 0, 0],
                udp_data=bytes(25),
            )
        )
        sensor._telegram_received_cb(
            SimpleNamespace(
                operate_code=None,
                payload=[1],
                udp_data=b"\x00",
            )
        )

        self.assertEqual(capture.raw_responses, [])

    def test_sensor_in_one_broadcast_decodes_exposed_measurements(self):
        sensor = make_sensor("sensors_in_one")
        sensor._telegram_received_cb(
            SimpleNamespace(
                operate_code=OperateCode.BroadcastSensorsInOneStatusResponse,
                payload=[0xF8, 47, 0x07, 0xD0, 71, 0, 0, 0, 0, 0],
            )
        )

        self.assertEqual(sensor.temperature, 27)
        self.assertEqual(sensor.brightness, 2000)
        self.assertEqual(sensor.humidity, 71)
        self.assertIs(sensor.movement, False)

    def test_outdoor_encoded_temperature_61_decodes_to_41(self):
        sensor = make_sensor("sensors_in_one")
        sensor._current_temperature = 61

        self.assertEqual(sensor.temperature, 41)

    def test_pir_measurement_diagnostic_omits_device_identifier(self):
        sensor = make_sensor("pir")
        telegram = SimpleNamespace(
            operate_code=OperateCode.ReadMotionSensorStatusResponse,
            payload=[0, 0, 0, 1],
        )

        with self.assertLogs(SensorModule._LOGGER, level="WARNING") as logs:
            sensor._telegram_received_cb(telegram)

        message = "\n".join(logs.output)
        self.assertIn("Test PIR temperature", message)
        self.assertIn("ReadMotionSensorStatusResponse", message)
        self.assertIn("payload_length=4", message)
        self.assertIn("payload=[0, 0, 0, 1]", message)
        self.assertNotIn(str(sensor._device_address), message)

    def test_legacy_pir_read_uses_integer_success_and_16_bit_lux(self):
        sensor = make_sensor("pir")
        sensor._telegram_received_cb(
            SimpleNamespace(
                operate_code=OperateCode.ReadSensorStatusResponse,
                payload=[0xF8, 49, 0x04, 0xB0, 1, 0, 0, 0],
            )
        )

        self.assertEqual(sensor.brightness, 1200)
        self.assertIs(sensor.movement, True)

    def test_seven_byte_pir_read_decodes_supported_measurements(self):
        sensor = make_sensor("pir")
        sensor._telegram_received_cb(
            SimpleNamespace(
                operate_code=OperateCode.ReadSensorStatusResponse,
                payload=[0xF8, 49, 0x04, 0xB0, 1, 0, 1],
            )
        )

        self.assertEqual(sensor.brightness, 1200)
        self.assertIs(sensor.movement, True)
        self.assertIs(sensor.dry_contact_1_is_on, False)
        self.assertIs(sensor.dry_contact_2_is_on, True)

    def test_pir_temperature_applies_protocol_offset(self):
        sensor = make_sensor("pir")
        sensor._telegram_received_cb(
            SimpleNamespace(
                operate_code=OperateCode.ReadSensorStatusResponse,
                payload=[0xF8, 49, 0, 0, 0, 0, 0],
            )
        )

        self.assertEqual(sensor.temperature, 29)

    def test_legacy_broadcasts_decode_16_bit_lux(self):
        for operate_code in (
            OperateCode.BroadcastSensorStatusResponse,
            OperateCode.BroadcastSensorStatusAutoResponse,
        ):
            with self.subTest(operate_code=operate_code):
                sensor = make_sensor("pir")
                sensor._telegram_received_cb(
                    SimpleNamespace(
                        operate_code=operate_code,
                        payload=[49, 0x04, 0xB0, 0, 0, 0, 0],
                    )
                )
                self.assertEqual(sensor.brightness, 1200)

    def test_zero_lux_is_preserved_as_valid_data(self):
        sensor = make_sensor("pir")
        sensor._telegram_received_cb(
            SimpleNamespace(
                operate_code=OperateCode.BroadcastSensorStatusResponse,
                payload=[49, 0, 0, 0, 0, 0, 0],
            )
        )

        self.assertEqual(sensor.brightness, 0)

    def test_short_payload_does_not_erase_last_valid_values(self):
        sensor = make_sensor("sensors_in_one")
        sensor._current_temperature = 45
        sensor._brightness = 900
        sensor._humidity = 55

        try:
            sensor._telegram_received_cb(
                SimpleNamespace(
                    operate_code=OperateCode.ReadSensorsInOneStatusResponse,
                    payload=[0xF8, 30],
                )
            )
        except Exception as error:
            self.fail(f"short payload raised {error!r}")

        self.assertEqual(sensor.temperature, 25)
        self.assertEqual(sensor.brightness, 900)
        self.assertEqual(sensor.humidity, 55)
        sensor._call_device_updated.assert_not_called()

    def test_temperature_channel_decodes_matching_direct_responses(self):
        sensor = make_sensor("temperature_channel")
        sensor._channel_number = 1

        for payload, expected in (
            ([1, 27], 27),
            ([1, 0x85], -5),
            ([1, 29, 0x00, 0x00, 0xE8, 0x41], 29),
        ):
            with self.subTest(payload=payload):
                sensor._call_device_updated.reset_mock()
                sensor._telegram_received_cb(
                    SimpleNamespace(
                        operate_code=OperateCode.ReadTemperatureResponse,
                        payload=payload,
                    )
                )
                self.assertEqual(sensor.temperature, expected)
                sensor._call_device_updated.assert_called_once_with()

    def test_temperature_channel_rejects_wrong_or_malformed_responses(self):
        sensor = make_sensor("temperature_channel")
        sensor._channel_number = 2
        sensor._current_temperature = 24

        for payload in ([], [2], [1, 30]):
            with self.subTest(payload=payload):
                sensor._telegram_received_cb(
                    SimpleNamespace(
                        operate_code=OperateCode.ReadTemperatureResponse,
                        payload=payload,
                    )
                )

        self.assertEqual(sensor.temperature, 24)
        sensor._call_device_updated.assert_not_called()

    def test_temperature_channel_ignores_temperature_broadcast(self):
        sensor = make_sensor("temperature_channel")
        sensor._channel_number = 1
        sensor._current_temperature = 24

        sensor._telegram_received_cb(
            SimpleNamespace(
                operate_code=OperateCode.BroadcastTemperatureResponse,
                payload=[1, 31],
            )
        )

        self.assertEqual(sensor.temperature, 24)
        sensor._call_device_updated.assert_not_called()
class SensorReadRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_motion_profile_uses_motion_status_request(self):
        created = []
        original = SensorModule._ReadMotionSensorStatus

        class Request:
            def __init__(self, buspro):
                self.subnet_id = None
                self.device_id = None
                self.telegram = SimpleNamespace(
                    operate_code=OperateCode.ReadMotionSensorStatus
                )
                created.append("_ReadMotionSensorStatus")

            async def send(self):
                return None

        capture = RecordingCapture()
        try:
            SensorModule._ReadMotionSensorStatus = Request
            sensor = make_sensor("pir", diagnostic_role="motion", capture=capture)
            sensor._request_profile = "motion"
            await sensor.read_sensor_status()
        finally:
            SensorModule._ReadMotionSensorStatus = original

        self.assertEqual(created, ["_ReadMotionSensorStatus"])
        self.assertEqual(
            capture.requests[0]["operate_code"],
            "ReadMotionSensorStatus",
        )
    async def test_uv_suffix_does_not_override_pir_or_sensor_in_one_profile(self):
        created = []
        request_names = (
            "_ReadSensorStatus",
            "_ReadStatusOfUniversalSwitch",
            "_ReadStatusOfChannels",
            "_ReadFloorHeatingStatus",
            "_ReadDryContactStatus",
            "_ReadSensorsInOneStatus",
        )
        originals = {
            name: getattr(SensorModule, name) for name in request_names
        }

        def request_class(name):
            class Request:
                def __init__(self, buspro):
                    self.subnet_id = None
                    self.device_id = None
                    self.switch_number = None
                    created.append(name)

                async def send(self):
                    return None

            Request.__name__ = name
            return Request

        try:
            for name in request_names:
                setattr(SensorModule, name, request_class(name))
            for device, suffix, expected_request in (
                ("pir", 254, "_ReadSensorStatus"),
                ("sensors_in_one", 255, "_ReadSensorsInOneStatus"),
            ):
                with self.subTest(device=device):
                    created.clear()
                    sensor = make_sensor(device)
                    sensor._buspro = object()
                    sensor._channel_number = suffix
                    await sensor.read_sensor_status()
                    self.assertEqual(created, [expected_request])
        finally:
            for name, request in originals.items():
                setattr(SensorModule, name, request)

    async def test_temperature_channel_uses_channel_read_request(self):
        created = []
        original = getattr(SensorModule, "_ReadTemperature", None)
        original_generic = SensorModule._ReadStatusOfChannels

        class Request:
            def __init__(self, buspro):
                self.subnet_id = None
                self.device_id = None
                self.channel_number = None
                created.append(self)

            async def send(self):
                return None

        class GenericRequest(Request):
            pass

        try:
            SensorModule._ReadTemperature = Request
            sensor = make_sensor("temperature_channel")
            SensorModule._ReadStatusOfChannels = GenericRequest
            sensor._buspro = object()
            sensor._channel_number = 3
            await sensor.read_sensor_status()
        finally:
            if original is None:
                del SensorModule._ReadTemperature
            else:
                SensorModule._ReadTemperature = original

            SensorModule._ReadStatusOfChannels = original_generic
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].channel_number, 3)
        self.assertIs(type(created[0]), Request)
        self.assertEqual(
            (created[0].subnet_id, created[0].device_id),
            sensor._device_address,
        )
if __name__ == "__main__":
    unittest.main()
