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

    def async_on_remove(self, callback):
        self.remove_callback = callback


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
    def __init__(self, *args, **kwargs):
        self.args = args


class FakePanelAC:
    def __init__(self, *args, **kwargs):
        self.args = args


class FakePanelFloorHeatingDevice:
    calls = []

    def __init__(
        self,
        buspro,
        panel_address,
        panel_channel,
        actuator_address,
        actuator_channel,
        name="",
        status_route=None,
        min_temp=5,
        max_temp=35,
    ):
        self.__class__.calls.append(self)
        self.buspro = buspro
        self.panel_address = panel_address
        self.panel_channel = panel_channel
        self.actuator_address = actuator_address
        self.actuator_channel = actuator_channel
        self.name = name
        self.status_route = status_route
        self.min_temp = min_temp
        self.max_temp = max_temp
        self.device_identifier = f"{actuator_address + (actuator_channel,)}"
        self.is_on = None
        self.mode = None
        self.target_temperature = None
        self.actuator_is_on = None
        self.normal_temperature = None
        self.day_temperature = None
        self.night_temperature = None
        self.away_temperature = None
        self.callbacks = []
        self.commands = []

    def register_device_updated_cb(self, callback):
        self.callbacks.append(callback)

    async def read_status(self):
        self.commands.append(("read_status",))

    async def set_on(self):
        self.commands.append(("set_on",))

    async def set_off(self):
        self.commands.append(("set_off",))

    async def set_target_temperature(self, temperature):
        self.commands.append(("set_target_temperature", temperature))


def _load_modules():
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
    legacy_floor = types.ModuleType(
        "custom_components.buspro.pybuspro.devices.climate"
    )
    legacy_floor.ControlFloorHeatingStatus = type(
        "ControlFloorHeatingStatus", (), {}
    )
    panel_ac_device = types.ModuleType(
        "custom_components.buspro.pybuspro.devices.panel_ac"
    )
    panel_ac_device.PanelACDevice = FakePanelAC
    panel_floor_device = types.ModuleType(
        "custom_components.buspro.pybuspro.devices.panel_floor_heating"
    )
    panel_floor_device.PanelFloorHeatingDevice = FakePanelFloorHeatingDevice
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
            "custom_components.buspro.pybuspro.devices.climate": legacy_floor,
            "custom_components.buspro.pybuspro.devices.panel_ac": panel_ac_device,
            "custom_components.buspro.pybuspro.devices.panel_floor_heating": panel_floor_device,
            "custom_components.buspro.pybuspro.helpers.enums": enums,
        }
    )

    adapter_name = "custom_components.buspro.panel_floor_heating_climate"
    adapter_spec = importlib.util.spec_from_file_location(
        adapter_name, COMPONENT_ROOT / "panel_floor_heating_climate.py"
    )
    adapter = importlib.util.module_from_spec(adapter_spec)
    sys.modules[adapter_name] = adapter
    adapter_spec.loader.exec_module(adapter)

    climate_name = "custom_components.buspro.climate"
    climate_spec = importlib.util.spec_from_file_location(
        climate_name, COMPONENT_ROOT / "climate.py"
    )
    climate = importlib.util.module_from_spec(climate_spec)
    sys.modules[climate_name] = climate
    climate_spec.loader.exec_module(climate)
    sys.modules.pop("custom_components.buspro", None)
    return climate, adapter


buspro_climate, panel_adapter = _load_modules()


class PanelFloorHeatingClimateSchemaTests(unittest.TestCase):
    def test_schema_accepts_panel_floor_heating_fields(self):
        device = {
            "address": "10.30.5",
            "panel_address": "10.20.3",
            "name": "Living floor",
            "device_type": "panel_floor_heating",
            "temperature_entity": "sensor.living_temperature",
            "status_route": 3,
            "min_temp": 10,
            "max_temp": 30,
        }

        validated = buspro_climate.PLATFORM_SCHEMA({"devices": [device]})

        validated_device = validated["devices"][0]
        for key, value in device.items():
            self.assertEqual(validated_device[key], value)

    def test_schema_rejects_missing_panel_address_and_invalid_routes(self):
        invalid_devices = (
            {
                "address": "10.30.5",
                "name": "Living floor",
                "device_type": "panel_floor_heating",
                "min_temp": 10,
                "max_temp": 30,
            },
            {
                "address": "10.30",
                "panel_address": "10.20.3",
                "name": "Living floor",
                "device_type": "panel_floor_heating",
                "min_temp": 10,
                "max_temp": 30,
            },
            {
                "address": "10.30.5",
                "panel_address": "10.20.0",
                "name": "Living floor",
                "device_type": "panel_floor_heating",
                "min_temp": 10,
                "max_temp": 30,
            },
            {
                "address": "10.30.5",
                "panel_address": "10.20.3",
                "name": "Living floor",
                "device_type": "panel_floor_heating",
                "status_route": 256,
                "min_temp": 10,
                "max_temp": 30,
            },
        )

        for device in invalid_devices:
            with self.subTest(device=device):
                with self.assertRaises(vol.Invalid):
                    buspro_climate.PLATFORM_SCHEMA({"devices": [device]})


class PanelFloorHeatingClimateEntityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.states = {
            "sensor.living_temperature": types.SimpleNamespace(state="21.5")
        }
        self.hass = types.SimpleNamespace(
            data={"buspro": types.SimpleNamespace(connected=True)},
            states=types.SimpleNamespace(get=self.states.get),
        )
        self.device = FakePanelFloorHeatingDevice(
            object(), (10, 20), 3, (10, 30), 5, name="Living floor"
        )
        self.entity = panel_adapter.BusproPanelFloorHeatingClimate(
            self.hass, self.device, "sensor.living_temperature"
        )

    async def test_entity_set_temperature_does_not_change_hvac_mode(self):
        self.device.is_on = True

        await self.entity.async_set_temperature(temperature=23)

        self.assertEqual(
            self.device.commands, [("set_target_temperature", 23)]
        )
        self.assertEqual(self.entity.hvac_mode, HVACMode.HEAT)

    def test_entity_uses_configured_temperature_sensor(self):
        self.assertEqual(self.entity.current_temperature, 21.5)

        self.states["sensor.living_temperature"] = types.SimpleNamespace(
            state="unavailable"
        )
        self.assertIsNone(self.entity.current_temperature)

    def test_entity_exposes_confirmed_target_and_power_state(self):
        self.device.is_on = True
        self.device.mode = 1
        self.device.target_temperature = 22
        self.device.actuator_is_on = False

        self.assertEqual(self.entity.target_temperature, 22)
        self.assertEqual(self.entity.hvac_mode, HVACMode.HEAT)
        self.assertEqual(self.entity.hvac_action, HVACAction.IDLE)
        self.assertEqual(self.entity.unique_id, "(10, 30, 5)")


if __name__ == "__main__":
    unittest.main()
