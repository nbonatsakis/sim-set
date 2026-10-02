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

from fake_simctl import FakeSimctl, IOS_18_4, IOS_26_3, IPHONE_17E, IPHONE_17_PRO_MAX, make_device
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

    def _invoke(self, args, cwd):
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(list(args), simctl=self.fake, env=self.env, stdout=out, stderr=err, cwd=str(cwd or self.project),
                        sleep=lambda seconds: None)
        return code, out.getvalue(), err.getvalue()

    def run_cli(self, *args, cwd=None, expect=0):
        code, out, err = self._invoke(args, cwd)
        self.assertEqual(code, expect, out + err)
        return out + err

    def run_json(self, *args, cwd=None, expect=0):
        code, out, err = self._invoke(list(args) + ["--json"], cwd)
        self.assertEqual(code, expect, out + err)
        return json.loads(out)


class ConfigureTests(CliCase):
    def test_configure_provisions_default_roster_and_writes_files(self):
        result = self.run_json("configure")
        self.assertEqual(result["id"], "triton")
        self.assertEqual([c["name"] for c in result["created"]],
                         ["[triton] iPhone 17 Pro", "[triton] iPhone 17e", "[triton] iPad Pro 13-inch (M5)"])
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

    def test_configure_with_mismatched_markers_is_user_error(self):
        from simsetlib.claudemd import START
        (self.project / "CLAUDE.md").write_text(f"# P\n\n{START}\nold\n")
        out = self.run_cli("configure", expect=1)
        self.assertIn("marker", out)


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
        self.fake.devices.append(make_device("[other] iPhone 17e", devicetype_id=IPHONE_17E))
        self.run_json("configure")
        result = self.run_json("list", "--all")
        self.assertEqual(sorted(result["sets"].keys()), ["other", "triton"])
        self.assertEqual([d["name"] for d in result["unmanaged"]], ["iPhone 17 Pro"])

    def test_list_without_manifest_is_user_error(self):
        out = self.run_cli("list", expect=1)
        self.assertIn("simset configure", out)

    def test_list_json_in_unconfigured_dir_emits_json_error(self):
        empty = Path(self.tmp.name) / "empty-project"
        empty.mkdir()
        result = self.run_json("list", cwd=empty, expect=1)
        self.assertEqual(result["exit_code"], 1)
        self.assertIn("simset configure", result["error"])

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
        self.fake.calls = []

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
        self.assertIn(("bootstatus", result["udid"]), self.fake.calls)

    def test_second_claim_of_same_size_is_contention(self):
        self.env["SIMSET_OWNER_PID"] = "1"
        self.run_json("claim", "phone")
        out = self.run_cli("claim", "phone", expect=3)
        self.assertIn("--grow", out)

    def test_claim_json_contention_emits_json_error(self):
        self.env["SIMSET_OWNER_PID"] = "1"
        self.run_json("claim", "phone")
        result = self.run_json("claim", "phone", expect=3)
        self.assertEqual(result["exit_code"], 3)
        self.assertIn("leased", result["error"])

    def test_grow_provisions_numbered_extra(self):
        self.env["SIMSET_OWNER_PID"] = "1"
        self.run_json("claim", "phone")
        result = self.run_json("claim", "phone", "--grow")
        self.assertEqual(result["name"], "[triton] iPhone 17 Pro #2")

    def test_grow_caps_at_five_attempts(self):
        with mock.patch.object(cli.Leases, "claim", return_value=None):
            out = self.run_cli("claim", "phone", "--grow", expect=1)
        self.assertIn("gave up", out)
        self.assertEqual(len([c for c in self.fake.calls if c[0] == "create"]), 5)

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

    def test_wait_before_grow_claims_original_device_without_growing(self):
        self.env["SIMSET_OWNER_PID"] = "1"
        first = self.run_json("claim", "phone")
        sleeps = []

        def fake_sleep(seconds):
            sleeps.append(seconds)
            self.run_json("release", first["udid"])

        with mock.patch.object(cli.time, "sleep", fake_sleep):
            result = self.run_json("claim", "phone", "--wait", "10", "--grow")
        self.assertEqual(result["udid"], first["udid"])
        self.assertEqual(len(sleeps), 1)
        self.assertEqual([c for c in self.fake.calls if c[0] == "create"], [])

    def test_claim_releases_lease_when_device_vanishes(self):
        with mock.patch.object(cli, "find_device", return_value=None):
            out = self.run_cli("claim", "phone", expect=1)
        self.assertIn("disappeared", out)
        self.assertEqual(self.run_json("leases")["leases"], [])

    def test_claim_shows_owner_source_env(self):
        result = self.run_json("claim", "phone")
        self.assertEqual(result["lease"]["owner_source"], "env")

    def test_claim_warns_on_parent_pid_owner_source(self):
        del self.env["SIMSET_OWNER_PID"]
        with mock.patch.object(cli, "find_owner_pid", return_value=(12345, "parent-pid")):
            out = self.run_cli("claim", "phone")
            self.assertIn("SIMSET_OWNER_PID", out)
            result = self.run_json("claim", "tablet")
        self.assertIn("SIMSET_OWNER_PID", result["warning"])

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
        self.assertIn(("bootstatus", udid), self.fake.calls)
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
        self.assertIn("[triton] iPhone 17e", self.fake.names())
        self.run_json("boot", "phone-small")
        result = self.run_json("remove", "phone-small", "--yes")
        self.assertEqual(result["deleted"][0]["name"], "[triton] iPhone 17e")
        self.assertNotIn("[triton] iPhone 17e", self.fake.names())
        manifest = json.loads((self.project / ".simset.json").read_text())
        self.assertEqual([e["type"] for e in manifest["roster"]], ["iPhone 17 Pro", "iPad Pro 13-inch (M5)"])

    def test_remove_preserves_roster_entry_deleted_outside_simset(self):
        tablet = next(d for d in self.fake.devices if d["name"] == "[triton] iPad Pro 13-inch (M5)")
        self.fake.devices.remove(tablet)
        self.run_json("remove", "phone-small", "--yes")
        manifest = json.loads((self.project / ".simset.json").read_text())
        self.assertIn("iPad Pro 13-inch (M5)", [e["type"] for e in manifest["roster"]])

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
            make_device("[other] iPhone 17e", udid="MANAGED", devicetype_id=IPHONE_17E),
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
        self.assertIn("[other] iPhone 17e", names)
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

    def test_prune_without_keep_or_keep_nothing_is_user_error(self):
        before = len(self.fake.devices)
        out = self.run_cli("prune", "--yes", expect=1)
        self.assertIn("--keep-nothing", out)
        self.assertEqual(len(self.fake.devices), before)

    def test_prune_keep_nothing_deletes_unmanaged_unbooted(self):
        result = self.run_json("prune", "--keep-nothing", "--yes")
        self.assertEqual(sorted(d["udid"] for d in result["deleted"]), ["GONE", "KEEP-TYPE"])
        self.assertIn(("delete", "KEEP-TYPE"), self.fake.calls)
        names = self.fake.names()
        self.assertIn("[other] iPhone 17e", names)
        self.assertIn("Booted Thing", names)


