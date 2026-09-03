import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import json
import tempfile
import unittest

from simsetlib.state import (DEFAULT_ROSTER, Manifest, Registry, RosterEntry, StateError,
                             find_project_root, load_manifest, simset_home, write_manifest)


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_manifest_uses_default_roster(self):
        manifest = Manifest.default("triton")
        self.assertEqual(manifest.id, "triton")
        self.assertEqual([e.type for e in manifest.roster], [e["type"] for e in DEFAULT_ROSTER])
        self.assertEqual(manifest.runtime, "latest")

    def test_write_then_load_round_trips(self):
        manifest = Manifest("triton", [RosterEntry("iPhone 17 Pro", "phone"), RosterEntry("iPad Pro 13-inch (M5)")], "26.3")
        write_manifest(self.root, manifest)
        loaded = load_manifest(self.root)
        self.assertEqual(loaded, manifest)
        raw = json.loads((self.root / ".simset.json").read_text())
        self.assertEqual(raw["roster"][1], {"type": "iPad Pro 13-inch (M5)", "alias": None})

    def test_load_missing_manifest_raises(self):
        with self.assertRaises(StateError):
            load_manifest(self.root)

    def test_find_project_root_walks_up(self):
        write_manifest(self.root, Manifest.default("x"))
        nested = self.root / "a" / "b"
        nested.mkdir(parents=True)
        self.assertEqual(find_project_root(nested), self.root)
        self.assertIsNone(find_project_root(Path(tempfile.mkdtemp())))

    def test_alias_resolution_explicit_then_heuristic(self):
        explicit = Manifest.default("x")
        self.assertEqual(explicit.type_for_alias("phone"), "iPhone 17 Pro")
        self.assertEqual(explicit.type_for_alias("phone-small"), "iPhone 16e")
        self.assertEqual(explicit.type_for_alias("tablet"), "iPad Pro 13-inch (M5)")
        self.assertIsNone(explicit.type_for_alias("watch"))
        bare = Manifest("y", [RosterEntry("iPhone 17 Pro Max"), RosterEntry("iPhone 16e"), RosterEntry("iPad mini (A17 Pro)")])
        self.assertEqual(bare.type_for_alias("phone"), "iPhone 17 Pro Max")
        self.assertEqual(bare.type_for_alias("phone-small"), "iPhone 16e")
        self.assertEqual(bare.type_for_alias("tablet"), "iPad mini (A17 Pro)")


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"

    def tearDown(self):
        self.tmp.cleanup()

    def test_simset_home_honors_env_override(self):
        self.assertEqual(simset_home({"SIMSET_HOME": "/tmp/x"}), Path("/tmp/x"))
        self.assertEqual(simset_home({}), Path("~/.simset").expanduser())

    def test_register_and_unregister(self):
        registry = Registry(self.home)
        registry.register("triton", Path("/p/triton"), now="2026-09-03T16:00:00Z")
        self.assertEqual(registry.sets()["triton"], {"project": "/p/triton", "created_at": "2026-09-03T16:00:00Z"})
        registry.register("triton", Path("/p/triton2"), now="2026-09-04T00:00:00Z")
        self.assertEqual(registry.sets()["triton"]["project"], "/p/triton2")
        self.assertEqual(registry.sets()["triton"]["created_at"], "2026-09-03T16:00:00Z")
        registry.unregister("triton")
        self.assertEqual(registry.sets(), {})
        registry.unregister("never-there")


if __name__ == "__main__":
    unittest.main()
