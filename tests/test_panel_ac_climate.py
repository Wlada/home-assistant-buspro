import asyncio
import importlib.util
import sys
import types
import unittest
from enum import IntFlag
from pathlib import Path

import voluptuous as vol


COMPONENT_ROOT = Path(__file__).resolve().parents[1]


class StubClimateEntity:
    def async_write_ha_state(self):
        self.write_count = getattr(self, "write_count", 0) + 1


class ClimateEntityFeature(IntFlag):
    TARGET_TEMPERATURE = 1
    PRESET_MODE = 2
    TURN_OFF = 4
    TURN_ON = 8
    FAN_MODE = 16


class HVACMode:
    OFF = "off"
    COOL = "cool"
    HEAT = "heat"


class HVACAction:
    OFF = "off"
    HEATING = "heating"
    IDLE = "idle"


class UnitOfTemperature:
    CELSIUS = "°C"


def _ensure_list(value):
    return value if isinstance(value, list) else [value]


def _install_home_assistant_stubs():
    homeassistant = types.ModuleType("homeassistant")
    components = types.ModuleType("homeassistant.components")
    climate = types.ModuleType("homeassistant.components.climate")
    helpers = types.ModuleType("homeassistant.helpers")
    config_validation = types.ModuleType(
        "homeassistant.helpers.config_validation"
    )
    event = types.ModuleType("homeassistant.helpers.event")
    const = types.ModuleType("homeassistant.const")
    core = types.ModuleType("homeassistant.core")

    climate.PLATFORM_SCHEMA = vol.Schema({})
    climate.ClimateEntity = StubClimateEntity
    climate.ClimateEntityFeature = ClimateEntityFeature
    climate.HVACMode = HVACMode
    climate.HVACAction = HVACAction
    config_validation.ensure_list = _ensure_list
    config_validation.string = str
    event.async_track_state_change_event = lambda *args: lambda: None
    const.CONF_NAME = "name"
    const.CONF_DEVICES = "devices"
    const.CONF_ADDRESS = "address"
    const.UnitOfTemperature = UnitOfTemperature
    const.ATTR_TEMPERATURE = "temperature"
    core.callback = lambda function: function

    sys.modules.update(
        {
            "homeassistant": homeassistant,
            "homeassistant.components": components,
            "homeassistant.components.climate": climate,
            "homeassistant.helpers": helpers,
            "homeassistant.helpers.config_validation": config_validation,
            "homeassistant.helpers.event": event,
            "homeassistant.const": const,
            "homeassistant.core": core,
        }
    )


class FakeFloorClimate:
    def __init__(self, *args):
        self.args = args


class FakeSensor:
    calls = []

    def __init__(
        self,
        hdl,
        address,
        channel_number=None,
        device=None,
        name="",
        **kwargs,
    ):
        self.hdl = hdl
        self.address = address
        self.channel_number = channel_number
        self.device = device
        self.name = name
        self.temperature = None
        self.callbacks = []
        self.__class__.calls.append(self)

    def register_device_updated_cb(self, callback):
        self.callbacks.append(callback)


class FakePanelAC:
    calls = []

    def __init__(
        self,
        hdl,
        address,
        channel,
        name="",
        min_temp=None,
        max_temp=None,
    ):
        self.hdl = hdl
        self.address = address
        self.channel = channel
        self.name = name
        self.min_temp = min_temp
        self.max_temp = max_temp
        self.device_identifier = f"panel-ac-{channel}"
        self.is_on = None
        self.selected_mode = None
        self.target_temperature = None
        self.fan_mode = None
        self.callbacks = []
        self.commands = []
        self.__class__.calls.append(self)

    def register_device_updated_cb(self, callback):
        self.callbacks.append(callback)

    async def read_status(self):
        self.commands.append(("read_status",))

    async def set_on(self):
        self.commands.append(("set_on",))

    async def set_off(self):
        self.commands.append(("set_off",))

    async def set_mode(self, mode):
        self.commands.append(("set_mode", mode))

    async def set_target_temperature(self, temperature):
        self.commands.append(("set_target_temperature", temperature))

    async def set_fan_mode(self, fan_mode):
        self.commands.append(("set_fan_mode", fan_mode))


