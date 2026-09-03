import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from fake_simctl import make_device
from simsetlib.leases import LeaseError, Leases, find_owner_pid


class Clock:
    def __init__(self):
        self.now = datetime(2026, 9, 3, 16, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now


class LeaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.alive = {100, 200}
        self.clock = Clock()
        self.leases = Leases(self.home, pid_alive=lambda pid: pid in self.alive, now=self.clock)
        self.a = make_device("[t] iPhone 17 Pro", udid="A")
        self.b = make_device("[t] iPhone 17 Pro #2", udid="B")

    def tearDown(self):
        self.tmp.cleanup()

    def test_claim_picks_first_free_candidate_and_persists(self):
        first = self.leases.claim([self.a, self.b], "t", owner_pid=100, owner_source="test", label="one")
        second = self.leases.claim([self.a, self.b], "t", owner_pid=200, owner_source="test", label="two")
        self.assertEqual((first.udid, second.udid), ("A", "B"))
        self.assertEqual(self.leases.get("A").label, "one")
        self.assertEqual(self.leases.get("A").expires_at, "2026-09-03T20:00:00Z")
        self.assertIsNone(self.leases.claim([self.a, self.b], "t", owner_pid=100, owner_source="test"))

    def test_dead_owner_lease_is_reaped_on_next_claim(self):
        self.leases.claim([self.a], "t", owner_pid=100, owner_source="test")
        self.alive.discard(100)
        lease = self.leases.claim([self.a], "t", owner_pid=200, owner_source="test")
        self.assertEqual(lease.owner_pid, 200)

    def test_expired_lease_is_stale(self):
        self.leases.claim([self.a], "t", owner_pid=100, owner_source="test", ttl_hours=1)
        self.clock.now += timedelta(hours=2)
        self.assertTrue(self.leases.is_stale(self.leases.get("A")))
        self.assertEqual([l.udid for l in self.leases.reap()], ["A"])
        self.assertEqual(self.leases.all(), [])

    def test_reap_leaves_live_lease_untouched_beside_stale_one(self):
        self.leases.claim([self.a], "t", owner_pid=100, owner_source="test")
        self.leases.claim([self.b], "t", owner_pid=200, owner_source="test")
        self.alive.discard(100)
        reaped = self.leases.reap()
        self.assertEqual([l.udid for l in reaped], ["A"])
        self.assertEqual([l.udid for l in self.leases.all()], ["B"])

    def test_renew_extends_and_missing_raises(self):
        self.leases.claim([self.a], "t", owner_pid=100, owner_source="test", ttl_hours=1)
        self.clock.now += timedelta(minutes=30)
        renewed = self.leases.renew("A", ttl_hours=4)
        self.assertEqual(renewed.expires_at, "2026-09-03T20:30:00Z")
        with self.assertRaises(LeaseError):
            self.leases.renew("Z")

    def test_release_variants(self):
        self.leases.claim([self.a], "t", owner_pid=100, owner_source="test")
        self.leases.claim([self.b], "t", owner_pid=200, owner_source="test")
        self.assertTrue(self.leases.release("A"))
        self.assertFalse(self.leases.release("A"))
        self.assertEqual([l.udid for l in self.leases.release_owned(200)], ["B"])
        self.leases.claim([self.a, self.b], "t", owner_pid=100, owner_source="test")
        self.leases.claim([self.a, self.b], "t", owner_pid=100, owner_source="test")
        self.assertEqual(sorted(l.udid for l in self.leases.release_set("t")), ["A", "B"])
        self.assertEqual(self.leases.all(), [])


class OwnerPidTests(unittest.TestCase):
    def test_env_override_wins(self):
        self.assertEqual(find_owner_pid({"SIMSET_OWNER_PID": "4242"}, ancestors=lambda: [], getppid=lambda: 1), (4242, "env"))

    def test_nearest_claude_ancestor(self):
        chain = [(50, "/bin/zsh"), (40, "/Users/nick/.local/bin/claude"), (30, "-/bin/zsh"), (20, "/usr/bin/login")]
        self.assertEqual(find_owner_pid({}, ancestors=lambda: chain, getppid=lambda: 50), (40, "claude-ancestor"))

    def test_falls_back_to_parent_pid(self):
        chain = [(50, "/bin/zsh"), (30, "-/bin/zsh")]
        self.assertEqual(find_owner_pid({}, ancestors=lambda: chain, getppid=lambda: 50), (50, "parent-pid"))


if __name__ == "__main__":
    unittest.main()
