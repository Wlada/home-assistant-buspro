import importlib.util
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace


COMPONENT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PREFIX = "custom_components.buspro.pybuspro"


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_buspro_module():
    pybuspro = types.ModuleType(MODULE_PREFIX)
    pybuspro.__path__ = []
    helpers = types.ModuleType(f"{MODULE_PREFIX}.helpers")
    helpers.__path__ = []
    transport = types.ModuleType(f"{MODULE_PREFIX}.transport")
    transport.__path__ = []
    network_interface = types.ModuleType(
        f"{MODULE_PREFIX}.transport.network_interface"
    )
    network_interface.NetworkInterface = object

    sys.modules.update(
        {
            MODULE_PREFIX: pybuspro,
            f"{MODULE_PREFIX}.helpers": helpers,
            f"{MODULE_PREFIX}.transport": transport,
            f"{MODULE_PREFIX}.transport.network_interface": network_interface,
        }
    )
    enums = _load_module(
        f"{MODULE_PREFIX}.helpers.enums",
        COMPONENT_ROOT / "pybuspro" / "helpers" / "enums.py",
    )
    buspro_module = _load_module(
        f"{MODULE_PREFIX}.buspro",
        COMPONENT_ROOT / "pybuspro" / "buspro.py",
    )
    return buspro_module.Buspro, enums.OperateCode


Buspro, OperateCode = _load_buspro_module()


class RecordingCapture:
    def __init__(self):
        self.records = []

    def record_telegram(self, **record):
        self.records.append(record)


class FailingCapture:
    def __init__(self):
        self.called = False

    def record_telegram(self, **record):
        self.called = True
        raise OSError("diagnostic storage unavailable")


class FloorHeatingZone:
    def __init__(
        self,
        name,
        panel_address,
        panel_channel,
        actuator_address,
        actuator_channel,
    ):
        self._name = name
        self._panel_address = panel_address
        self._panel_channel = panel_channel
        self._actuator_address = actuator_address
        self._actuator_channel = actuator_channel

    def _panel_telegram_received_cb(self, telegram):
        pass

    def _actuator_telegram_received_cb(self, telegram):
        pass


class FloorHeatingCaptureScopeTests(unittest.TestCase):
    def setUp(self):
        self.buspro = Buspro(("gateway", 1), loop_=object())
        self.capture = RecordingCapture()
        self.buspro.floor_heating_diagnostic_capture = self.capture
        self.living = FloorHeatingZone(
            "Living floor", (10, 20), 3, (10, 30), 5
        )
        self.bathroom = FloorHeatingZone(
            "Bathroom floor", (10, 20), 4, (10, 31), 6
        )
        self.buspro.register_telegram_received_device_cb(
            self.living._panel_telegram_received_cb, (10, 20)
        )
        self.buspro.register_telegram_received_device_cb(
            self.living._actuator_telegram_received_cb, (10, 30)
        )
        self.buspro.register_telegram_received_device_cb(
            self.bathroom._panel_telegram_received_cb, (10, 20)
        )
        self.buspro.register_telegram_received_device_cb(
            self.bathroom._actuator_telegram_received_cb, (10, 31)
        )

    def test_incoming_capture_maps_registered_aliases_without_raw_addresses(self):
        telegram = SimpleNamespace(
            source_address=(10, 20),
            target_address=(255, 255),
            operate_code=OperateCode.ControlPanelACResponse,
            payload=[25, 22, 3],
        )

        self.buspro._callback_all_messages(telegram)

        self.assertEqual(
            self.capture.records,
            [
                {
                    "direction": "incoming",
                    "operate_code": "ControlPanelACResponse",
                    "source_aliases": [
                        {"name": "Living floor", "configured_channel": 3},
                        {"name": "Bathroom floor", "configured_channel": 4},
                    ],
                    "target_aliases": ["broadcast"],
                    "payload": [25, 22, 3],
                }
            ],
        )

    def test_actuator_alias_uses_owner_channel_for_two_part_registration(self):
        telegram = SimpleNamespace(
            source_address=(10, 30),
            target_address=(255, 255),
            operate_code=OperateCode.ControlPanelACResponse,
            payload=[20, 1, 5],
        )

        self.buspro._callback_all_messages(telegram)

        self.assertEqual(
            self.capture.records[0]["source_aliases"],
            [{"name": "Living floor", "configured_channel": 5}],
        )

    def test_filters_unrelated_panel_and_sensor_telegrams(self):
        telegrams = (
            SimpleNamespace(
                source_address=(10, 20),
                target_address=(255, 255),
                operate_code=OperateCode.ControlPanelACResponse,
                payload=[3, 1, 3],
            ),
            SimpleNamespace(
                source_address=(10, 20),
                target_address=(255, 255),
                operate_code=OperateCode.ReadSensorStatusResponse,
                payload=[1, 2, 3],
            ),
        )

        for telegram in telegrams:
            self.buspro._callback_all_messages(telegram)

        self.assertEqual(self.capture.records, [])

    def test_capture_failure_does_not_interrupt_dispatch(self):
        received = []
        capture = FailingCapture()
        self.buspro.floor_heating_diagnostic_capture = capture
        self.buspro.register_telegram_received_device_cb(
            received.append, (10, 20)
        )
        telegram = SimpleNamespace(
            source_address=(10, 20),
            target_address=(255, 255),
            operate_code=OperateCode.ControlPanelACResponse,
            payload=[20, 1, 3],
        )

        self.buspro._callback_all_messages(telegram)

        self.assertTrue(capture.called)
        self.assertEqual(received, [telegram])


