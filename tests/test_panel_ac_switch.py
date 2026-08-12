import importlib.util
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import voluptuous as vol


COMPONENT_ROOT = Path(__file__).resolve().parents[1]


class StubSwitchEntity:
    def async_write_ha_state(self):
        pass


def _install_home_assistant_stubs():
    homeassistant = types.ModuleType("homeassistant")
    components = types.ModuleType("homeassistant.components")
    switch = types.ModuleType("homeassistant.components.switch")
    helpers = types.ModuleType("homeassistant.helpers")
    config_validation = types.ModuleType(
        "homeassistant.helpers.config_validation"
    )
    const = types.ModuleType("homeassistant.const")
    core = types.ModuleType("homeassistant.core")

    switch.SwitchEntity = StubSwitchEntity
    switch.PLATFORM_SCHEMA = vol.Schema({})
    config_validation.string = str
    const.CONF_NAME = "name"
    const.CONF_DEVICES = "devices"
    core.callback = lambda function: function

    sys.modules.update(
        {
            "homeassistant": homeassistant,
            "homeassistant.components": components,
            "homeassistant.components.switch": switch,
            "homeassistant.helpers": helpers,
            "homeassistant.helpers.config_validation": config_validation,
            "homeassistant.const": const,
            "homeassistant.core": core,
        }
    )


def _load_switch_module():
    _install_home_assistant_stubs()

    custom_components = types.ModuleType("custom_components")
    custom_components.__path__ = []
    buspro_package = types.ModuleType("custom_components.buspro")
    buspro_package.__path__ = [str(COMPONENT_ROOT)]
    buspro_package.DATA_BUSPRO = "buspro"
    sys.modules.update(
        {
            "custom_components": custom_components,
            "custom_components.buspro": buspro_package,
        }
    )

    module_name = "custom_components.buspro.switch"
    spec = importlib.util.spec_from_file_location(
        module_name, COMPONENT_ROOT / "switch.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


buspro_switch = _load_switch_module()


class FakeProtocolDevice:
    def __init__(self, hdl, address, channel, name):
        self.hdl = hdl
        self.address = address
        self.channel = channel
        self.name = name
        self.is_on = None
        self.device_identifier = f"fake-{channel}"
        self.callbacks = []

    def register_device_updated_cb(self, callback):
        self.callbacks.append(callback)

    async def set_on(self):
        pass

    async def set_off(self):
        pass


class FakeRelay(FakeProtocolDevice):
    calls = []

    def __init__(self, hdl, address, channel, name):
        super().__init__(hdl, address, channel, name)
        self.__class__.calls.append((hdl, address, channel, name, self))


class FakePanelAC(FakeProtocolDevice):
    calls = []

    def __init__(self, hdl, address, channel, name):
        super().__init__(hdl, address, channel, name)
        self.__class__.calls.append((hdl, address, channel, name, self))


def _protocol_stubs():
    pybuspro = types.ModuleType("custom_components.buspro.pybuspro")
    pybuspro.__path__ = []
    devices = types.ModuleType("custom_components.buspro.pybuspro.devices")
    devices.__path__ = []
    devices.Switch = FakeRelay
    panel_ac = types.ModuleType(
        "custom_components.buspro.pybuspro.devices.panel_ac"
    )
    panel_ac.PanelACDevice = FakePanelAC
    return {
        "custom_components.buspro.pybuspro": pybuspro,
        "custom_components.buspro.pybuspro.devices": devices,
        "custom_components.buspro.pybuspro.devices.panel_ac": panel_ac,
    }


class PanelACSwitchSchemaTests(unittest.TestCase):
    def test_missing_device_type_defaults_to_relay(self):
        validated = buspro_switch.PLATFORM_SCHEMA(
            {"devices": {"10.20.3": {"name": "Existing relay"}}}
        )

        self.assertEqual(
            validated["devices"]["10.20.3"]["device_type"], "relay"
        )

    def test_panel_ac_and_explicit_relay_types_are_accepted(self):
        for device_type in ("relay", "panel_ac"):
            with self.subTest(device_type=device_type):
                validated = buspro_switch.PLATFORM_SCHEMA(
                    {
                        "devices": {
                            "10.20.3": {
                                "name": "Device",
                                "device_type": device_type,
                            }
                        }
                    }
                )
                self.assertEqual(
                    validated["devices"]["10.20.3"]["device_type"],
                    device_type,
                )

    def test_rejects_unknown_device_type(self):
        with self.assertRaises(vol.Invalid):
            buspro_switch.PLATFORM_SCHEMA(
                {
                    "devices": {
                        "10.20.3": {
                            "name": "Device",
                            "device_type": "unknown",
                        }
                    }
                }
            )

    def test_rejects_invalid_addresses(self):
        invalid_addresses = (
            "10.20",
            "10.20.3.4",
            "a.20.3",
            "-1.20.3",
            "256.20.3",
            "10.-1.3",
            "10.256.3",
            "10.20.0",
            "10.20.256",
        )

        for address in invalid_addresses:
            with self.subTest(address=address):
                with self.assertRaises(vol.Invalid):
                    buspro_switch.PLATFORM_SCHEMA(
                        {"devices": {address: {"name": "Device"}}}
                    )


class PanelACSwitchSetupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        FakeRelay.calls.clear()
        FakePanelAC.calls.clear()
        self.hdl = object()
        self.hass = SimpleNamespace(
            data={
                buspro_switch.DATA_BUSPRO: SimpleNamespace(
                    hdl=self.hdl, connected=True
                )
            }
        )

    async def _setup(self, device_config):
        added = []

        def async_add_entities(entities):
            added.extend(entities)

        config = buspro_switch.PLATFORM_SCHEMA(
            {"devices": {"10.20.3": device_config}}
        )
        with patch.dict(sys.modules, _protocol_stubs()):
            await buspro_switch.async_setup_platform(
                self.hass, config, async_add_entities
            )
        return added

    async def test_missing_type_constructs_existing_relay_entity(self):
        added = await self._setup({"name": "Existing relay"})

        self.assertEqual(len(added), 1)
        self.assertIsInstance(added[0], buspro_switch.BusproSwitch)
        self.assertNotIsInstance(
            added[0], buspro_switch.BusproPanelACSwitch
        )
        self.assertEqual(
            FakeRelay.calls[0][:4],
            (self.hdl, (10, 20), 3, "Existing relay"),
        )
        self.assertEqual(FakePanelAC.calls, [])

    async def test_explicit_relay_constructs_existing_relay_entity(self):
        added = await self._setup(
            {"name": "Explicit relay", "device_type": "relay"}
        )

        self.assertEqual(len(added), 1)
        self.assertIsInstance(added[0], buspro_switch.BusproSwitch)
        self.assertEqual(
            FakeRelay.calls[0][:4],
            (self.hdl, (10, 20), 3, "Explicit relay"),
        )
        self.assertEqual(FakePanelAC.calls, [])
    async def test_panel_ac_constructs_panel_device_and_adapter(self):
        added = await self._setup(
            {"name": "Office AC", "device_type": "panel_ac"}
        )

        self.assertEqual(len(added), 1)
        self.assertIsInstance(
            added[0], buspro_switch.BusproPanelACSwitch
        )
        self.assertEqual(
            FakePanelAC.calls[0][:4],
            (self.hdl, (10, 20), 3, "Office AC"),
        )
        self.assertEqual(FakeRelay.calls, [])


if __name__ == "__main__":
    unittest.main()