class DoctorTests(CliCase):
    def test_doctor_reports_checks(self):
        self.run_json("configure")
        self.fake.devices.append(make_device("[orphan] iPhone 17 Pro"))
        with mock.patch.object(cli.shutil, "which", lambda _: None):
            result = self.run_json("doctor", expect=1)
        names = {c["name"]: c for c in result["checks"]}
        self.assertTrue(names["simctl"]["ok"])
        self.assertTrue(names["ios-runtime"]["ok"])
        self.assertFalse(names["axe"]["ok"])
        self.assertFalse(names["mobilebuildmcp"]["ok"])
        self.assertTrue(names["runtime-pin"]["ok"])
        self.assertFalse(names["orphan-sets"]["ok"])
        self.assertIn("orphan", names["orphan-sets"]["detail"])
        self.assertTrue(names["leases"]["ok"])

    def test_doctor_flags_devices_off_the_pinned_runtime(self):
        self.run_json("configure")
        self.fake.devices.append(make_device("[triton] iPhone 17 Pro Max", devicetype_id=IPHONE_17_PRO_MAX, runtime=IOS_18_4))
        with mock.patch.object(cli.shutil, "which", lambda name: f"/x/{name}"):
            result = self.run_json("doctor", expect=1)
        names = {c["name"]: c for c in result["checks"]}
        self.assertFalse(names["runtime-pin"]["ok"])
        self.assertIn("migrate", names["runtime-pin"]["detail"])


class RuntimePinTests(CliCase):
    def test_configure_pins_newest_runtime_and_keeps_pin(self):
        result = self.run_json("configure")
        self.assertEqual(result["pinned"], "26.3")
        manifest = json.loads((self.project / ".simset.json").read_text())
        self.assertEqual(manifest["runtime"], "26.3")
        self.assertEqual(self.run_json("configure")["pinned"], "26.3")

    def test_configure_reports_devices_on_other_runtimes(self):
        self.fake.devices.append(make_device("[triton] iPhone 17 Pro", runtime=IOS_18_4))
        result = self.run_json("configure")
        self.assertEqual(result["off_runtime"], ["[triton] iPhone 17 Pro"])

    def test_claim_skips_devices_off_the_pin_and_points_at_migrate(self):
        self.fake.devices.append(make_device("[triton] iPhone 17 Pro", runtime=IOS_18_4))
        self.run_json("configure")
        out = self.run_cli("claim", "phone", expect=1)
        self.assertIn("simset migrate", out)

    def test_migrate_dry_run_then_upgrades_unleased_devices(self):
        old = make_device("[triton] iPhone 17 Pro", udid="OLD", runtime=IOS_18_4, state="Booted")
        self.fake.devices.append(old)
        self.run_json("configure")
        dry = self.run_json("migrate", expect=1)
        self.assertTrue(dry["dry_run"])
        self.assertEqual([d["udid"] for d in dry["upgrade"]], ["OLD"])
        result = self.run_json("migrate", "--yes")
        self.assertEqual([d["udid"] for d in result["upgraded"]], ["OLD"])
        self.assertIn(("shutdown", "OLD"), self.fake.calls)
        self.assertIn(("upgrade", "OLD", IOS_26_3), self.fake.calls)
        self.assertEqual(self.run_json("claim", "phone")["udid"], "OLD")

    def test_claim_payload_includes_destination(self):
        self.run_json("configure")
        result = self.run_json("claim", "phone")
        self.assertEqual(result["destination"], f"platform=iOS Simulator,id={result['udid']}")


