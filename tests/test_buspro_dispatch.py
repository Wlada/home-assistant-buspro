import importlib.util
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace


COMPONENT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PREFIX = "custom_components.buspro.pybuspro"


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_buspro_module():
    pybuspro = types.ModuleType(MODULE_PREFIX)
    pybuspro.__path__ = []
    helpers = types.ModuleType(f"{MODULE_PREFIX}.helpers")
    helpers.__path__ = []
    transport = types.ModuleType(f"{MODULE_PREFIX}.transport")
    transport.__path__ = []
    network_interface = types.ModuleType(
        f"{MODULE_PREFIX}.transport.network_interface"
    )
    network_interface.NetworkInterface = object

    sys.modules.update(
        {
            MODULE_PREFIX: pybuspro,
            f"{MODULE_PREFIX}.helpers": helpers,
            f"{MODULE_PREFIX}.transport": transport,
            f"{MODULE_PREFIX}.transport.network_interface": network_interface,
        }
    )
    enums = _load_module(
        f"{MODULE_PREFIX}.helpers.enums",
        COMPONENT_ROOT / "pybuspro" / "helpers" / "enums.py",
    )
    buspro_module = _load_module(
        f"{MODULE_PREFIX}.buspro",
        COMPONENT_ROOT / "pybuspro" / "buspro.py",
    )
    return buspro_module, enums.OperateCode


BusproModule, OperateCode = _load_buspro_module()
Buspro = BusproModule.Buspro


class RecordingCapture:
    def __init__(self):
        self.dispatches = []

    def record_dispatch(self, **record):
        self.dispatches.append(record)


class DiagnosticSensor:
    def __init__(self, name, role):
        self._name = name
        self._device = "sensors_in_one"
        self._diagnostic_role = role
        self.received = []

    def receive(self, telegram):
        self.received.append(telegram)


class BusproDispatchDiagnosticTests(unittest.TestCase):
    def test_records_safe_match_result_for_each_multisensor_callback(self):
        buspro = Buspro(("gateway", 1), loop_=object())
        capture = RecordingCapture()
        buspro.sensor_diagnostic_capture = capture
        temperature = DiagnosticSensor("Closet temperature", "temperature")
        motion = DiagnosticSensor("Closet Motion", "motion")
        buspro.register_telegram_received_device_cb(
            temperature.receive,
            (1, 20),
        )
        buspro.register_telegram_received_device_cb(
            motion.receive,
            (1, 21),
        )
        telegram = SimpleNamespace(
            source_address=(1, 20),
            target_address=(1, 99),
            operate_code=OperateCode.ReadSensorsInOneStatusResponse,
            payload=[0xF8, 49, 0, 52, 255, 255, 255, 0],
            udp_data=b"",
        )

        buspro._callback_all_messages(telegram)

        self.assertEqual(
            capture.dispatches,
            [
                {
                    "operate_code": "ReadSensorsInOneStatusResponse",
                    "candidates": [
                        {
                            "name": "Closet temperature",
                            "device": "sensors_in_one",
                            "role": "temperature",
                            "matched_by": "source",
                        },
                        {
                            "name": "Closet Motion",
                            "device": "sensors_in_one",
                            "role": "motion",
                            "matched_by": None,
                        },
                    ],
                }
            ],
        )
        self.assertEqual(temperature.received, [telegram])
        self.assertEqual(motion.received, [])


if __name__ == "__main__":
    unittest.main()
