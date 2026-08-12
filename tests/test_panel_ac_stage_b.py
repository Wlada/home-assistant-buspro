import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from test_panel_ac_device import (
    FakeBuspro,
    FakeGeneric,
    OperateCode,
    PanelACDevice,
)


class PanelACStageBProtocolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        FakeGeneric.calls.clear()
        self.buspro = FakeBuspro()

    def tearDown(self):
        self.buspro.loop.close()

    def _device(self, channel=3):
        return PanelACDevice(
            self.buspro,
            (10, 20),
            channel=channel,
            min_temp=18,
            max_temp=28,
        )

    def test_confirmed_fields_update_from_write_and_read_responses(self):
        cases = (
            (3, 1, "is_on", True),
            (4, 24, "cool_target_temperature", 24),
            (5, 2, "fan_mode", "medium"),
            (6, 1, "selected_mode", "heat"),
            (7, 25, "heat_target_temperature", 25),
        )

        for operate_code in (
            OperateCode.ControlPanelACResponse,
            OperateCode.ReadPanelACResponse,
        ):
            for field, value, property_name, expected in cases:
                with self.subTest(
                    operate_code=operate_code,
                    field=field,
                    value=value,
                ):
                    device = self._device()
                    notify = Mock()
                    device._call_device_updated = notify

                    device._telegram_received_cb(
                        SimpleNamespace(
                            operate_code=operate_code,
                            payload=[field, value, 3],
                        )
                    )

                    self.assertEqual(getattr(device, property_name), expected)
                    notify.assert_called_once_with()

    def test_active_target_follows_confirmed_selected_mode(self):
        device = self._device()
        device._call_device_updated = Mock()

        for field, value in ((4, 24), (7, 25), (6, 0)):
            device._telegram_received_cb(
                SimpleNamespace(
                    operate_code=OperateCode.ReadPanelACResponse,
                    payload=[field, value, 3],
                )
            )
        self.assertEqual(device.target_temperature, 24)

        device._telegram_received_cb(
            SimpleNamespace(
                operate_code=OperateCode.ControlPanelACResponse,
                payload=[6, 1, 3],
            )
        )
        self.assertEqual(device.target_temperature, 25)

    def test_repeated_value_does_not_schedule_redundant_callback(self):
        device = self._device()
        notify = Mock()
        device._call_device_updated = notify
        telegram = SimpleNamespace(
            operate_code=OperateCode.ControlPanelACResponse,
            payload=[5, 3, 3],
        )

        device._telegram_received_cb(telegram)
        device._telegram_received_cb(telegram)

        self.assertEqual(device.fan_mode, "high")
        notify.assert_called_once_with()

    def test_auto_fan_clears_advertised_fan_mode(self):
        device = self._device()
        notify = Mock()
        device._call_device_updated = notify

        for value in (3, 0):
            device._telegram_received_cb(
                SimpleNamespace(
                    operate_code=OperateCode.ControlPanelACResponse,
                    payload=[5, value, 3],
                )
            )

        self.assertIsNone(device.fan_mode)
        self.assertEqual(notify.call_count, 2)

    def test_invalid_full_state_values_are_ignored(self):
        device = self._device()
        notify = Mock()
        device._call_device_updated = notify

        for field, value in (
            (3, 2),
            (4, 17),
            (4, 29),
            (5, 4),
            (6, 2),
            (7, 17),
        ):
            with self.subTest(field=field, value=value):
                device._telegram_received_cb(
                    SimpleNamespace(
                        operate_code=OperateCode.ReadPanelACResponse,
                        payload=[field, value, 3],
                    )
                )

        self.assertIsNone(device.is_on)
        self.assertIsNone(device.selected_mode)
        self.assertIsNone(device.cool_target_temperature)
        self.assertIsNone(device.heat_target_temperature)
        self.assertIsNone(device.fan_mode)
        notify.assert_not_called()

    async def test_typed_commands_are_channel_scoped_and_non_optimistic(self):
        device = self._device(channel=2)
        device._is_on = True

        await device.set_mode("cool")
        device._selected_mode = "cool"
        await device.set_target_temperature(23)
        await device.set_fan_mode("high")

        self.assertEqual(
            [(call[2], call[3]) for call in FakeGeneric.calls],
            [
                ([6, 0, 2], OperateCode.ControlPanelAC),
                ([4, 23, 2], OperateCode.ControlPanelAC),
                ([5, 3, 2], OperateCode.ControlPanelAC),
            ],
        )
        self.assertIsNone(device.cool_target_temperature)
        self.assertIsNone(device.fan_mode)

    async def test_controls_reject_off_unknown_and_out_of_range_values(self):
        device = self._device(channel=2)

        with self.assertRaises(RuntimeError):
            await device.set_mode("heat")
        device._is_on = True
        with self.assertRaises(ValueError):
            await device.set_mode("auto")
        with self.assertRaises(RuntimeError):
            await device.set_target_temperature(23)
        device._selected_mode = "heat"
        for temperature in (17, 29, 22.5, True):
            with self.subTest(temperature=temperature):
                with self.assertRaises(ValueError):
                    await device.set_target_temperature(temperature)
        with self.assertRaises(ValueError):
            await device.set_fan_mode("auto")

        self.assertEqual(FakeGeneric.calls, [])

    async def test_read_status_requests_confirmed_fields_for_its_channel(self):
        device = self._device(channel=2)

        await device.read_status()

        self.assertEqual(
            [(call[2], call[3]) for call in FakeGeneric.calls],
            [
                ([3, 2, 2], OperateCode.ReadPanelAC),
                ([4, 2, 2], OperateCode.ReadPanelAC),
                ([5, 2, 2], OperateCode.ReadPanelAC),
                ([6, 2, 2], OperateCode.ReadPanelAC),
                ([7, 2, 2], OperateCode.ReadPanelAC),
            ],
        )


if __name__ == "__main__":
    unittest.main()
