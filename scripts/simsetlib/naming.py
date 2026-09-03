"""The `[set-id] Device Type #N` naming convention that namespaces devices in the default set."""
import re
from dataclasses import dataclass

NAME_RE = re.compile(r"^\[(?P<set>[^\]]+)\] (?P<type>.+?)(?: #(?P<n>\d+))?$")


@dataclass(frozen=True)
class ParsedName:
    set_id: str
    type_name: str
    index: int


def device_name(set_id, type_name, index=1):
    base = f"[{set_id}] {type_name}"
    return base if index == 1 else f"{base} #{index}"


def parse_name(name):
    match = NAME_RE.match(name or "")
    if not match:
        return None
    return ParsedName(match.group("set"), match.group("type"), int(match.group("n") or 1))


def is_managed(name):
    return parse_name(name) is not None
