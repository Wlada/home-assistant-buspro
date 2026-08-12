import asyncio
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


class FakeGeneric:
    calls = []

    def __init__(self, buspro, device_address, payload, operate_code, name=""):
        self.__class__.calls.append(
            (buspro, device_address, payload, operate_code, name)
        )

    async def run(self):
        return None


def _load_panel_ac_module():
    pybuspro = types.ModuleType(MODULE_PREFIX)
    pybuspro.__path__ = []
    devices = types.ModuleType(f"{MODULE_PREFIX}.devices")
    devices.__path__ = []
    helpers = types.ModuleType(f"{MODULE_PREFIX}.helpers")
    helpers.__path__ = []
    control = types.ModuleType(f"{MODULE_PREFIX}.devices.control")
    control._ReadStatusOfChannels = object
    generic = types.ModuleType(f"{MODULE_PREFIX}.devices.generic")
    generic.Generic = FakeGeneric

    sys.modules.update(
        {
            MODULE_PREFIX: pybuspro,
            f"{MODULE_PREFIX}.devices": devices,
            f"{MODULE_PREFIX}.helpers": helpers,
            f"{MODULE_PREFIX}.devices.control": control,
            f"{MODULE_PREFIX}.devices.generic": generic,
        }
    )

    enums = _load_module(
        f"{MODULE_PREFIX}.helpers.enums",
        COMPONENT_ROOT / "pybuspro" / "helpers" / "enums.py",
    )
    _load_module(
        f"{MODULE_PREFIX}.devices.device",
        COMPONENT_ROOT / "pybuspro" / "devices" / "device.py",
    )
    panel_ac = _load_module(
        f"{MODULE_PREFIX}.devices.panel_ac",
        COMPONENT_ROOT / "pybuspro" / "devices" / "panel_ac.py",
    )
    return panel_ac.PanelACDevice, enums.OperateCode


PanelACDevice, OperateCode = _load_panel_ac_module()


class FakeBuspro:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.registrations = []

    def register_telegram_received_device_cb(
        self, callback, device_address, postfix=None
    ):
        self.registrations.append((callback, device_address, postfix))


class PanelACDeviceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        FakeGeneric.calls.clear()
        self.buspro = FakeBuspro()
        self.device = PanelACDevice(
            self.buspro, (10, 20), channel=3, name="Office AC"
        )

    def tearDown(self):
        self.buspro.loop.close()

    def test_initial_state_is_unknown_and_callback_is_registered(self):
        self.assertIsNone(self.device.is_on)
        self.assertEqual(len(self.buspro.registrations), 1)
        callback, address, postfix = self.buspro.registrations[0]
        self.assertEqual(callback, self.device._telegram_received_cb)
        self.assertEqual(address, (10, 20))
        self.assertIsNone(postfix)

    async def test_set_on_sends_typed_control_payload_without_optimism(self):
        await self.device.set_on()

        self.assertEqual(
            FakeGeneric.calls,
            [
                (
                    self.buspro,
                    (10, 20),
                    [3, 1, 3],
                    OperateCode.ControlPanelAC,
                    "Office AC",
                )
            ],
        )
        self.assertEqual(FakeGeneric.calls[0][3].value, b"\xe3\xd8")
        self.assertIsNone(self.device.is_on)

    async def test_set_off_sends_typed_control_payload_without_optimism(self):
        await self.device.set_off()

        self.assertEqual(
            FakeGeneric.calls,
            [
                (
                    self.buspro,
                    (10, 20),
                    [3, 0, 3],
                    OperateCode.ControlPanelAC,
                    "Office AC",
                )
            ],
        )
        self.assertIsNone(self.device.is_on)

    def test_matching_response_updates_state_and_notifies(self):
        notify = Mock()
        self.device._call_device_updated = notify

        self.device._telegram_received_cb(
            SimpleNamespace(
                operate_code=OperateCode.ControlPanelACResponse,
                payload=[3, 1, 3],
            )
        )

        self.assertIs(self.device.is_on, True)
        notify.assert_called_once_with()

    def test_matching_ac_response_emits_sanitized_capture(self):
        self.device._call_device_updated = Mock()
        telegram = SimpleNamespace(
            source_address=(10, 20),
            target_address=(10, 30),
            operate_code=OperateCode.ControlPanelACResponse,
            payload=[3, 1, 3],
        )

        with self.assertLogs("buspro.ac_capture", logging.DEBUG) as logs:
            self.device._telegram_received_cb(telegram)

        message = "\n".join(logs.output)
        self.assertIn("instance=Office AC", message)
        self.assertIn("direction=from_panel", message)
        self.assertIn("opcode=E3D9", message)
        self.assertIn("field=3", message)
        self.assertIn("value=1", message)
        self.assertIn("channel=3", message)
        self.assertIn("payload=[3, 1, 3]", message)
        self.assertNotIn("source=", message)
        self.assertNotIn("target=", message)
        self.assertNotIn("raw=", message)

    def test_capture_ignores_another_ac_channel(self):
        telegram = SimpleNamespace(
            source_address=(10, 20),
            target_address=(10, 30),
            operate_code=OperateCode.ControlPanelACResponse,
            payload=[3, 1, 4],
        )

        with self.assertNoLogs("buspro.ac_capture", logging.DEBUG):
            self.device._telegram_received_cb(telegram)

    def test_capture_ignores_non_ac_opcode(self):
        telegram = SimpleNamespace(
            source_address=(10, 20),
            target_address=(10, 30),
            operate_code=OperateCode.ReadStatusOfChannelsResponse,
            payload=[3, 1, 3],
        )

        with self.assertNoLogs("buspro.ac_capture", logging.DEBUG):
            self.device._telegram_received_cb(telegram)

    def test_read_ac_response_is_captured_without_state_mutation(self):
        notify = Mock()
        self.device._call_device_updated = notify
        telegram = SimpleNamespace(
            source_address=(10, 20),
            target_address=(10, 30),
            operate_code=OperateCode.ReadPanelACResponse,
            payload=[9, 8, 3],
        )

        with self.assertLogs("buspro.ac_capture", logging.DEBUG) as logs:
            self.device._telegram_received_cb(telegram)

        message = "\n".join(logs.output)
        self.assertIn("opcode=E3DB", message)
        self.assertIn("channel=3", message)
        self.assertIsNone(self.device.is_on)
        notify.assert_not_called()

    def test_read_ac_capture_ignores_another_channel(self):
        for operate_code in (
            OperateCode.ReadPanelAC,
            OperateCode.ReadPanelACResponse,
        ):
            with self.subTest(operate_code=operate_code):
                telegram = SimpleNamespace(
                    source_address=(10, 20),
                    target_address=(10, 30),
                    operate_code=operate_code,
                    payload=[9, 8, 4],
                )

                with self.assertNoLogs("buspro.ac_capture", logging.DEBUG):
                    self.device._telegram_received_cb(telegram)

    def test_off_response_updates_state(self):
        notify = Mock()
        self.device._call_device_updated = notify

        self.device._telegram_received_cb(
            SimpleNamespace(
                operate_code=OperateCode.ControlPanelACResponse,
                payload=[3, 0, 3],
            )
        )

        self.assertIs(self.device.is_on, False)
        notify.assert_called_once_with()

    def test_ignores_other_opcode_channel_and_field(self):
        notify = Mock()
        self.device._call_device_updated = notify
        telegrams = (
            SimpleNamespace(
                operate_code=OperateCode.ControlPanelAC,
                payload=[3, 1, 3],
            ),
            SimpleNamespace(
                operate_code=OperateCode.ControlPanelACResponse,
                payload=[3, 1, 4],
            ),
            SimpleNamespace(
                operate_code=OperateCode.ControlPanelACResponse,
                payload=[2, 1, 3],
            ),
        )

        for telegram in telegrams:
            self.device._telegram_received_cb(telegram)

        self.assertIsNone(self.device.is_on)
        notify.assert_not_called()

    def test_malformed_response_is_ignored_without_raising(self):
        notify = Mock()
        self.device._call_device_updated = notify
        malformed_payloads = (None, [], [3], [3, 1], [3, 2, 3], "313")

        for payload in malformed_payloads:
            with self.subTest(payload=payload):
                self.device._telegram_received_cb(
                    SimpleNamespace(
                        operate_code=OperateCode.ControlPanelACResponse,
                        payload=payload,
                    )
                )

        self.assertIsNone(self.device.is_on)
        notify.assert_not_called()

    def test_device_identifier_is_stable(self):
        self.assertEqual(self.device.device_identifier, "panel-ac-10-20-3")

    def test_temperature_reads_are_captured_without_state_mutation(self):
        """Temperature reads are logged but do not mutate panel AC state."""
        cases = (
            (
                OperateCode.ReadTemperature,
                [1],
                "opcode=E3E7 field=1 value=None channel=None",
            ),
            (
                OperateCode.ReadTemperatureResponse,
                [1, 28],
                "opcode=E3E8 field=1 value=28 channel=None",
            ),
        )

        notify = Mock()
        self.device._call_device_updated = notify

        for operate_code, payload, expected_log in cases:
            with self.subTest(operate_code=operate_code):
                telegram = SimpleNamespace(
                    source_address=(10, 20),
                    target_address=(10, 30),
                    operate_code=operate_code,
                    payload=payload,
                )

                with self.assertLogs("buspro.ac_capture", logging.DEBUG) as logs:
                    self.device._telegram_received_cb(telegram)

                message = "\n".join(logs.output)
                self.assertIn(expected_log, message)
                self.assertIsNone(self.device.is_on)
                notify.assert_not_called()


if __name__ == "__main__":
    unittest.main()
