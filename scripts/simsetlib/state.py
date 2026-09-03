"""Machine-local state (~/.simset) and the per-project .simset.json manifest."""
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

MANIFEST_FILENAME = ".simset.json"

DEFAULT_ROSTER = [
    {"type": "iPhone 17 Pro", "alias": "phone"},
    {"type": "iPhone 16e", "alias": "phone-small"},
    {"type": "iPad Pro 13-inch (M5)", "alias": "tablet"},
]


class StateError(Exception):
    pass


def simset_home(env=None):
    env = os.environ if env is None else env
    return Path(env.get("SIMSET_HOME", "~/.simset")).expanduser()


def utcnow_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json_atomic(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)


@dataclass
class RosterEntry:
    type: str
    alias: str | None = None


@dataclass
class Manifest:
    id: str
    roster: list = field(default_factory=list)
    runtime: str = "latest"

    @classmethod
    def default(cls, set_id):
        return cls(set_id, [RosterEntry(e["type"], e["alias"]) for e in DEFAULT_ROSTER])

    @classmethod
    def from_dict(cls, data):
        roster = [RosterEntry(e["type"], e.get("alias")) for e in data.get("roster", [])]
        return cls(data["id"], roster, data.get("runtime", "latest"))

    def to_dict(self):
        return {
            "id": self.id,
            "roster": [{"type": e.type, "alias": e.alias} for e in self.roster],
            "runtime": self.runtime,
        }

    def roster_types(self):
        return [e.type for e in self.roster]

    def type_for_alias(self, alias):
        for entry in self.roster:
            if entry.alias == alias:
                return entry.type
        phones = [e.type for e in self.roster if e.type.startswith("iPhone")]
        tablets = [e.type for e in self.roster if e.type.startswith("iPad")]
        if alias == "phone" and phones:
            return phones[0]
        if alias == "phone-small" and phones:
            return phones[1] if len(phones) > 1 else phones[0]
        if alias == "tablet" and tablets:
            return tablets[0]
        return None


def manifest_path(root):
    return Path(root) / MANIFEST_FILENAME


def find_project_root(start):
    start = Path(start)
    for candidate in [start, *start.parents]:
        if manifest_path(candidate).exists():
            return candidate
    return None


def load_manifest(root):
    path = manifest_path(root)
    if not path.exists():
        raise StateError(f"no {MANIFEST_FILENAME} in {root}; run `simset configure` first")
    return Manifest.from_dict(json.loads(path.read_text()))


def write_manifest(root, manifest):
    write_json_atomic(manifest_path(root), manifest.to_dict())


class Registry:
    def __init__(self, home):
        self.path = Path(home) / "registry.json"

    def load(self):
        if not self.path.exists():
            return {"sets": {}}
        return json.loads(self.path.read_text())

    def sets(self):
        return self.load().get("sets", {})

    def register(self, set_id, project_root, now=None):
        data = self.load()
        entry = data["sets"].get(set_id, {"created_at": now or utcnow_iso()})
        entry["project"] = str(project_root)
        data["sets"][set_id] = entry
        write_json_atomic(self.path, data)

    def unregister(self, set_id):
        data = self.load()
        if set_id in data["sets"]:
            del data["sets"][set_id]
            write_json_atomic(self.path, data)
