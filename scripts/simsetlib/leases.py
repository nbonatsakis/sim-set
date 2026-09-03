"""Exclusive device leases: one JSON file per claimed UDID, every mutation under an flock."""
import fcntl
import json
import os
import subprocess
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .state import write_json_atomic


class LeaseError(Exception):
    pass


@dataclass
class Lease:
    udid: str
    name: str
    set_id: str
    owner_pid: int
    label: str
    claimed_at: str
    expires_at: str

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        return cls(**{k: data[k] for k in cls.__dataclass_fields__})


def pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def parse_iso(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def iso(moment):
    return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _utcnow():
    return datetime.now(timezone.utc)


class Leases:
    def __init__(self, home, pid_alive=pid_alive, now=_utcnow):
        self.dir = Path(home) / "leases"
        self.lock_path = Path(home) / "leases.lock"
        self.pid_alive = pid_alive
        self.now = now

    @contextmanager
    def locked(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        with open(self.lock_path, "a+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def _path(self, udid):
        return self.dir / f"{udid}.json"

    def _write(self, lease):
        write_json_atomic(self._path(lease.udid), lease.to_dict())

    def all(self):
        if not self.dir.exists():
            return []
        leases = []
        for path in sorted(self.dir.glob("*.json")):
            try:
                leases.append(Lease.from_dict(json.loads(path.read_text())))
            except (ValueError, KeyError):
                path.unlink(missing_ok=True)
        return leases

    def get(self, udid):
        path = self._path(udid)
        if not path.exists():
            return None
        return Lease.from_dict(json.loads(path.read_text()))

    def is_stale(self, lease):
        return not self.pid_alive(lease.owner_pid) or parse_iso(lease.expires_at) < self.now()

    def _reap_unlocked(self):
        removed = []
        for lease in self.all():
            if self.is_stale(lease):
                self._path(lease.udid).unlink(missing_ok=True)
                removed.append(lease)
        return removed

    def reap(self):
        with self.locked():
            return self._reap_unlocked()

    def claim(self, candidates, set_id, owner_pid, label="", ttl_hours=4.0):
        with self.locked():
            self._reap_unlocked()
            held = {lease.udid for lease in self.all()}
            for device in candidates:
                if device["udid"] in held:
                    continue
                moment = self.now()
                lease = Lease(device["udid"], device["name"], set_id, owner_pid, label,
                              iso(moment), iso(moment + timedelta(hours=ttl_hours)))
                self._write(lease)
                return lease
        return None

    def renew(self, udid, ttl_hours=4.0):
        with self.locked():
            lease = self.get(udid)
            if lease is None:
                raise LeaseError(f"no lease for {udid}")
            lease.expires_at = iso(self.now() + timedelta(hours=ttl_hours))
            self._write(lease)
            return lease

    def release(self, udid):
        with self.locked():
            path = self._path(udid)
            existed = path.exists()
            path.unlink(missing_ok=True)
            return existed

    def _release_where(self, predicate):
        with self.locked():
            released = [lease for lease in self.all() if predicate(lease)]
            for lease in released:
                self._path(lease.udid).unlink(missing_ok=True)
            return released

    def release_owned(self, owner_pid):
        return self._release_where(lambda lease: lease.owner_pid == owner_pid)

    def release_set(self, set_id):
        return self._release_where(lambda lease: lease.set_id == set_id)


def process_ancestors(pid=None):
    """Return [(pid, command), ...] from the parent of `pid` up to (not including) launchd."""
    chain = []
    current = os.getppid() if pid is None else pid
    for _ in range(64):
        proc = subprocess.run(["ps", "-o", "pid=,ppid=,comm=", "-p", str(current)], capture_output=True, text=True)
        parts = proc.stdout.split(None, 2)
        if len(parts) < 3:
            break
        found_pid, parent, command = int(parts[0]), int(parts[1]), parts[2].strip()
        chain.append((found_pid, command))
        if parent <= 1:
            break
        current = parent
    return chain


def find_owner_pid(env=None, ancestors=process_ancestors, getppid=os.getppid):
    env = os.environ if env is None else env
    override = env.get("SIMSET_OWNER_PID")
    if override and override.isdigit():
        return int(override)
    for pid, command in ancestors():
        if Path(command.lstrip("-")).name == "claude":
            return pid
    return getppid()
