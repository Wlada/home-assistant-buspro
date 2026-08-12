import unittest
from pathlib import Path


COMPONENT_ROOT = Path(__file__).resolve().parents[1]


class PanelACCaptureScopeTests(unittest.TestCase):
    def test_hardcoded_global_capture_blocks_are_removed(self):
        buspro_source = (
            COMPONENT_ROOT / "pybuspro" / "buspro.py"
        ).read_text(encoding="utf-8")
        network_source = (
            COMPONENT_ROOT
            / "pybuspro"
            / "transport"
            / "network_interface.py"
        ).read_text(encoding="utf-8")

        self.assertFalse(
            "Enviro telegram source=" in buspro_source,
            "global parsed Enviro capture block still exists",
        )
        self.assertFalse(
            "Enviro raw datagram" in network_source,
            "global raw Enviro capture block still exists",
        )
        self.assertFalse(
            "data.hex()" in network_source,
            "raw datagram serialization still exists",
        )


if __name__ == "__main__":
    unittest.main()
