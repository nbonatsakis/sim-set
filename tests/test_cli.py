import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import io
import json
import os
import tempfile
import unittest
from unittest import mock

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


class GlobalFlagTests(CliCase):
    def test_project_flag_after_subcommand_resolves_project(self):
        self.run_json("configure")
        elsewhere = Path(self.tmp.name) / "elsewhere"
        elsewhere.mkdir()
        result = self.run_json("list", "--project", str(self.project), cwd=elsewhere)
        self.assertEqual(result["id"], "triton")

    def test_json_flag_before_subcommand_yields_json_output(self):
        self.run_json("configure")
        out = self.run_cli("--json", "list")
        self.assertEqual(json.loads(out)["id"], "triton")


class ClaimTests(CliCase):
    def setUp(self):
        super().setUp()
        self.run_json("configure")

    def test_claim_phone_returns_udid_and_leases_it(self):
        result = self.run_json("claim", "phone", "--label", "onboarding")
        self.assertEqual(result["name"], "[triton] iPhone 17 Pro")
        self.assertEqual(result["type"], "iPhone 17 Pro")
        self.assertEqual(result["lease"]["owner_pid"], os.getpid())
        self.assertEqual(result["lease"]["label"], "onboarding")
        listing = self.run_json("list")
        self.assertEqual(listing["devices"][0]["lease"]["label"], "onboarding")

    def test_claim_boots_when_asked(self):
        result = self.run_json("claim", "tablet", "--boot")
        self.assertEqual(result["state"], "Booted")
        self.assertIn(("boot", result["udid"]), self.fake.calls)

    def test_second_claim_of_same_size_is_contention(self):
        self.env["SIMSET_OWNER_PID"] = "1"
        self.run_json("claim", "phone")
        out = self.run_cli("claim", "phone", expect=3)
        self.assertIn("--grow", out)

    def test_grow_provisions_numbered_extra(self):
        self.env["SIMSET_OWNER_PID"] = "1"
        self.run_json("claim", "phone")
        result = self.run_json("claim", "phone", "--grow")
        self.assertEqual(result["name"], "[triton] iPhone 17 Pro #2")

    def test_claim_exact_type_outside_roster_is_user_error(self):
        out = self.run_cli("claim", "iPhone 17 Pro Max", expect=1)
        self.assertIn("simset add", out)

    def test_wait_polls_until_free(self):
        self.env["SIMSET_OWNER_PID"] = "1"
        first = self.run_json("claim", "phone")
        sleeps = []

        def fake_sleep(seconds):
            sleeps.append(seconds)
            self.run_json("release", first["udid"])

        with mock.patch.object(cli.time, "sleep", fake_sleep):
            result = self.run_json("claim", "phone", "--wait", "10")
        self.assertEqual(result["udid"], first["udid"])
        self.assertEqual(len(sleeps), 1)

    def test_renew_extends_existing_lease(self):
        first = self.run_json("claim", "phone", "--ttl", "1")
        renewed = self.run_json("claim", "--renew", first["udid"], "--ttl", "8")
        self.assertGreater(renewed["lease"]["expires_at"], first["lease"]["expires_at"])


class ReleaseTests(CliCase):
    def setUp(self):
        super().setUp()
        self.run_json("configure")

    def test_release_by_udid_and_mine_and_all(self):
        a = self.run_json("claim", "phone")
        self.assertEqual(self.run_json("release", a["udid"])["released"], [a["udid"]])
        self.run_json("claim", "phone")
        self.run_json("claim", "tablet")
        self.assertEqual(len(self.run_json("release", "--mine")["released"]), 2)
        self.env["SIMSET_OWNER_PID"] = "1"
        self.run_json("claim", "phone")
        self.env["SIMSET_OWNER_PID"] = str(os.getpid())
        self.assertEqual(len(self.run_json("release", "--all")["released"]), 1)

    def test_release_requires_a_target(self):
        out = self.run_cli("release", expect=1)
        self.assertIn("--mine", out)

    def test_leases_lists_and_reaps(self):
        self.env["SIMSET_OWNER_PID"] = "999999"
        self.run_json("claim", "phone")
        listing = self.run_json("leases")
        self.assertEqual(len(listing["leases"]), 1)
        self.assertTrue(listing["leases"][0]["stale"])
        reaped = self.run_json("leases", "--reap")
        self.assertEqual(len(reaped["reaped"]), 1)
        self.assertEqual(self.run_json("leases")["leases"], [])


