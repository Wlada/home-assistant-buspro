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


def _load_cover_platform():
    homeassistant = types.ModuleType("homeassistant")
    components = types.ModuleType("homeassistant.components")
    components.__path__ = []
    cover_component = types.ModuleType("homeassistant.components.cover")
    helpers = types.ModuleType("homeassistant.helpers")
    helpers.__path__ = []
    config_validation = types.ModuleType("homeassistant.helpers.config_validation")
    event_module = types.ModuleType("homeassistant.helpers.event")
    const = types.ModuleType("homeassistant.const")
    core = types.ModuleType("homeassistant.core")
    custom_components = types.ModuleType("custom_components")
    custom_components.__path__ = []
    buspro_package = types.ModuleType("custom_components.buspro")
    buspro_package.__path__ = []

    class CoverEntity:
        def async_write_ha_state(self):
            if getattr(self, "hass", None) is None:
                raise RuntimeError("Attribute hass is None")

        def async_on_remove(self, callback):
            self._remove_callbacks = getattr(self, "_remove_callbacks", [])
            self._remove_callbacks.append(callback)

    class CoverEntityFeature:
        OPEN = 1
        CLOSE = 2
        STOP = 4
        SET_POSITION = 8

    cover_component.CoverEntity = CoverEntity
    cover_component.CoverEntityFeature = CoverEntityFeature
    cover_component.CoverDeviceClass = SimpleNamespace(CURTAIN="curtain")
    cover_component.PLATFORM_SCHEMA = vol.Schema({})
    cover_component.ATTR_POSITION = "position"
    config_validation.string = str
    config_validation.positive_int = int
    config_validation.boolean = bool
    const.CONF_NAME = "name"
    const.CONF_DEVICES = "devices"
    core.callback = lambda function: function
    event_module.async_track_time_interval = lambda *args: (lambda: None)
    buspro_package.DATA_BUSPRO = DATA_BUSPRO

    sys.modules.update(
        {
            "homeassistant": homeassistant,
            "homeassistant.components": components,
            "homeassistant.components.cover": cover_component,
            "homeassistant.helpers": helpers,
            "homeassistant.helpers.config_validation": config_validation,
            "homeassistant.helpers.event": event_module,
            "homeassistant.const": const,
            "homeassistant.core": core,
            "custom_components": custom_components,
            "custom_components.buspro": buspro_package,
        }
    )

    spec = importlib.util.spec_from_file_location(
        "custom_components.buspro.cover_under_test",
        COMPONENT_ROOT / "cover.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CoverPlatform = _load_cover_platform()


class BusproCoverPlatformTests(unittest.IsolatedAsyncioTestCase):
    async def test_cover_reads_and_subscribes_only_after_hass_is_assigned(self):
        created = []
        module_name = "custom_components.buspro.pybuspro.devices"
        fake_module = types.ModuleType(module_name)

        class FakeCover:
            def __init__(self, hdl, address, channel, name):
                self.name = name
                self.device_identifier = "test-cover"
                self._status = None
                self.read_count = 0
                self.callbacks = []
                created.append(self)

            async def read_status(self):
                self.read_count += 1
                self._status = "open"

            def register_device_updated_cb(self, callback):
                self.callbacks.append(callback)

            def unregister_device_updated_cb(self, callback):
                self.callbacks.remove(callback)

        fake_module.Cover = FakeCover
        previous = sys.modules.get(module_name)
        sys.modules[module_name] = fake_module
        added = []
        hass = SimpleNamespace(data={DATA_BUSPRO: SimpleNamespace(hdl=object(), connected=True)})
        config = {
            "devices": {
                "1.2.3": {
                    "name": "Test cover",
                    "opening_time": 20,
                    "adjustable": True,
                }
            }
        }
        try:
            await CoverPlatform.async_setup_platform(hass, config, added.extend)
        finally:
            if previous is None:
                sys.modules.pop(module_name, None)
            else:
                sys.modules[module_name] = previous

        self.assertEqual(created[0].read_count, 0)
        self.assertEqual(created[0].callbacks, [])
        entity = added[0]
        entity.hass = hass

        await entity.async_added_to_hass()

        self.assertEqual(created[0].read_count, 1)
        self.assertEqual(len(created[0].callbacks), 1)

        await created[0].callbacks[0](created[0])
        for remove_callback in entity._remove_callbacks:
            remove_callback()

        self.assertEqual(created[0].callbacks, [])


if __name__ == "__main__":
    unittest.main()
