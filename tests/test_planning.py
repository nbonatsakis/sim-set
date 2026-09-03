import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import unittest

from fake_simctl import (DEFAULT_DEVICETYPES, DEFAULT_RUNTIMES, IOS_18_4, IOS_26_3, IPAD_PRO_13,
                         IPHONE_16E, IPHONE_17_PRO, IPHONE_17_PRO_MAX, make_device)
from simsetlib.planning import (PlanningError, grow_op, matching_devices, next_index, plan_provision,
                                plan_prune, resolve_devicetype, resolve_runtime, resolve_type,
                                set_devices, type_names_by_id)
from simsetlib.state import Manifest, RosterEntry


class RuntimeAndTypeTests(unittest.TestCase):
    def test_latest_picks_newest_available_ios(self):
        self.assertEqual(resolve_runtime(DEFAULT_RUNTIMES)["identifier"], IOS_26_3)

    def test_version_prefix_policy(self):
        self.assertEqual(resolve_runtime(DEFAULT_RUNTIMES, "18")["identifier"], IOS_18_4)
        self.assertEqual(resolve_runtime(DEFAULT_RUNTIMES, "18.4")["identifier"], IOS_18_4)
        with self.assertRaises(PlanningError):
            resolve_runtime(DEFAULT_RUNTIMES, "17")

    def test_unavailable_runtimes_are_ignored(self):
        runtimes = [dict(r, isAvailable=False) for r in DEFAULT_RUNTIMES]
        with self.assertRaises(PlanningError):
            resolve_runtime(runtimes)

    def test_devicetype_exact_name(self):
        self.assertEqual(resolve_devicetype(DEFAULT_DEVICETYPES, "iPhone 16e")["identifier"], IPHONE_16E)
        with self.assertRaises(PlanningError):
            resolve_devicetype(DEFAULT_DEVICETYPES, "iPhone 99")

    def test_resolve_type_alias_or_passthrough(self):
        manifest = Manifest.default("x")
        self.assertEqual(resolve_type(manifest, "tablet"), "iPad Pro 13-inch (M5)")
        self.assertEqual(resolve_type(manifest, "iPhone 17 Pro Max"), "iPhone 17 Pro Max")


class ProvisionTests(unittest.TestCase):
    def test_plan_creates_only_missing_roster_devices(self):
        manifest = Manifest.default("triton")
        devices = [make_device("[triton] iPhone 17 Pro"), make_device("[other] iPhone 16e", devicetype_id=IPHONE_16E)]
        ops = plan_provision(manifest, devices, DEFAULT_DEVICETYPES, resolve_runtime(DEFAULT_RUNTIMES))
        self.assertEqual([op.name for op in ops], ["[triton] iPhone 16e", "[triton] iPad Pro 13-inch (M5)"])
        self.assertEqual(ops[0].devicetype_id, IPHONE_16E)
        self.assertEqual(ops[1].devicetype_id, IPAD_PRO_13)
        self.assertEqual(ops[0].runtime_id, IOS_26_3)

    def test_plan_is_empty_when_everything_exists(self):
        manifest = Manifest.default("triton")
        devices = [make_device("[triton] iPhone 17 Pro"), make_device("[triton] iPhone 16e"),
                   make_device("[triton] iPad Pro 13-inch (M5)")]
        self.assertEqual(plan_provision(manifest, devices, DEFAULT_DEVICETYPES, resolve_runtime(DEFAULT_RUNTIMES)), [])

    def test_grow_uses_next_free_index(self):
        manifest = Manifest.default("triton")
        devices = [make_device("[triton] iPhone 17 Pro"), make_device("[triton] iPhone 17 Pro #2")]
        self.assertEqual(next_index(devices, "triton", "iPhone 17 Pro"), 3)
        self.assertEqual(next_index(devices, "triton", "iPhone 16e"), 1)
        op = grow_op(manifest, devices, DEFAULT_DEVICETYPES, resolve_runtime(DEFAULT_RUNTIMES), "iPhone 17 Pro")
        self.assertEqual(op.name, "[triton] iPhone 17 Pro #3")

    def test_set_and_matching_devices(self):
        devices = [make_device("[triton] iPhone 17 Pro #2"), make_device("[triton] iPhone 17 Pro"),
                   make_device("[triton] iPhone 16e"), make_device("[other] iPhone 17 Pro"), make_device("iPhone 17 Pro")]
        self.assertEqual(len(set_devices(devices, "triton")), 3)
        names = [d["name"] for d in matching_devices(devices, "triton", "iPhone 17 Pro")]
        self.assertEqual(names, ["[triton] iPhone 17 Pro", "[triton] iPhone 17 Pro #2"])


class PruneTests(unittest.TestCase):
    def test_prune_keeps_managed_kept_and_booted(self):
        devices = [
            make_device("[triton] iPhone 17 Pro"),
            make_device("iPhone 17 Pro"),
            make_device("iPhone 17 Pro Max", devicetype_id="com.apple.CoreSimulator.SimDeviceType.iPhone-17-Pro-Max"),
            make_device("Old Booted", state="Booted", devicetype_id=IPHONE_17_PRO_MAX),
            make_device("My Custom Name", devicetype_id=IPHONE_16E),
        ]
        names = type_names_by_id(DEFAULT_DEVICETYPES)
        plan = plan_prune(devices, keep=["iPhone 17 Pro", "iPhone 16e"], type_names=names)
        self.assertEqual([d["name"] for d in plan.managed], ["[triton] iPhone 17 Pro"])
        self.assertEqual(sorted(d["name"] for d in plan.kept), ["My Custom Name", "iPhone 17 Pro"])
        self.assertEqual([d["name"] for d in plan.skipped_booted], ["Old Booted"])
        self.assertEqual([d["name"] for d in plan.delete], ["iPhone 17 Pro Max"])

    def test_prune_with_include_booted_deletes_booted(self):
        devices = [make_device("Old Booted", state="Booted")]
        plan = plan_prune(devices, keep=[], type_names={}, include_booted=True)
        self.assertEqual([d["name"] for d in plan.delete], ["Old Booted"])
        self.assertEqual(plan.skipped_booted, [])

    def test_prune_keeps_booted_device_when_type_is_kept(self):
        devices = [make_device("Booted iPhone 17 Pro", state="Booted", devicetype_id=IPHONE_17_PRO)]
        names = type_names_by_id(DEFAULT_DEVICETYPES)
        plan = plan_prune(devices, keep=["iPhone 17 Pro"], type_names=names, include_booted=False)
        self.assertEqual([d["name"] for d in plan.kept], ["Booted iPhone 17 Pro"])
        self.assertEqual(plan.skipped_booted, [])


if __name__ == "__main__":
    unittest.main()
