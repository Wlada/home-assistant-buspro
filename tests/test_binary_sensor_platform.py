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


def _load_binary_sensor_platform():
    homeassistant = types.ModuleType("homeassistant")
    components = types.ModuleType("homeassistant.components")
    components.__path__ = []
    binary_sensor_component = types.ModuleType(
        "homeassistant.components.binary_sensor"
    )
    helpers = types.ModuleType("homeassistant.helpers")
    helpers.__path__ = []
    config_validation = types.ModuleType(
        "homeassistant.helpers.config_validation"
    )
    const = types.ModuleType("homeassistant.const")
    core = types.ModuleType("homeassistant.core")
    event = types.ModuleType("homeassistant.helpers.event")
    custom_components = types.ModuleType("custom_components")
    custom_components.__path__ = []
    buspro_package = types.ModuleType("custom_components.buspro")
    buspro_package.__path__ = []
    buspro_package.__file__ = str(COMPONENT_ROOT / "__init__.py")

    binary_sensor_component.PLATFORM_SCHEMA = vol.Schema({})
    config_validation.ensure_list = lambda value: (
        value if isinstance(value, list) else [value]
    )
    config_validation.string = str
    for name, value in {
        "CONF_NAME": "name",
        "CONF_DEVICES": "devices",
        "CONF_ADDRESS": "address",
        "CONF_TYPE": "type",
        "CONF_DEVICE_CLASS": "device_class",
        "CONF_SCAN_INTERVAL": "scan_interval",
    }.items():
        setattr(const, name, value)
    core.callback = lambda function: function
    event.async_track_time_interval = lambda *args, **kwargs: lambda: None

    class BinarySensorEntity:
        async def async_added_to_hass(self):
            return None

        def async_on_remove(self, callback):
            self.remove_callback = callback

        def async_write_ha_state(self):
            return None

    binary_sensor_component.BinarySensorEntity = BinarySensorEntity
    buspro_package.DATA_BUSPRO = DATA_BUSPRO

    sys.modules.update(
        {
            "homeassistant": homeassistant,
            "homeassistant.components": components,
            "homeassistant.components.binary_sensor": binary_sensor_component,
            "homeassistant.helpers": helpers,
            "homeassistant.helpers.config_validation": config_validation,
            "homeassistant.const": const,
            "homeassistant.core": core,
            "homeassistant.helpers.event": event,
            "custom_components": custom_components,
            "custom_components.buspro": buspro_package,
        }
    )

    spec = importlib.util.spec_from_file_location(
        "custom_components.buspro.binary_sensor_under_test",
        COMPONENT_ROOT / "binary_sensor.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    sys.modules.pop("custom_components.buspro", None)
    sys.modules.pop("custom_components", None)
    return module


BinarySensorPlatform = _load_binary_sensor_platform()


class BusproBinarySensorPlatformTests(unittest.TestCase):
    def make_entity(self, motion_available):
        device = SimpleNamespace(
            name="Test motion",
            device_identifier="test-motion",
            motion_available=motion_available,
            movement=False,
            register_device_updated_cb=lambda callback: None,
        )
        hass = SimpleNamespace(
            data={
                BinarySensorPlatform.DATA_BUSPRO: SimpleNamespace(
                    connected=True
                )
            }
        )
        return BinarySensorPlatform.BusproBinarySensor(
            hass,
            device,
            BinarySensorPlatform.CONF_MOTION,
            "motion",
            20,
        )

    def test_only_legacy_pir_motion_uses_motion_request_profile(self):
        self.assertEqual(
            BinarySensorPlatform._request_profile("motion", "pir"),
            "motion",
        )
        self.assertIsNone(
            BinarySensorPlatform._request_profile(
                "motion",
                "sensors_in_one",
            )
        )

    def test_motion_requires_first_valid_device_response(self):
        self.assertFalse(self.make_entity(False).available)
        self.assertTrue(self.make_entity(True).available)

    def test_disconnected_bus_is_unavailable_even_with_motion_data(self):
        entity = self.make_entity(True)
        entity._hass.data[BinarySensorPlatform.DATA_BUSPRO].connected = False

        self.assertFalse(entity.available)

    def test_dry_contact_update_requests_fresh_status(self):
        calls = []

        async def read_sensor_status():
            calls.append("read")

        device = SimpleNamespace(
            name="Test smoke detector",
            device_identifier="test-smoke",
            register_device_updated_cb=lambda callback: None,
            read_sensor_status=read_sensor_status,
        )
        hass = SimpleNamespace(
            data={
                BinarySensorPlatform.DATA_BUSPRO: SimpleNamespace(
                    connected=True
                )
            }
        )
        entity = BinarySensorPlatform.BusproBinarySensor(
            hass,
            device,
            BinarySensorPlatform.CONF_DRY_CONTACT,
            "smoke",
            5,
        )

        asyncio.run(entity.async_update())

        self.assertEqual(calls, ["read"])

    def test_scan_interval_schedules_per_entity_refresh(self):
        scheduled = {}

        def track_interval(hass, action, interval, **kwargs):
            scheduled.update(
                hass=hass,
                action=action,
                interval=interval,
                kwargs=kwargs,
            )
            return lambda: None

        original = getattr(
            BinarySensorPlatform,
            "async_track_time_interval",
            None,
        )
        BinarySensorPlatform.async_track_time_interval = track_interval
        try:
            entity = self.make_entity(True)
            self.assertFalse(entity.should_poll)
            asyncio.run(entity.async_added_to_hass())
        finally:
            if original is None:
                del BinarySensorPlatform.async_track_time_interval
            else:
                BinarySensorPlatform.async_track_time_interval = original

        self.assertEqual(scheduled["hass"], entity._hass)
        self.assertEqual(scheduled["interval"].total_seconds(), 20)
        self.assertTrue(scheduled["kwargs"]["cancel_on_shutdown"])


    def test_setup_passes_motion_type_as_diagnostic_role(self):
        created = []
        module_names = (
            "custom_components",
            "custom_components.buspro",
            "custom_components.buspro.pybuspro",
            "custom_components.buspro.pybuspro.devices",
        )
        previous = {name: sys.modules.get(name) for name in module_names}
        custom_components = types.ModuleType(module_names[0])
        custom_components.__path__ = []
        buspro_package = types.ModuleType(module_names[1])
        buspro_package.__path__ = []
        pybuspro_package = types.ModuleType(module_names[2])
        pybuspro_package.__path__ = []
        devices = types.ModuleType(module_names[3])

        class FakeSensor:
            def __init__(self, *args, **kwargs):
                created.append(kwargs)
                self.name = kwargs["name"]
                self.device_identifier = "test-motion"

            def register_device_updated_cb(self, callback):
                return None

        devices.Sensor = FakeSensor
        sys.modules.update(
            {
                module_names[0]: custom_components,
                module_names[1]: buspro_package,
                module_names[2]: pybuspro_package,
                module_names[3]: devices,
            }
        )
        try:
            hass = SimpleNamespace(
                data={DATA_BUSPRO: SimpleNamespace(hdl=object())}
            )
            config = {
                "devices": [
                    {
                        "address": "1.2",
                        "name": "Test motion",
                        "type": "motion",
                        "device": "pir",
                        "device_class": "motion",
                        "scan_interval": "20",
                    }
                ]
            }
            asyncio.run(
                BinarySensorPlatform.async_setup_platform(
                    hass,
                    config,
                    lambda entities: None,
                )
            )
        finally:
            for name, module in previous.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module

        self.assertEqual(created[0]["diagnostic_role"], "motion")


if __name__ == "__main__":
    unittest.main()
