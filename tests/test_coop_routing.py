"""Focused, SDK-free role routing checks. Not a co-op gameplay certification."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "mod" / "AutoPickup" / "capacity_guard.py"
spec = importlib.util.spec_from_file_location("coop_pickup", SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class NetworkRoleTests(unittest.TestCase):
    def test_all_stock_modes(self):
        for mode, expected in ((0, "authority"), (1, "authority"),
                               (2, "authority"), (3, "client")):
            self.assertEqual(module.pickup_network_mode(NS(WorldInfo=NS(NetMode=mode))), expected)

    def test_named_mode(self):
        mode = NS(name="NM_Client")
        self.assertEqual(module.pickup_network_mode(NS(WorldInfo=NS(NetMode=mode))), "client")

    def test_unknown_fails_closed(self):
        for controller in (NS(), NS(WorldInfo=None),
                           NS(WorldInfo=NS(NetMode=4)),
                           NS(WorldInfo=NS(NetMode=object()))):
            self.assertIsNone(module.pickup_network_mode(controller))

if __name__ == "__main__":
    unittest.main()