class ShutdownTests(CliCase):
    def setUp(self):
        super().setUp()
        self.run_json("configure")

    def test_release_shuts_device_down_unless_keep_booted(self):
        udid = self.run_json("claim", "phone", "--boot")["udid"]
        result = self.run_json("release", udid)
        self.assertEqual(result["shutdown"], [udid])
        udid = self.run_json("claim", "phone", "--boot")["udid"]
        result = self.run_json("release", udid, "--keep-booted")
        self.assertEqual(result["shutdown"], [])

    def test_reap_shutdown_idle_only_touches_unleased_managed_devices(self):
        held = self.run_json("claim", "phone", "--boot")["udid"]
        idle = self.run_json("claim", "tablet", "--boot")["udid"]
        self.run_json("release", idle, "--keep-booted")
        self.fake.devices.append(make_device("Unmanaged", udid="UNMANAGED", state="Booted"))
        result = self.run_json("leases", "--reap", "--shutdown-idle")
        self.assertEqual(result["shutdown"], [idle])
        states = {d["udid"]: d["state"] for d in self.fake.devices}
        self.assertEqual(states[held], "Booted")
        self.assertEqual(states["UNMANAGED"], "Booted")


class ReuseTests(CliCase):
    def setUp(self):
        super().setUp()
        self.run_json("configure")

    def test_reuse_returns_my_existing_lease_instead_of_contending(self):
        first = self.run_json("claim", "phone", "--boot")
        again = self.run_json("claim", "phone", "--boot", "--reuse")
        self.assertEqual(again["udid"], first["udid"])
        self.assertTrue(again["reused"])
        self.assertFalse(again["healed"])

    def test_reuse_claims_when_i_hold_nothing_and_ignores_other_owners(self):
        self.env["SIMSET_OWNER_PID"] = "1"
        self.run_json("claim", "phone")
        self.env["SIMSET_OWNER_PID"] = str(os.getpid())
        self.run_cli("claim", "phone", "--reuse", expect=3)
        result = self.run_json("claim", "tablet", "--reuse")
        self.assertFalse(result["reused"])


class DeviceHubHealTests(CliCase):
    def setUp(self):
        super().setUp()
        self.run_json("configure")

    def test_claim_boot_heals_when_device_hub_attached(self):
        udid = self.run_json("list")["devices"][0]["udid"]
        self.fake.hub_attached.add(udid)
        result = self.run_json("claim", "phone", "--boot")
        self.assertTrue(result["healed"])
        spawned = [c[2:] for c in self.fake.calls if c[0] == "spawn"]
        reset = spawned.index(("notifyutil", "-s", "com.apple.coredevice.dtuhidd.active", "0"))
        restart = spawned.index(("launchctl", "kickstart", "-k", "system/com.apple.backboardd"))
        self.assertLess(reset, restart)

    def test_claim_boot_skips_heal_when_not_attached_or_opted_out(self):
        self.assertFalse(self.run_json("claim", "phone", "--boot")["healed"])
        self.run_json("release", "--mine")
        udid = self.run_json("list")["devices"][0]["udid"]
        self.fake.hub_attached.add(udid)
        self.assertFalse(self.run_json("claim", "phone", "--boot", "--no-heal")["healed"])
        self.assertIn(udid, self.fake.hub_attached)

    def test_heal_command_reports_per_device(self):
        self.run_json("boot", "all")
        tablet = [d for d in self.run_json("list")["devices"] if d["type"].startswith("iPad")][0]["udid"]
        self.fake.hub_attached.add(tablet)
        result = self.run_json("heal", "all")
        healed = {d["udid"]: d["healed"] for d in result["devices"]}
        self.assertTrue(healed[tablet])
        self.assertEqual(sum(healed.values()), 1)


class InstructionsFileTests(CliCase):
    def test_configure_can_target_claude_local_md(self):
        self.run_json("configure", "--claude-md", "CLAUDE.local.md")
        self.assertIn("simset:start", (self.project / "CLAUDE.local.md").read_text())
        self.assertFalse((self.project / "CLAUDE.md").exists())
        self.run_json("configure")
        self.assertFalse((self.project / "CLAUDE.md").exists())
        self.assertEqual(json.loads((self.project / ".simset.json").read_text())["instructions"], "CLAUDE.local.md")


if __name__ == "__main__":
    unittest.main()
