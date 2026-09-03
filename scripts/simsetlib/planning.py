"""Pure planning functions: what to create, what to delete. No subprocess calls here."""
from dataclasses import dataclass, field

from .naming import device_name, parse_name


class PlanningError(Exception):
    pass


def _version_tuple(version):
    return tuple(int(part) for part in version.split(".") if part.isdigit())


def resolve_runtime(runtimes, policy="latest"):
    candidates = [r for r in runtimes if r.get("platform") == "iOS" and r.get("isAvailable")]
    if policy != "latest":
        candidates = [r for r in candidates if r["version"] == policy or r["version"].startswith(policy + ".")]
    if not candidates:
        raise PlanningError(f"no available iOS runtime matching {policy!r}; see `xcrun simctl list runtimes`")
    return max(candidates, key=lambda r: _version_tuple(r["version"]))


def resolve_devicetype(devicetypes, type_name):
    for devicetype in devicetypes:
        if devicetype["name"] == type_name:
            return devicetype
    raise PlanningError(f"unknown device type {type_name!r}; see `xcrun simctl list devicetypes`")


def type_names_by_id(devicetypes):
    return {dt["identifier"]: dt["name"] for dt in devicetypes}


def set_devices(devices, set_id):
    return [d for d in devices if (parsed := parse_name(d["name"])) and parsed.set_id == set_id]


def matching_devices(devices, set_id, type_name):
    matches = [d for d in set_devices(devices, set_id) if parse_name(d["name"]).type_name == type_name]
    return sorted(matches, key=lambda d: parse_name(d["name"]).index)


def next_index(devices, set_id, type_name):
    indices = [parse_name(d["name"]).index for d in matching_devices(devices, set_id, type_name)]
    return max(indices, default=0) + 1


def resolve_type(manifest, alias_or_type):
    return manifest.type_for_alias(alias_or_type) or alias_or_type


@dataclass
class CreateOp:
    name: str
    type_name: str
    devicetype_id: str
    runtime_id: str


def plan_provision(manifest, devices, devicetypes, runtime):
    present = {parse_name(d["name"]).type_name for d in set_devices(devices, manifest.id)}
    ops = []
    for entry in manifest.roster:
        if entry.type in present:
            continue
        devicetype = resolve_devicetype(devicetypes, entry.type)
        ops.append(CreateOp(device_name(manifest.id, entry.type), entry.type, devicetype["identifier"], runtime["identifier"]))
    return ops


def grow_op(manifest, devices, devicetypes, runtime, type_name):
    devicetype = resolve_devicetype(devicetypes, type_name)
    index = next_index(devices, manifest.id, type_name)
    return CreateOp(device_name(manifest.id, type_name, index), type_name, devicetype["identifier"], runtime["identifier"])


@dataclass
class PrunePlan:
    delete: list = field(default_factory=list)
    skipped_booted: list = field(default_factory=list)
    kept: list = field(default_factory=list)
    managed: list = field(default_factory=list)


def plan_prune(devices, keep, type_names, include_booted=False):
    keep = set(keep)
    plan = PrunePlan()
    for device in devices:
        if parse_name(device["name"]):
            plan.managed.append(device)
        elif device["name"] in keep or type_names.get(device.get("deviceTypeIdentifier"), "") in keep:
            plan.kept.append(device)
        elif device.get("state") == "Booted" and not include_booted:
            plan.skipped_booted.append(device)
        else:
            plan.delete.append(device)
    return plan
