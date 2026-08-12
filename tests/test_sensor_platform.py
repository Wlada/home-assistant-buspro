import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace

import voluptuous as vol


COMPONENT_ROOT = Path(__file__).resolve().parents[1]
DATA_BUSPRO = "buspro"


def _load_sensor_platform():
    homeassistant = types.ModuleType("homeassistant")
    components = types.ModuleType("homeassistant.components")
    components.__path__ = []
    sensor_component = types.ModuleType("homeassistant.components.sensor")
    helpers = types.ModuleType("homeassistant.helpers")
    helpers.__path__ = []
    config_validation = types.ModuleType(
        "homeassistant.helpers.config_validation"
    )
    entity_module = types.ModuleType("homeassistant.helpers.entity")
    const = types.ModuleType("homeassistant.const")
    core = types.ModuleType("homeassistant.core")
    custom_components = types.ModuleType("custom_components")
    custom_components.__path__ = []
    buspro_package = types.ModuleType("custom_components.buspro")
    buspro_package.__path__ = []

    sensor_component.PLATFORM_SCHEMA = vol.Schema({})
    config_validation.ensure_list = lambda value: (
        value if isinstance(value, list) else [value]
    )
    config_validation.string = str
    for name, value in {
        "CONF_NAME": "name",
        "CONF_DEVICES": "devices",
        "CONF_ADDRESS": "address",
        "CONF_TYPE": "type",
        "CONF_UNIT_OF_MEASUREMENT": "unit_of_measurement",
        "ILLUMINANCE": "illuminance",
        "TEMPERATURE": "temperature",
        "CONF_DEVICE_CLASS": "device_class",
        "CONF_SCAN_INTERVAL": "scan_interval",
    }.items():
        setattr(const, name, value)
    core.callback = lambda function: function

    class Entity:
        def async_write_ha_state(self):
            return None

    entity_module.Entity = Entity
    buspro_package.DATA_BUSPRO = DATA_BUSPRO

    sys.modules.update(
        {
            "homeassistant": homeassistant,
            "homeassistant.components": components,
            "homeassistant.components.sensor": sensor_component,
            "homeassistant.helpers": helpers,
            "homeassistant.helpers.config_validation": config_validation,
            "homeassistant.helpers.entity": entity_module,
            "homeassistant.const": const,
            "homeassistant.core": core,
            "custom_components": custom_components,
            "custom_components.buspro": buspro_package,
        }
    )

    spec = importlib.util.spec_from_file_location(
        "custom_components.buspro.sensor_under_test",
        COMPONENT_ROOT / "sensor.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


SensorPlatform = _load_sensor_platform()
BusproSensor = SensorPlatform.BusproSensor


class BusproSensorPlatformTests(unittest.TestCase):
    def make_entity(self, sensor_type):
        device = SimpleNamespace(
            name="Test sensor",
            device_identifier="test-device",
            temperature=25,
            brightness=None,
            humidity=None,
        )
        entity = BusproSensor(device, sensor_type, 30, 0)
        entity.hass = SimpleNamespace(
            data={SensorPlatform.DATA_BUSPRO: SimpleNamespace(connected=True)}
        )
        return entity

    def test_humidity_metadata_and_state(self):
        self.assertIn("humidity", SensorPlatform.SENSOR_TYPES)
        entity = self.make_entity("humidity")
        entity._humidity = 63

        self.assertEqual(entity.state, 63)
        self.assertEqual(entity.device_class, "humidity")
        self.assertEqual(entity.unit_of_measurement, "%")
        self.assertTrue(entity.available)

    def test_missing_lux_and_humidity_are_unavailable_but_zero_lux_is_valid(self):
        lux = self.make_entity("illuminance")
        humidity = self.make_entity("humidity")

        self.assertFalse(lux.available)
        self.assertFalse(humidity.available)

        lux._brightness = 0
        self.assertTrue(lux.available)
        self.assertEqual(lux.state, 0)

    def test_update_callback_copies_humidity(self):
        entity = self.make_entity("humidity")
        entity._device.humidity = 58

        asyncio.run(entity.after_update_callback(entity._device))

        self.assertEqual(entity.state, 58)


    def test_setup_passes_sensor_type_as_diagnostic_role(self):
        created = []
        module_name = "custom_components.buspro.pybuspro.devices.sensor"
        fake_module = types.ModuleType(module_name)

        class FakeSensor:
            def __init__(self, *args, **kwargs):
                created.append(kwargs)

        fake_module.Sensor = FakeSensor
        previous = sys.modules.get(module_name)
        sys.modules[module_name] = fake_module
        try:
            hass = SimpleNamespace(
                data={DATA_BUSPRO: SimpleNamespace(hdl=object())}
            )
            config = {
                "devices": [
                    {
                        "address": "1.2",
                        "name": "Test temperature",
                        "type": "temperature",
                        "device": "pir",
                        "offset": 0,
                        "scan_interval": "10",
                    }
                ]
            }
            asyncio.run(
                SensorPlatform.async_setup_platform(
                    hass,
                    config,
                    lambda entities: None,
                )
            )
        finally:
            if previous is None:
                sys.modules.pop(module_name, None)
            else:
                sys.modules[module_name] = previous

        self.assertEqual(created[0]["diagnostic_role"], "temperature")


if __name__ == "__main__":
    unittest.main()
