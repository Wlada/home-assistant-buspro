import importlib.util
import sys
import types
import unittest
from pathlib import Path


COMPONENT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PREFIX = "custom_components.buspro.pybuspro"


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_control_module():
    pybuspro = types.ModuleType(MODULE_PREFIX)
    pybuspro.__path__ = []
    core = types.ModuleType(f"{MODULE_PREFIX}.core")
    core.__path__ = []
    devices = types.ModuleType(f"{MODULE_PREFIX}.devices")
    devices.__path__ = []
    helpers = types.ModuleType(f"{MODULE_PREFIX}.helpers")
    helpers.__path__ = []
    sys.modules.update(
        {
            MODULE_PREFIX: pybuspro,
            f"{MODULE_PREFIX}.core": core,
            f"{MODULE_PREFIX}.devices": devices,
            f"{MODULE_PREFIX}.helpers": helpers,
        }
    )
    enums = _load_module(
        f"{MODULE_PREFIX}.helpers.enums",
        COMPONENT_ROOT / "pybuspro" / "helpers" / "enums.py",
    )
    _load_module(
        f"{MODULE_PREFIX}.core.telegram",
        COMPONENT_ROOT / "pybuspro" / "core" / "telegram.py",
    )
    control = _load_module(
        f"{MODULE_PREFIX}.devices.control",
        COMPONENT_ROOT / "pybuspro" / "devices" / "control.py",
    )
    return control, enums.OperateCode


ControlModule, OperateCode = _load_control_module()


class TemperatureChannelControlTests(unittest.TestCase):
    def test_read_temperature_builds_channel_specific_telegram(self):
        request_type = getattr(ControlModule, "_ReadTemperature", None)
        read_code = getattr(OperateCode, "ReadTemperature", None)
        response_code = getattr(
            OperateCode,
            "ReadTemperatureResponse",
            None,
        )

        self.assertIsNotNone(request_type)
        self.assertIsNotNone(read_code)
        self.assertIsNotNone(response_code)

        request = request_type(buspro=None)
        request.subnet_id = 10
        request.device_id = 20
        request.channel_number = 3

        self.assertEqual(request.telegram.operate_code, read_code)
        self.assertEqual(request.telegram.target_address, (10, 20))
        self.assertEqual(request.telegram.payload, [3])
        self.assertEqual(read_code.value, b"\xE3\xE7")
        self.assertEqual(response_code.value, b"\xE3\xE8")