class FakeTelegramHelper:
    def build_send_buffer(self, telegram):
        return b"serialized"


class FakeUDPClient:
    def __init__(self):
        self.messages = []

    async def send_message(self, message):
        self.messages.append(message)


class OutgoingFloorHeatingCaptureTests(unittest.IsolatedAsyncioTestCase):
    async def test_network_send_captures_outgoing_floor_heating_telegram(self):
        udp_client_module = types.ModuleType(
            f"{MODULE_PREFIX}.transport.udp_client"
        )
        udp_client_module.UDPClient = object
        telegram_helper_module = types.ModuleType(
            f"{MODULE_PREFIX}.helpers.telegram_helper"
        )
        telegram_helper_module.TelegramHelper = FakeTelegramHelper
        sys.modules.update(
            {
                f"{MODULE_PREFIX}.transport.udp_client": udp_client_module,
                f"{MODULE_PREFIX}.helpers.telegram_helper": telegram_helper_module,
            }
        )
        network_module = _load_module(
            f"{MODULE_PREFIX}.transport.floor_heating_test_network_interface",
            COMPONENT_ROOT / "pybuspro" / "transport" / "network_interface.py",
        )
        buspro = Buspro(("gateway", 1), loop_=object())
        capture = RecordingCapture()
        buspro.floor_heating_diagnostic_capture = capture
        target = FloorHeatingZone(
            "Living floor", (10, 20), 3, (10, 30), 5
        )
        buspro.register_telegram_received_device_cb(
            target._panel_telegram_received_cb, (10, 20)
        )
        network = network_module.NetworkInterface.__new__(
            network_module.NetworkInterface
        )
        network.buspro = buspro
        network.gateway_address_send_receive = (("gateway", 1), ("", 1))
        network._th = FakeTelegramHelper()
        network.udp_client = FakeUDPClient()
        telegram = SimpleNamespace(
            source_address=(200, 200),
            target_address=(10, 20),
            operate_code=OperateCode.ControlPanelAC,
            payload=[25, 23, 3],
        )

        await network.send_telegram(telegram)

        self.assertEqual(network.udp_client.messages, [b"serialized"])
        self.assertEqual(
            capture.records,
            [
                {
                    "direction": "outgoing",
                    "operate_code": "ControlPanelAC",
                    "source_aliases": ["home_assistant"],
                    "target_aliases": [
                        {"name": "Living floor", "configured_channel": 3}
                    ],
                    "payload": [25, 23, 3],
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