class LifecycleTests(CliCase):
    def setUp(self):
        super().setUp()
        self.run_json("configure")

    def test_boot_and_shutdown_by_alias_udid_and_all(self):
        booted = self.run_json("boot", "phone")
        self.assertEqual([d["state"] for d in booted["devices"]], ["Booted"])
        udid = booted["devices"][0]["udid"]
        self.assertEqual(self.run_json("shutdown", udid)["devices"][0]["state"], "Shutdown")
        self.assertEqual(len(self.run_json("boot", "all")["devices"]), 3)
        self.assertEqual(len(self.run_json("shutdown", "all")["devices"]), 3)

    def test_boot_refuses_device_outside_set(self):
        foreign = make_device("iPhone 17 Pro", udid="FOREIGN")
        self.fake.devices.append(foreign)
        out = self.run_cli("boot", "FOREIGN", expect=1)
        self.assertIn("outside set", out)
        self.assertNotIn(("boot", "FOREIGN"), self.fake.calls)

    def test_erase_calls_simctl_erase(self):
        result = self.run_json("erase", "tablet")
        self.assertIn(("erase", result["devices"][0]["udid"]), self.fake.calls)

    def test_add_extends_roster_and_provisions(self):
        result = self.run_json("add", "iPhone 17 Pro Max", "--alias", "phone-max")
        self.assertEqual(result["created"][0]["name"], "[triton] iPhone 17 Pro Max")
        manifest = json.loads((self.project / ".simset.json").read_text())
        self.assertEqual(manifest["roster"][-1], {"type": "iPhone 17 Pro Max", "alias": "phone-max"})
        self.assertEqual(self.run_json("claim", "phone-max")["type"], "iPhone 17 Pro Max")

    def test_remove_needs_yes_then_deletes_and_drops_roster(self):
        out = self.run_cli("remove", "phone-small", expect=1)
        self.assertIn("--yes", out)
        self.assertIn("[triton] iPhone 16e", self.fake.names())
        self.run_json("boot", "phone-small")
        result = self.run_json("remove", "phone-small", "--yes")
        self.assertEqual(result["deleted"][0]["name"], "[triton] iPhone 16e")
        self.assertNotIn("[triton] iPhone 16e", self.fake.names())
        manifest = json.loads((self.project / ".simset.json").read_text())
        self.assertEqual([e["type"] for e in manifest["roster"]], ["iPhone 17 Pro", "iPad Pro 13-inch (M5)"])

    def test_destroy_removes_everything(self):
        self.run_json("claim", "phone", "--boot")
        out = self.run_cli("destroy", expect=1)
        self.assertIn("--yes", out)
        result = self.run_json("destroy", "--yes")
        self.assertEqual(len(result["deleted"]), 3)
        self.assertEqual(self.fake.names(), [])
        self.assertFalse((self.project / ".simset.json").exists())
        self.assertNotIn("simset", (self.project / "CLAUDE.md").read_text())
        self.assertEqual(json.loads((self.home / "registry.json").read_text())["sets"], {})
        self.assertEqual(self.run_json("leases")["leases"], [])


class PruneTests(CliCase):
    def setUp(self):
        super().setUp()
        self.fake.devices += [
            make_device("iPhone 17 Pro", udid="KEEP-TYPE"),
            make_device("iPhone 17 Pro Max", udid="GONE", devicetype_id="com.apple.CoreSimulator.SimDeviceType.iPhone-17-Pro-Max"),
            make_device("Booted Thing", udid="BOOTED", state="Booted", devicetype_id="com.apple.CoreSimulator.SimDeviceType.iPhone-17-Pro-Max"),
            make_device("[other] iPhone 16e", udid="MANAGED", devicetype_id=IPHONE_16E),
        ]
        self.run_json("configure")

    def test_prune_is_dry_run_without_yes(self):
        result = self.run_json("prune", "--keep", "iPhone 17 Pro", expect=1)
        self.assertTrue(result["dry_run"])
        self.assertEqual([d["udid"] for d in result["delete"]], ["GONE"])
        self.assertEqual([d["udid"] for d in result["skipped_booted"]], ["BOOTED"])
        self.assertEqual(len(self.fake.devices), 7)

    def test_prune_yes_deletes_unmanaged_only(self):
        result = self.run_json("prune", "--keep", "iPhone 17 Pro", "--yes")
        self.assertEqual([d["udid"] for d in result["deleted"]], ["GONE"])
        self.assertIn(("delete", "GONE"), self.fake.calls)
        names = self.fake.names()
        self.assertIn("[other] iPhone 16e", names)
        self.assertIn("[triton] iPhone 17 Pro", names)
        self.assertIn("iPhone 17 Pro", names)
        self.assertIn("Booted Thing", names)

    def test_prune_shutdown_includes_booted(self):
        result = self.run_json("prune", "--keep", "iPhone 17 Pro", "--shutdown", "--yes")
        self.assertEqual(sorted(d["udid"] for d in result["deleted"]), ["BOOTED", "GONE"])
        self.assertIn(("shutdown", "BOOTED"), self.fake.calls)

    def test_prune_works_without_a_project(self):
        empty = Path(self.tmp.name) / "elsewhere"
        empty.mkdir()
        result = self.run_json("prune", "--keep", "iPhone 17 Pro", cwd=empty, expect=1)
        self.assertTrue(result["dry_run"])


