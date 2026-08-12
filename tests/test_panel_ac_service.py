import asyncio
import importlib
import importlib.util
import logging
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import voluptuous as vol


CONFIG_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(CONFIG_ROOT))


def _install_home_assistant_stubs():
    homeassistant = types.ModuleType("homeassistant")
    helpers = types.ModuleType("homeassistant.helpers")
    config_validation = types.ModuleType(
        "homeassistant.helpers.config_validation"
    )
    const = types.ModuleType("homeassistant.const")
    core = types.ModuleType("homeassistant.core")
    config_entries = types.ModuleType("homeassistant.config_entries")

    config_validation.positive_int = vol.All(
        vol.Coerce(int), vol.Range(min=0)
    )
    config_validation.string = str
    config_validation.port = vol.All(
        vol.Coerce(int), vol.Range(min=1, max=65535)
    )
    const.CONF_HOST = "host"
    const.CONF_PORT = "port"
    const.CONF_NAME = "name"
    const.EVENT_HOMEASSISTANT_STOP = "homeassistant_stop"
    core.HomeAssistant = object
    config_entries.ConfigEntry = object

    sys.modules.update(
        {
            "homeassistant": homeassistant,
            "homeassistant.helpers": helpers,
            "homeassistant.helpers.config_validation": config_validation,
            "homeassistant.const": const,
            "homeassistant.core": core,
            "homeassistant.config_entries": config_entries,
        }
    )


_install_home_assistant_stubs()
buspro = importlib.import_module("custom_components.buspro")

_enum_spec = importlib.util.spec_from_file_location(
    "buspro_test_enums",
    Path(buspro.__file__).parent / "pybuspro" / "helpers" / "enums.py",
)
_test_enums = importlib.util.module_from_spec(_enum_spec)
_enum_spec.loader.exec_module(_test_enums)
OperateCode = _test_enums.OperateCode


def _protocol_stubs(generic_class):
    pybuspro = types.ModuleType("custom_components.buspro.pybuspro")
    pybuspro.__path__ = []
    devices = types.ModuleType("custom_components.buspro.pybuspro.devices")
    devices.__path__ = []
    helpers = types.ModuleType("custom_components.buspro.pybuspro.helpers")
    helpers.__path__ = []
    generic = types.ModuleType(
        "custom_components.buspro.pybuspro.devices.generic"
    )
    generic.Generic = generic_class
    enums = types.ModuleType(
        "custom_components.buspro.pybuspro.helpers.enums"
    )
    enums.OperateCode = OperateCode
    return {
        "custom_components.buspro.pybuspro": pybuspro,
        "custom_components.buspro.pybuspro.devices": devices,
        "custom_components.buspro.pybuspro.devices.generic": generic,
        "custom_components.buspro.pybuspro.helpers": helpers,
        "custom_components.buspro.pybuspro.helpers.enums": enums,
    }


class FakeServices:
    def __init__(self):
        self.registered = {}

    def async_register(self, domain, service, handler, schema):
        self.registered[(domain, service)] = (handler, schema)


class PanelACServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.hass = SimpleNamespace(services=FakeServices())
        self.module = buspro.BusproModule.__new__(buspro.BusproModule)
        self.module.hass = self.hass
        self.module.hdl = object()

    def test_buspro_module_attaches_one_shared_sensor_capture(self):
        class FakeBuspro:
            def __init__(self, gateway, loop):
                self.gateway = gateway
                self.loop = loop

        protocol = types.ModuleType(
            "custom_components.buspro.pybuspro.buspro"
        )
        protocol.Buspro = FakeBuspro
        pybuspro_package = sys.modules.get("custom_components.buspro.pybuspro")
        previous_path = None
        if pybuspro_package is not None:
            previous_path = pybuspro_package.__path__
            pybuspro_package.__path__ = [
                str(Path(buspro.__file__).parent / "pybuspro")
            ]

        with tempfile.TemporaryDirectory() as directory:
            hass = SimpleNamespace(
                loop=object(),
                config=SimpleNamespace(
                    path=lambda name: str(Path(directory) / name)
                ),
            )
            try:
                with patch.dict(
                    sys.modules,
                    {"custom_components.buspro.pybuspro.buspro": protocol},
                ):
                    module = buspro.BusproModule(hass, "example", 1234)
            finally:
                if pybuspro_package is not None:
                    pybuspro_package.__path__ = previous_path

            capture = module.hdl.sensor_diagnostic_capture
            self.assertEqual(capture.path.name, "buspro_sensor_capture.jsonl")

    def test_registers_set_panel_ac_service(self):
        self.module.register_services()

        self.assertIn(
            (buspro.DOMAIN, buspro.SERVICE_BUSPRO_SET_PANEL_AC),
            self.hass.services.registered,
        )

    async def test_yaml_and_config_entry_share_one_gateway_instance(self):
        instances = []

        class FakeBusproModule:
            def __init__(self, hass, host, port):
                self.gateway_address_send_receive = ((host, port), ("", port))
                self.start_count = 0
                self.register_count = 0
                instances.append(self)

            async def start(self):
                self.start_count += 1

            def register_services(self):
                self.register_count += 1

        hass = SimpleNamespace(data={})
        config = {buspro.DOMAIN: {"host": "192.168.1.200", "port": 6000}}
        entry = SimpleNamespace(data={"host": "192.168.1.200", "port": 6000})

        with patch.object(buspro, "BusproModule", FakeBusproModule):
            self.assertTrue(await buspro.async_setup(hass, config))
            self.assertTrue(await buspro.async_setup_entry(hass, entry))

        self.assertEqual(len(instances), 1)
        self.assertEqual(instances[0].start_count, 1)
        self.assertEqual(instances[0].register_count, 1)

    async def test_concurrent_setup_starts_only_one_gateway_instance(self):
        instances = []
        start_entered = asyncio.Event()
        release_start = asyncio.Event()

        class FakeBusproModule:
            def __init__(self, hass, host, port):
                self.gateway_address_send_receive = ((host, port), ("", port))
                self.register_count = 0
                instances.append(self)

            async def start(self):
                start_entered.set()
                await release_start.wait()

            def register_services(self):
                self.register_count += 1

        hass = SimpleNamespace(data={})
        config = {buspro.DOMAIN: {"host": "192.168.1.200", "port": 6000}}
        entry = SimpleNamespace(data={"host": "192.168.1.200", "port": 6000})

        with patch.object(buspro, "BusproModule", FakeBusproModule):
            yaml_setup = asyncio.create_task(buspro.async_setup(hass, config))
            await start_entered.wait()
            entry_setup = asyncio.create_task(buspro.async_setup_entry(hass, entry))
            await asyncio.sleep(0)
            try:
                self.assertEqual(len(instances), 1)
            finally:
                release_start.set()
                results = await asyncio.gather(yaml_setup, entry_setup)

        self.assertEqual(results, [True, True])
        self.assertEqual(instances[0].register_count, 1)

    async def test_second_different_gateway_endpoint_is_rejected(self):
        class FakeBusproModule:
            def __init__(self, hass, host, port):
                self.gateway_address_send_receive = ((host, port), ("", port))

            async def start(self):
                return None

            def register_services(self):
                return None

        hass = SimpleNamespace(data={})
        first = {buspro.DOMAIN: {"host": "192.168.1.200", "port": 6000}}
        second = SimpleNamespace(data={"host": "192.168.1.201", "port": 6000})

        with patch.object(buspro, "BusproModule", FakeBusproModule):
            self.assertTrue(await buspro.async_setup(hass, first))
            self.assertFalse(await buspro.async_setup_entry(hass, second))

        self.assertEqual(
            hass.data[buspro.DATA_BUSPRO].gateway_address_send_receive[0],
            ("192.168.1.200", 6000),
        )

    async def test_stop_flushes_both_diagnostic_capture_writers(self):
        closed = []

        class FakeCapture:
            def __init__(self, name):
                self.name = name

            async def async_close(self):
                closed.append(self.name)

        class FakeHdl:
            sensor_diagnostic_capture = FakeCapture("sensor")
            floor_heating_diagnostic_capture = FakeCapture("floor")

            async def stop(self):
                closed.append("hdl")

        self.module.hdl = FakeHdl()

        await self.module.stop(None)

        self.assertEqual(closed[0], "hdl")
        self.assertCountEqual(closed[1:], ["sensor", "floor"])

    async def test_failed_start_stops_partial_module_before_propagating(self):
        stopped = []

        class FakeBusproModule:
            def __init__(self, hass, host, port):
                self.gateway_address_send_receive = ((host, port), ("", port))

            async def start(self):
                raise RuntimeError("gateway start failed")

            async def stop(self, event):
                stopped.append(event)

        hass = SimpleNamespace(data={})
        config = {buspro.DOMAIN: {"host": "192.168.1.200", "port": 6000}}

        with patch.object(buspro, "BusproModule", FakeBusproModule):
            with self.assertRaisesRegex(RuntimeError, "gateway start failed"):
                await buspro.async_setup(hass, config)

        self.assertEqual(stopped, [None])
        self.assertNotIn(buspro.DATA_BUSPRO, hass.data)

    async def test_stop_flushes_captures_when_hdl_stop_fails(self):
        closed = []

        class FakeCapture:
            def __init__(self, name):
                self.name = name

            async def async_close(self):
                closed.append(self.name)

        class FakeHdl:
            sensor_diagnostic_capture = FakeCapture("sensor")
            floor_heating_diagnostic_capture = FakeCapture("floor")

            async def stop(self):
                raise RuntimeError("transport stop failed")

        self.module.hdl = FakeHdl()
        self.module.connected = True

        with self.assertRaisesRegex(RuntimeError, "transport stop failed"):
            await self.module.stop(None)

        self.assertCountEqual(closed, ["sensor", "floor"])
        self.assertFalse(self.module.connected)

    async def test_diagnostic_close_failure_does_not_block_other_cleanup(self):
        closed = []

        class FakeCapture:
            def __init__(self, name, fails=False):
                self.name = name
                self.fails = fails

            async def async_close(self):
                closed.append(self.name)
                if self.fails:
                    raise OSError("diagnostic flush failed")

        class FakeHdl:
            sensor_diagnostic_capture = FakeCapture("sensor", fails=True)
            floor_heating_diagnostic_capture = FakeCapture("floor")

            async def stop(self):
                return None

        self.module.hdl = FakeHdl()
        self.module.connected = True

        await self.module.stop(None)

        self.assertCountEqual(closed, ["sensor", "floor"])
        self.assertFalse(self.module.connected)

    async def test_on_sends_control_panel_ac_payload(self):
        calls = []

        class FakeGeneric:
            def __init__(
                self, hdl, address, payload, operate_code, name
            ):
                calls.append(
                    (hdl, address, payload, operate_code, name)
                )

            async def run(self):
                return None

        with (
            patch.dict(sys.modules, _protocol_stubs(FakeGeneric)),
            self.assertLogs(
                "custom_components.buspro", level=logging.DEBUG
            ) as logs,
        ):
            await self.module.service_set_panel_ac(
                SimpleNamespace(
                    data={"address": [1, 49], "channel": 1, "power": 1}
                )
            )

        self.assertEqual(
            calls,
            [
                (
                    self.module.hdl,
                    [1, 49],
                    [3, 1, 1],
                    OperateCode.ControlPanelAC,
                    buspro.DEFAULT_SEND_MESSAGE_NAME,
                )
            ],
        )
        self.assertEqual(calls[0][3].value, b"\xe3\xd8")
        self.assertIn(
            "address=[1, 49] channel=1 power=1",
            "\n".join(logs.output),
        )

    async def test_off_sends_control_panel_ac_payload(self):
        calls = []

        class FakeGeneric:
            def __init__(
                self, hdl, address, payload, operate_code, name
            ):
                calls.append((address, payload, operate_code))

            async def run(self):
                return None

        with patch.dict(sys.modules, _protocol_stubs(FakeGeneric)):
            await self.module.service_set_panel_ac(
                SimpleNamespace(
                    data={"address": [1, 49], "channel": 1, "power": 0}
                )
            )

        self.assertEqual(
            calls,
            [([1, 49], [3, 0, 1], OperateCode.ControlPanelAC)],
        )

    async def test_send_message_converts_known_opcode_before_transport(self):
        calls = []

        class FakeGeneric:
            def __init__(
                self, hdl, address, payload, operate_code, name
            ):
                calls.append((address, payload, operate_code))

            async def run(self):
                return None

        self.module.register_services()
        handler, schema = self.hass.services.registered[
            (buspro.DOMAIN, buspro.SERVICE_BUSPRO_SEND_MESSAGE)
        ]
        with patch.dict(sys.modules, _protocol_stubs(FakeGeneric)):
            validated = schema(
                {
                    "address": [1, 49],
                    "operate_code": [0xE3, 0xD8],
                    "payload": [3, 1, 1],
                }
            )
            await handler(SimpleNamespace(data=validated))

        self.assertEqual(
            calls,
            [([1, 49], [3, 1, 1], OperateCode.ControlPanelAC)],
        )
        self.assertEqual(calls[0][2].value, b"\xe3\xd8")

    def test_send_message_schema_rejects_malformed_or_unknown_opcode(self):
        invalid_opcodes = (
            [0xE3],
            [0xE3, 0xD8, 0],
            [0x12, 0x34],
            [-1, 0],
            [256, 0],
            "E3D8",
        )

        with patch.dict(sys.modules, _protocol_stubs(object)):
            for operate_code in invalid_opcodes:
                with self.subTest(operate_code=operate_code):
                    with self.assertRaises(vol.Invalid):
                        buspro.SERVICE_BUSPRO_SEND_MESSAGE_SCHEMA(
                            {
                                "address": [1, 49],
                                "operate_code": operate_code,
                                "payload": [3, 1, 1],
                            }
                        )

    def test_universal_switch_schema_accepts_only_zero_or_one(self):
        for status in (0, 1):
            with self.subTest(status=status):
                validated = buspro.SERVICE_BUSPRO_UNIVERSAL_SWITCH_SCHEMA(
                    {
                        "address": [1, 49],
                        "switch_number": 1,
                        "status": status,
                    }
                )
                self.assertEqual(validated["status"], status)

        for status in (-1, 2, True, "on"):
            with self.subTest(status=status):
                with self.assertRaises(vol.Invalid):
                    buspro.SERVICE_BUSPRO_UNIVERSAL_SWITCH_SCHEMA(
                        {
                            "address": [1, 49],
                            "switch_number": 1,
                            "status": status,
                        }
                    )

    def test_schema_rejects_invalid_values(self):
        invalid_data = (
            {"address": [1], "channel": 1, "power": 1},
            {"address": [1, 49, 2], "channel": 1, "power": 1},
            {"address": [-1, 49], "channel": 1, "power": 1},
            {"address": [1, 256], "channel": 1, "power": 1},
            {"address": [1, 49], "channel": 0, "power": 1},
            {"address": [1, 49], "channel": 1, "power": -1},
            {"address": [1, 49], "channel": 1, "power": 2},
        )

        for data in invalid_data:
            with self.subTest(data=data):
                with self.assertRaises(vol.Invalid):
                    buspro.SERVICE_BUSPRO_SET_PANEL_AC_SCHEMA(data)


if __name__ == "__main__":
    unittest.main()
