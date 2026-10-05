import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


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


def _load_panel_floor_heating_module():
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
    panel_floor_heating = _load_module(
        f"{MODULE_PREFIX}.devices.panel_floor_heating",
        COMPONENT_ROOT / "pybuspro" / "devices" / "panel_floor_heating.py",
    )
    return panel_floor_heating.PanelFloorHeatingDevice, enums.OperateCode


PanelFloorHeatingDevice, OperateCode = _load_panel_floor_heating_module()


class FakeBuspro:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.registrations = []

    def register_telegram_received_device_cb(
        self, callback, device_address, postfix=None
    ):
        self.registrations.append((callback, device_address, postfix))


class PanelFloorHeatingDeviceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        FakeGeneric.calls.clear()
        self.buspro = FakeBuspro()
        self.device = PanelFloorHeatingDevice(
            self.buspro,
            panel_address=(10, 20),
            panel_channel=3,
            actuator_address=(10, 30),
            actuator_channel=5,
            name="Living floor",
            status_route=3,
            min_temp=10,
            max_temp=30,
        )

        self.device._call_device_updated = Mock()

    def tearDown(self):
        self.buspro.loop.close()

    def test_device_preserves_legacy_actuator_identifier(self):
        self.assertEqual(self.device.device_identifier, "(10, 30, 5)")

    async def test_set_target_temperature_requires_confirmed_normal_mode(self):
        with self.assertRaises(RuntimeError):
            await self.device.set_target_temperature(22)

        self.assertEqual(FakeGeneric.calls, [])

    async def test_set_target_temperature_writes_only_normal_field(self):
        self.device._mode = 1

        await self.device.set_target_temperature(22)

        self.assertEqual(
            FakeGeneric.calls,
            [
                (
                    self.buspro,
                    (10, 20),
                    [25, 22, 3],
                    OperateCode.ControlPanelAC,
                    "Living floor",
                )
            ],
        )
        self.assertIsNone(self.device.target_temperature)

    async def test_set_target_temperature_rejects_fractional_and_out_of_range_values(self):
        self.device._mode = 1

        for value in (19.5, 9, 31, True):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    await self.device.set_target_temperature(value)

        self.assertEqual(FakeGeneric.calls, [])

    def test_confirmed_panel_responses_update_power_mode_and_target(self):
        notify = Mock()
        self.device._call_device_updated = notify

        for payload in ([20, 1, 3], [21, 1, 3], [25, 22, 3]):
            self.device._panel_telegram_received_cb(
                SimpleNamespace(
                    operate_code=OperateCode.ControlPanelACResponse,
                    payload=payload,
                )
            )

        self.assertIs(self.device.is_on, True)
        self.assertEqual(self.device.mode, 1)
        self.assertEqual(self.device.target_temperature, 22)
        self.assertEqual(notify.call_count, 3)

    def output_response(self, code, payload, address=(10, 30)):
        self.device._actuator_telegram_received_cb(SimpleNamespace(
            operate_code=code, payload=payload, source_address=address,
        ))

    def test_enabled_status_never_claims_an_open_valve(self):
        for code, payload in (
            (OperateCode.ControlPanelACResponse, [20, 1, 5, 0, 0]),
            (OperateCode.ReadPanelACResponse, [20, 5, 1, 1, 24]),
        ):
            self.output_response(code, payload)
            self.assertIsNone(self.device.actuator_is_on)
            self.assertEqual({}, self.device.actuator_outputs)

    def test_actual_outputs_distinguish_closed_downstairs_and_open_upstairs(self):
        self.output_response(OperateCode.ReadActualStatusOfChannelsResponse,
                             [6, 0, 0, 0, 100, 100, 0])
        self.assertIs(self.device.actuator_is_on, True)
        self.assertEqual({'1': False, '2': False, '3': False,
                          '4': True, '5': True, '6': False},
                         self.device.actuator_outputs)
        self.assertEqual('channel_actual', self.device.actuator_feedback_source)

    def test_channel_target_feedback_matches_configured_switch_protocol(self):
        for level, expected in ((0, False), (100, True)):
            self.output_response(OperateCode.ReadStatusOfChannelsResponse,
                                 [6, 0, 0, 0, 0, level, 0])
            self.assertIs(self.device.actuator_is_on, expected)
            self.assertEqual('channel_target', self.device.actuator_feedback_source)

    def test_actual_snapshot_takes_priority_over_target_snapshot(self):
        self.output_response(OperateCode.ReadActualStatusOfChannelsResponse,
                             [6, 0, 0, 0, 0, 0, 0])
        self.output_response(OperateCode.ReadStatusOfChannelsResponse,
                             [6, 100, 100, 100, 100, 100, 100])
        self.assertIs(self.device.actuator_is_on, False)
        self.assertEqual('channel_actual', self.device.actuator_feedback_source)

    def test_missing_wrong_source_and_malformed_outputs_stay_unknown(self):
        for payload in ([], [6, 0], [4, 100, 100, 100, 100],
                        [6, 0, 0, 0, 0, 101, 0], [6, 0, 0, 0, 0, True, 0],
                        [0], ['6', 0, 0, 0, 0, 100, 0]):
            self.output_response(OperateCode.ReadActualStatusOfChannelsResponse, payload)
            self.assertIsNone(self.device.actuator_is_on)
        self.output_response(OperateCode.ReadActualStatusOfChannelsResponse,
                             [6, 100, 100, 100, 100, 100, 100], address=(10, 20))
        self.assertIsNone(self.device.actuator_is_on)

    def test_output_feedback_expires_without_a_fresh_response(self):
        with patch.dict(PanelFloorHeatingDevice._fresh_output_snapshot.__globals__, monotonic=lambda: 10):
            self.output_response(OperateCode.ReadStatusOfChannelsResponse,
                                 [6, 0, 0, 0, 0, 100, 0])
            self.assertIs(self.device.actuator_is_on, True)
        with patch.dict(PanelFloorHeatingDevice._fresh_output_snapshot.__globals__, monotonic=lambda: 100):
            self.assertIsNone(self.device.actuator_is_on)
            self.assertEqual({}, self.device.actuator_outputs)
            self.assertIsNone(self.device.actuator_feedback_source)

    async def test_reads_never_write_enable_or_output_levels(self):
        await self.device.read_status()
        self.assertEqual([
            (self.buspro, (10, 20), [field, 3, 3], OperateCode.ReadPanelAC, 'Living floor')
            for field in (20, 21, 25)
        ] + [
            (self.buspro, (10, 30), [], code, 'Living floor')
            for code in (OperateCode.ReadActualStatusOfChannels, OperateCode.ReadStatusOfChannels)
        ], FakeGeneric.calls)


if __name__ == "__main__":
    unittest.main()