from simsetlib import baguette


class BaguetteTests(unittest.TestCase):
    def test_farm_url_encodes_set_prefix(self):
        self.assertEqual(baguette.farm_url(8421, "triton"), "http://127.0.0.1:8421/farm?q=%5Btriton%5D")
        self.assertEqual(baguette.farm_url(9000), "http://127.0.0.1:9000/farm")

    def test_ensure_running_states(self):
        home = Path(tempfile.mkdtemp())
        self.assertEqual(baguette.ensure_running(8421, home, which=lambda _: None, running=lambda p: False), "missing")
        self.assertEqual(baguette.ensure_running(8421, home, which=lambda _: "/x/baguette", running=lambda p: True), "running")
        spawned = []
        checks = iter([False, False, True])

        class Proc:
            pid = 777

        result = baguette.ensure_running(8421, home, which=lambda _: "/x/baguette",
                                         popen=lambda cmd, **kw: spawned.append(cmd) or Proc(),
                                         running=lambda p: next(checks), sleep=lambda s: None)
        self.assertEqual(result, "started")
        self.assertEqual(spawned, [["/x/baguette", "serve", "--port", "8421"]])
        self.assertEqual((home / "baguette.pid").read_text().strip(), "777")


class UiTests(CliCase):
    def setUp(self):
        super().setUp()
        self.run_json("configure")

    def test_ui_boots_set_and_opens_filtered_farm(self):
        opened = []
        with mock.patch.object(cli.baguette, "ensure_running", lambda port, home: "running"), \
             mock.patch.object(cli.baguette, "open_url", lambda url: opened.append(url)):
            result = self.run_json("ui")
        self.assertEqual(result["url"], "http://127.0.0.1:8421/farm?q=%5Btriton%5D")
        self.assertEqual(opened, [result["url"]])
        self.assertTrue(all(d["state"] == "Booted" for d in self.run_json("list")["devices"]))

    def test_ui_all_opens_unfiltered_and_missing_baguette_fails_after_boot(self):
        with mock.patch.object(cli.baguette, "ensure_running", lambda port, home: "missing"), \
             mock.patch.object(cli.baguette, "open_url", lambda url: None):
            out = self.run_cli("ui", "--all", expect=1)
        self.assertIn("brew", out)
        self.assertTrue(all(d["state"] == "Booted" for d in self.run_json("list")["devices"]))


class DoctorTests(CliCase):
    def test_doctor_reports_checks(self):
        self.run_json("configure")
        self.fake.devices.append(make_device("[orphan] iPhone 17 Pro"))
        with mock.patch.object(cli.baguette, "is_running", lambda port: False), \
             mock.patch.object(cli.shutil, "which", lambda _: None):
            result = self.run_json("doctor", expect=1)
        names = {c["name"]: c for c in result["checks"]}
        self.assertTrue(names["simctl"]["ok"])
        self.assertTrue(names["ios-runtime"]["ok"])
        self.assertFalse(names["baguette"]["ok"])
        self.assertFalse(names["orphan-sets"]["ok"])
        self.assertIn("orphan", names["orphan-sets"]["detail"])
        self.assertTrue(names["leases"]["ok"])

    def test_doctor_probes_query_filter_support_once(self):
        self.run_json("configure")
        calls = []

        def counting_supports_query_filter(port):
            calls.append(port)
            return True

        with mock.patch.object(cli.shutil, "which", lambda _: "/x/baguette"), \
             mock.patch.object(cli.baguette, "is_running", lambda port: True), \
             mock.patch.object(cli.baguette, "supports_query_filter", counting_supports_query_filter):
            result = self.run_json("doctor")
        names = {c["name"]: c for c in result["checks"]}
        self.assertTrue(names["baguette"]["ok"])
        self.assertIn("supported", names["baguette"]["detail"])
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
