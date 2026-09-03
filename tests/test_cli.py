import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import io
import json
import os
import tempfile
import unittest

from fake_simctl import FakeSimctl, IPHONE_16E, make_device
from simsetlib import cli


class CliCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.home = base / "home"
        self.project = base / "proj" / "triton"
        self.project.mkdir(parents=True)
        self.fake = FakeSimctl()
        self.env = {"SIMSET_HOME": str(self.home), "SIMSET_OWNER_PID": str(os.getpid())}

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args, cwd=None, expect=0):
        out = io.StringIO()
        code = cli.main(list(args), simctl=self.fake, env=self.env, stdout=out, cwd=str(cwd or self.project))
        self.assertEqual(code, expect, out.getvalue())
        return out.getvalue()

    def run_json(self, *args, cwd=None, expect=0):
        return json.loads(self.run_cli(*args, "--json", cwd=cwd, expect=expect))


class ConfigureTests(CliCase):
    def test_configure_provisions_default_roster_and_writes_files(self):
        result = self.run_json("configure")
        self.assertEqual(result["id"], "triton")
        self.assertEqual([c["name"] for c in result["created"]],
                         ["[triton] iPhone 17 Pro", "[triton] iPhone 16e", "[triton] iPad Pro 13-inch (M5)"])
        self.assertEqual(result["runtime"], "26.3")
        manifest = json.loads((self.project / ".simset.json").read_text())
        self.assertEqual(manifest["id"], "triton")
        self.assertIn("[triton]", (self.project / "CLAUDE.md").read_text())
        registry = json.loads((self.home / "registry.json").read_text())
        self.assertEqual(registry["sets"]["triton"]["project"], str(self.project))

    def test_configure_is_idempotent(self):
        self.run_json("configure")
        again = self.run_json("configure")
        self.assertEqual(again["created"], [])
        self.assertEqual(len(self.fake.devices), 3)

    def test_configure_with_custom_id_roster_and_no_claude_md(self):
        result = self.run_json("configure", "--id", "ck", "--roster", "iPhone 17 Pro Max", "--roster", "iPad Pro 13-inch (M5)", "--no-claude-md")
        self.assertEqual(result["id"], "ck")
        self.assertEqual(self.fake.names(), ["[ck] iPad Pro 13-inch (M5)", "[ck] iPhone 17 Pro Max"])
        self.assertFalse((self.project / "CLAUDE.md").exists())
        manifest = json.loads((self.project / ".simset.json").read_text())
        self.assertEqual(manifest["roster"][0], {"type": "iPhone 17 Pro Max", "alias": "phone"})
        self.assertEqual(manifest["roster"][1], {"type": "iPad Pro 13-inch (M5)", "alias": "tablet"})

    def test_configure_unknown_device_type_is_user_error(self):
        out = self.run_cli("configure", "--roster", "iPhone 99", expect=1)
        self.assertIn("unknown device type", out)
        self.assertFalse((self.project / ".simset.json").exists())


class ListTests(CliCase):
    def test_list_shows_project_devices_with_state(self):
        self.run_json("configure")
        result = self.run_json("list")
        self.assertEqual(result["id"], "triton")
        self.assertEqual(len(result["devices"]), 3)
        row = result["devices"][0]
        self.assertEqual(row["name"], "[triton] iPhone 17 Pro")
        self.assertEqual(row["type"], "iPhone 17 Pro")
        self.assertEqual(row["state"], "Shutdown")
        self.assertIsNone(row["lease"])

    def test_list_all_groups_by_set_and_unmanaged(self):
        self.fake.devices.append(make_device("iPhone 17 Pro"))
        self.fake.devices.append(make_device("[other] iPhone 16e", devicetype_id=IPHONE_16E))
        self.run_json("configure")
        result = self.run_json("list", "--all")
        self.assertEqual(sorted(result["sets"].keys()), ["other", "triton"])
        self.assertEqual([d["name"] for d in result["unmanaged"]], ["iPhone 17 Pro"])

    def test_list_without_manifest_is_user_error(self):
        out = self.run_cli("list", expect=1)
        self.assertIn("simset configure", out)

    def test_project_root_is_found_from_subdirectory(self):
        self.run_json("configure")
        nested = self.project / "packages" / "app"
        nested.mkdir(parents=True)
        result = self.run_json("list", cwd=nested)
        self.assertEqual(result["id"], "triton")


if __name__ == "__main__":
    unittest.main()
