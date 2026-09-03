import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import json
import unittest
from types import SimpleNamespace

from simsetlib.simctl import Simctl, SimctlError


def fake_run_factory(responses):
    calls = []

    def run(args):
        calls.append(args)
        key = " ".join(args)
        stdout, returncode, stderr = responses.get(key, ("", 0, ""))
        return SimpleNamespace(stdout=stdout, returncode=returncode, stderr=stderr)

    run.calls = calls
    return run


class SimctlTests(unittest.TestCase):
    def test_list_devices_flattens_runtimes_into_each_device(self):
        payload = {"devices": {
            "com.apple.CoreSimulator.SimRuntime.iOS-26-3": [
                {"udid": "A", "name": "iPhone 17 Pro", "state": "Shutdown",
                 "deviceTypeIdentifier": "com.apple.CoreSimulator.SimDeviceType.iPhone-17-Pro",
                 "isAvailable": True}],
            "com.apple.CoreSimulator.SimRuntime.iOS-18-4": []}}
        run = fake_run_factory({"list devices -j": (json.dumps(payload), 0, "")})
        devices = Simctl(run).list_devices()
        self.assertEqual(devices[0]["runtime"], "com.apple.CoreSimulator.SimRuntime.iOS-26-3")
        self.assertEqual(devices[0]["udid"], "A")
        self.assertEqual(run.calls, [["list", "devices", "-j"]])

    def test_create_returns_stripped_udid(self):
        run = fake_run_factory({
            "create [x] iPhone 17 Pro dt rt": ("ABC-123\n", 0, "")})
        self.assertEqual(Simctl(run).create("[x] iPhone 17 Pro", "dt", "rt"), "ABC-123")

    def test_bootstatus_blocks_via_dash_b(self):
        run = fake_run_factory({"bootstatus UDID -b": ("", 0, "")})
        Simctl(run).bootstatus("UDID")
        self.assertEqual(run.calls, [["bootstatus", "UDID", "-b"]])

    def test_nonzero_exit_raises_simctl_error_with_stderr(self):
        run = fake_run_factory({"boot BAD": ("", 149, "Invalid device: BAD")})
        with self.assertRaises(SimctlError) as ctx:
            Simctl(run).boot("BAD")
        self.assertIn("Invalid device", str(ctx.exception))
        self.assertEqual(ctx.exception.stderr, "Invalid device: BAD")


if __name__ == "__main__":
    unittest.main()