def _load_climate_module():
    _install_home_assistant_stubs()

    custom_components = types.ModuleType("custom_components")
    custom_components.__path__ = [str(COMPONENT_ROOT.parent)]
    buspro_package = types.ModuleType("custom_components.buspro")
    buspro_package.__path__ = [str(COMPONENT_ROOT)]
    buspro_package.__file__ = str(COMPONENT_ROOT / "__init__.py")
    buspro_package.DATA_BUSPRO = "buspro"
    pybuspro = types.ModuleType("custom_components.buspro.pybuspro")
    pybuspro.__path__ = []
    devices = types.ModuleType("custom_components.buspro.pybuspro.devices")
    devices.__path__ = []
    devices.Climate = FakeFloorClimate
    devices.Sensor = FakeSensor
    floor_climate = types.ModuleType(
        "custom_components.buspro.pybuspro.devices.climate"
    )
    floor_climate.ControlFloorHeatingStatus = type(
        "ControlFloorHeatingStatus", (), {}
    )
    panel_ac = types.ModuleType(
        "custom_components.buspro.pybuspro.devices.panel_ac"
    )
    panel_ac.PanelACDevice = FakePanelAC
    enums = types.ModuleType("custom_components.buspro.pybuspro.helpers.enums")
    enums.OnOffStatus = types.SimpleNamespace(
        OFF=types.SimpleNamespace(value=0),
        ON=types.SimpleNamespace(value=1),
    )

    sys.modules.update(
        {
            "custom_components": custom_components,
            "custom_components.buspro": buspro_package,
            "custom_components.buspro.pybuspro": pybuspro,
            "custom_components.buspro.pybuspro.devices": devices,
            "custom_components.buspro.pybuspro.devices.climate": floor_climate,
            "custom_components.buspro.pybuspro.devices.panel_ac": panel_ac,
            "custom_components.buspro.pybuspro.helpers.enums": enums,
        }
    )

    module_name = "custom_components.buspro.climate"
    spec = importlib.util.spec_from_file_location(
        module_name, COMPONENT_ROOT / "climate.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    sys.modules.pop("custom_components.buspro", None)
    return module


buspro_climate = _load_climate_module()


class PanelACClimateSchemaTests(unittest.TestCase):
    def test_missing_device_type_preserves_floor_heating_default(self):
        validated = buspro_climate.PLATFORM_SCHEMA(
            {"devices": [{"address": "10.20", "name": "Floor"}]}
        )

        self.assertEqual(validated["devices"][0]["device_type"], "floor_heating")

    def test_panel_ac_accepts_explicit_temperature_contract(self):
        validated = buspro_climate.PLATFORM_SCHEMA(
            {
                "devices": [
                    {
                        "address": "10.20.2",
                        "name": "AC-2",
                        "device_type": "panel_ac",
                        "temperature_channel": 1,
                        "min_temp": 18,
                        "max_temp": 28,
                    }
                ]
            }
        )

        self.assertEqual(validated["devices"][0]["min_temp"], 18)
        self.assertEqual(validated["devices"][0]["max_temp"], 28)

    def test_panel_ac_rejects_missing_or_invalid_contract(self):
        invalid_devices = (
            {"address": "10.20.1", "name": "AC", "device_type": "panel_ac"},
            {
                "address": "10.20",
                "name": "AC",
                "device_type": "panel_ac",
                "temperature_channel": 1,
                "min_temp": 18,
                "max_temp": 28,
            },
            {
                "address": "10.20.1",
                "name": "AC",
                "device_type": "panel_ac",
                "temperature_channel": 0,
                "min_temp": 18,
                "max_temp": 28,
            },
            {
                "address": "10.20.1",
                "name": "AC",
                "device_type": "panel_ac",
                "temperature_channel": 1,
                "min_temp": 28,
                "max_temp": 18,
            },
        )

        for device in invalid_devices:
            with self.subTest(device=device):
                with self.assertRaises(vol.Invalid):
                    buspro_climate.PLATFORM_SCHEMA({"devices": [device]})


class PanelACClimateSetupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        FakePanelAC.calls.clear()
        FakeSensor.calls.clear()
        self._previous_buspro_package = sys.modules.get(
            "custom_components.buspro"
        )
        buspro_package = types.ModuleType("custom_components.buspro")
        buspro_package.__path__ = [str(COMPONENT_ROOT)]
        buspro_package.__file__ = str(COMPONENT_ROOT / "__init__.py")
        buspro_package.DATA_BUSPRO = "buspro"
        sys.modules["custom_components.buspro"] = buspro_package
        devices = sys.modules["custom_components.buspro.pybuspro.devices"]
        devices.Climate = FakeFloorClimate
        devices.Sensor = FakeSensor
        panel_ac = types.ModuleType(
            "custom_components.buspro.pybuspro.devices.panel_ac"
        )
        panel_ac.PanelACDevice = FakePanelAC
        sys.modules[
            "custom_components.buspro.pybuspro.devices.panel_ac"
        ] = panel_ac
        self.hass = types.SimpleNamespace(
            data={"buspro": types.SimpleNamespace(hdl=object(), connected=True)}
        )
    def tearDown(self):
        if self._previous_buspro_package is None:
            sys.modules.pop("custom_components.buspro", None)
        else:
            sys.modules[
                "custom_components.buspro"
            ] = self._previous_buspro_package


    async def _setup_two(self):
        added = []
        config = buspro_climate.PLATFORM_SCHEMA(
            {
                "devices": [
                    {
                        "address": f"10.20.{channel}",
                        "name": f"AC-{channel}",
                        "device_type": "panel_ac",
                        "temperature_channel": 1,
                        "min_temp": 18,
                        "max_temp": 28,
                    }
                    for channel in (1, 2)
                ]
            }
        )
        await buspro_climate.async_setup_platform(
            self.hass, config, added.extend
        )
        return added

    async def test_setup_constructs_two_ac_entities_with_one_shared_sensor(self):
        added = await self._setup_two()

        self.assertEqual(len(added), 2)
        self.assertTrue(
            all(
                isinstance(entity, buspro_climate.BusproPanelACClimate)
                for entity in added
            )
        )
        self.assertEqual([device.channel for device in FakePanelAC.calls], [1, 2])
        self.assertEqual(len(FakeSensor.calls), 1)
        self.assertIs(added[0]._temperature_sensor, added[1]._temperature_sensor)

    async def test_entity_exposes_reduced_confirmed_capabilities(self):
        entity = (await self._setup_two())[0]
        device = entity._device
        sensor = entity._temperature_sensor
        device.is_on = True
        device.selected_mode = "cool"
        device.target_temperature = 23
        device.fan_mode = "medium"
        sensor.temperature = 27

        self.assertEqual(entity.hvac_modes, ["off", "cool", "heat"])
        self.assertEqual(entity.hvac_mode, "cool")
        self.assertEqual(entity.target_temperature, 23)
        self.assertEqual(entity.current_temperature, 27)
        self.assertEqual(entity.fan_modes, ["low", "medium", "high"])
        self.assertEqual(entity.fan_mode, "medium")
        self.assertEqual(entity.min_temp, 18)
        self.assertEqual(entity.max_temp, 28)
        self.assertEqual(
            entity._attr_supported_features,
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.FAN_MODE
            | ClimateEntityFeature.TURN_OFF
            | ClimateEntityFeature.TURN_ON,
        )
        self.assertFalse(entity._enable_turn_on_off_backwards_compatibility)
        self.assertFalse(hasattr(entity, "swing_modes"))

    async def test_commands_delegate_without_optimistic_state_writes(self):
        entity = (await self._setup_two())[0]
        device = entity._device
        device.is_on = True
        device.selected_mode = "cool"

        await entity.async_set_hvac_mode("heat")
        await entity.async_set_temperature(temperature=24)
        await entity.async_set_fan_mode("high")
        await entity.async_turn_off()
        await entity.async_turn_on()

        self.assertEqual(
            device.commands,
            [
                ("set_mode", "heat"),
                ("set_target_temperature", 24),
                ("set_fan_mode", "high"),
                ("set_off",),
                ("set_on",),
            ],
        )
        self.assertEqual(getattr(entity, "write_count", 0), 0)

    async def test_selecting_stored_mode_from_off_turns_ac_on(self):
        entity = (await self._setup_two())[0]
        device = entity._device
        device.is_on = False
        device.selected_mode = "cool"

        await entity.async_set_hvac_mode("cool")

        self.assertEqual(device.commands, [("set_on",)])

    async def test_selecting_different_mode_from_off_waits_for_power(self):
        entity = (await self._setup_two())[0]
        device = entity._device
        device.is_on = False
        device.selected_mode = "cool"

        change_mode = asyncio.create_task(
            entity.async_set_hvac_mode("heat")
        )
        await asyncio.sleep(0)
        self.assertEqual(device.commands, [("set_on",)])

        device.is_on = True
        for callback in device.callbacks:
            await callback(device)
        await change_mode

        self.assertEqual(
            device.commands,
            [("set_on",), ("set_mode", "heat")],
        )


if __name__ == "__main__":
    unittest.main()
