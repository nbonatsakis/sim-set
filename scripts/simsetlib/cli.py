"""argparse front end. Every subcommand is `cmd_<name>(ctx, args) -> int`."""
import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .claudemd import update_claude_md
from .leases import LeaseError, Leases, find_owner_pid
from .naming import parse_name
from .planning import (PlanningError, grow_op, matching_devices, plan_provision, resolve_devicetype,
                       resolve_runtime, resolve_type, set_devices, type_names_by_id)
from .simctl import Simctl, SimctlError
from .state import (Manifest, Registry, RosterEntry, StateError, find_project_root, load_manifest,
                    manifest_path, simset_home, write_manifest)

EXIT_OK, EXIT_USER, EXIT_SIMCTL, EXIT_CONTENTION = 0, 1, 2, 3


class UsageError(Exception):
    pass


@dataclass
class Context:
    simctl: object
    home: Path
    env: dict
    stdout: object
    cwd: Path
    json: bool
    project_arg: str | None
    registry: Registry
    leases: Leases


def emit(ctx, payload, human_lines):
    if ctx.json:
        ctx.stdout.write(json.dumps(payload, indent=2) + "\n")
    else:
        for line in human_lines:
            ctx.stdout.write(line + "\n")


def resolve_project_root(ctx, require=True):
    if ctx.project_arg:
        root = Path(ctx.project_arg).expanduser().resolve()
        if require and not manifest_path(root).exists():
            raise UsageError(f"no .simset.json in {root}; run `simset configure --project {root}` first")
        return root
    found = find_project_root(ctx.cwd)
    if found is None:
        if require:
            raise UsageError(f"no .simset.json found from {ctx.cwd} upward; run `simset configure` in the project first")
        return ctx.cwd
    return found


def load_project(ctx):
    root = resolve_project_root(ctx)
    return root, load_manifest(root)


def lease_summary(lease, leases):
    if lease is None:
        return None
    return {"owner_pid": lease.owner_pid, "label": lease.label, "expires_at": lease.expires_at,
            "stale": leases.is_stale(lease)}


def device_row(device, lease, type_names, leases):
    parsed = parse_name(device["name"])
    return {
        "udid": device["udid"],
        "name": device["name"],
        "type": type_names.get(device.get("deviceTypeIdentifier"), parsed.type_name if parsed else ""),
        "state": device.get("state", "?"),
        "runtime": device.get("runtime", "").rsplit(".", 1)[-1],
        "available": bool(device.get("isAvailable", True)),
        "lease": lease_summary(lease, leases),
    }


def format_row(row):
    lease = row["lease"]
    holder = ""
    if lease:
        holder = f"  leased by pid {lease['owner_pid']}" + (f" ({lease['label']})" if lease["label"] else "")
        if lease["stale"]:
            holder += " [stale]"
    return f"{row['state']:<9} {row['udid']}  {row['name']}{holder}"


def default_alias_for(type_name, taken):
    family = "phone" if type_name.startswith("iPhone") else "tablet" if type_name.startswith("iPad") else None
    if family == "phone" and "phone" in taken:
        family = "phone-small"
    if family is None or family in taken:
        return None
    return family


def cmd_configure(ctx, args):
    root = resolve_project_root(ctx, require=False)
    existing = load_manifest(root) if manifest_path(root).exists() else None
    set_id = args.id or (existing.id if existing else root.name)
    if args.roster:
        roster, taken = [], set()
        for type_name in args.roster:
            alias = default_alias_for(type_name, taken)
            taken.add(alias)
            roster.append(RosterEntry(type_name, alias))
    else:
        roster = existing.roster if existing else Manifest.default(set_id).roster
    manifest = Manifest(set_id, roster, args.runtime or (existing.runtime if existing else "latest"))

    devicetypes = ctx.simctl.list_devicetypes()
    for entry in manifest.roster:
        resolve_devicetype(devicetypes, entry.type)
    runtime = resolve_runtime(ctx.simctl.list_runtimes(), manifest.runtime)
    ops = plan_provision(manifest, ctx.simctl.list_devices(), devicetypes, runtime)

    write_manifest(root, manifest)
    created = [{"name": op.name, "udid": ctx.simctl.create(op.name, op.devicetype_id, op.runtime_id)} for op in ops]
    ctx.registry.register(set_id, root)
    claude_md = None if args.no_claude_md else str(update_claude_md(root, set_id))

    payload = {"id": set_id, "project": str(root), "runtime": runtime["version"], "created": created,
               "roster": manifest.to_dict()["roster"], "claude_md": claude_md}
    lines = [f"set [{set_id}] configured for {root} on iOS {runtime['version']}"]
    lines += [f"  created {c['name']} ({c['udid']})" for c in created] or ["  all roster devices already exist"]
    if claude_md:
        lines.append(f"  CLAUDE.md section updated: {claude_md}")
    emit(ctx, payload, lines)
    return EXIT_OK


def cmd_list(ctx, args):
    type_names = type_names_by_id(ctx.simctl.list_devicetypes())
    devices = ctx.simctl.list_devices()
    leases_by_udid = {lease.udid: lease for lease in ctx.leases.all()}

    def rows(subset):
        return [device_row(d, leases_by_udid.get(d["udid"]), type_names, ctx.leases) for d in subset]

    if args.all:
        sets, unmanaged = {}, []
        for device in devices:
            parsed = parse_name(device["name"])
            (sets.setdefault(parsed.set_id, []) if parsed else unmanaged).append(device)
        registered = ctx.registry.sets()
        payload = {"sets": {sid: {"project": registered.get(sid, {}).get("project"), "devices": rows(ds)}
                            for sid, ds in sorted(sets.items())},
                   "unmanaged": rows(unmanaged)}
        lines = []
        for sid, info in payload["sets"].items():
            lines.append(f"[{sid}]  {info['project'] or '(not registered)'}")
            lines += ["  " + format_row(r) for r in info["devices"]]
        lines.append("unmanaged")
        lines += ["  " + format_row(r) for r in payload["unmanaged"]] or ["  (none)"]
        emit(ctx, payload, lines)
        return EXIT_OK

    root, manifest = load_project(ctx)
    payload = {"id": manifest.id, "project": str(root), "devices": rows(set_devices(devices, manifest.id))}
    lines = [f"[{manifest.id}]  {root}"] + ["  " + format_row(r) for r in payload["devices"]]
    emit(ctx, payload, lines)
    return EXIT_OK


def owner_pid(ctx):
    return find_owner_pid(ctx.env)


def claim_payload(ctx, device, lease, type_names):
    row = device_row(device, lease, type_names, ctx.leases)
    row["set"] = lease.set_id
    return row


def find_device(devices, udid):
    for device in devices:
        if device["udid"] == udid:
            return device
    return None


def cmd_claim(ctx, args):
    root, manifest = load_project(ctx)
    type_names = type_names_by_id(ctx.simctl.list_devicetypes())

    if args.renew:
        lease = ctx.leases.renew(args.renew, args.ttl)
        device = find_device(ctx.simctl.list_devices(), args.renew)
        if device is None:
            raise UsageError(f"device {args.renew} no longer exists")
        payload = claim_payload(ctx, device, lease, type_names)
        emit(ctx, payload, [f"renewed {device['name']} until {lease.expires_at}"])
        return EXIT_OK
    if not args.target:
        raise UsageError("claim needs a size or device type (phone, phone-small, tablet, or an exact type name) or --renew <udid>")

    type_name = resolve_type(manifest, args.target)
    if type_name not in manifest.roster_types():
        raise UsageError(f"{type_name!r} is not in set [{manifest.id}]; run `simset add \"{type_name}\"` first")

    deadline = time.monotonic() + (args.wait or 0)
    while True:
        devices = ctx.simctl.list_devices()
        candidates = matching_devices(devices, manifest.id, type_name)
        lease = ctx.leases.claim(candidates, manifest.id, owner_pid(ctx), args.label or "", args.ttl)
        if lease:
            break
        if args.grow:
            runtime = resolve_runtime(ctx.simctl.list_runtimes(), manifest.runtime)
            op = grow_op(manifest, devices, ctx.simctl.list_devicetypes(), runtime, type_name)
            ctx.simctl.create(op.name, op.devicetype_id, op.runtime_id)
            continue
        if args.wait and time.monotonic() < deadline:
            time.sleep(2)
            continue
        ctx.stdout.write(f"error: every [{manifest.id}] {type_name} is leased; retry with --grow or --wait <seconds>\n")
        return EXIT_CONTENTION

    device = find_device(ctx.simctl.list_devices(), lease.udid)
    if args.boot and device.get("state") != "Booted":
        ctx.simctl.boot(device["udid"])
        device = find_device(ctx.simctl.list_devices(), lease.udid)
    payload = claim_payload(ctx, device, lease, type_names)
    emit(ctx, payload, [f"claimed {device['name']}", f"udid {device['udid']}", f"state {device['state']}",
                        f"lease until {lease.expires_at} (pid {lease.owner_pid})"])
    return EXIT_OK


def cmd_release(ctx, args):
    if args.mine:
        released = ctx.leases.release_owned(owner_pid(ctx))
    elif args.all:
        _, manifest = load_project(ctx)
        released = ctx.leases.release_set(manifest.id)
    elif args.udid:
        lease = ctx.leases.get(args.udid)
        released = [lease] if lease and ctx.leases.release(args.udid) else []
    else:
        raise UsageError("release needs a udid, --mine, or --all")
    udids = [lease.udid for lease in released if lease]
    emit(ctx, {"released": udids}, [f"released {u}" for u in udids] or ["nothing to release"])
    return EXIT_OK


def cmd_leases(ctx, args):
    if args.reap:
        reaped = ctx.leases.reap()
        emit(ctx, {"reaped": [l.to_dict() for l in reaped]}, [f"reaped {l.udid} ({l.name})" for l in reaped] or ["no stale leases"])
        return EXIT_OK
    rows = [{**l.to_dict(), "stale": ctx.leases.is_stale(l)} for l in ctx.leases.all()]
    lines = [f"{r['udid']}  {r['name']}  pid {r['owner_pid']}  until {r['expires_at']}" + ("  [stale]" if r["stale"] else "")
             + (f"  ({r['label']})" if r["label"] else "") for r in rows] or ["no leases"]
    emit(ctx, {"leases": rows}, lines)
    return EXIT_OK


def add_subcommand(sub, name, help_text, global_options):
    """Register a subcommand that also accepts --project/--json after the subcommand name."""
    return sub.add_parser(name, help=help_text, parents=[global_options])


def build_parser():
    global_options = argparse.ArgumentParser(add_help=False)
    global_options.add_argument("--project", default=argparse.SUPPRESS,
                                 help="project root (default: nearest .simset.json from cwd upward)")
    global_options.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                                 help="machine-readable output")

    parser = argparse.ArgumentParser(prog="simset", description="Project-scoped iOS simulator sets for concurrent agents.")
    parser.add_argument("--project", help="project root (default: nearest .simset.json from cwd upward)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    sub = parser.add_subparsers(dest="command", required=True)

    p = add_subcommand(sub, "configure", "create or update this project's simulator set", global_options)
    p.add_argument("--id", help="set id (default: existing manifest id, else directory name)")
    p.add_argument("--roster", action="append", help="device type name; repeatable (default: iPhone 17 Pro, iPhone 16e, iPad Pro 13-inch (M5))")
    p.add_argument("--runtime", help="iOS runtime policy: latest (default) or a version prefix like 26.3")
    p.add_argument("--no-claude-md", action="store_true", help="do not touch CLAUDE.md")
    p.set_defaults(func=cmd_configure)

    p = add_subcommand(sub, "list", "list this project's devices (or every simulator with --all)", global_options)
    p.add_argument("--all", action="store_true")
    p.set_defaults(func=cmd_list)

    p = add_subcommand(sub, "claim", "lease a device of a size/type from this project's set", global_options)
    p.add_argument("target", nargs="?", help="phone | phone-small | tablet | exact device type name")
    p.add_argument("--label", help="what you are doing (shown in list)")
    p.add_argument("--boot", action="store_true", help="boot the device after claiming")
    p.add_argument("--wait", type=int, default=0, help="seconds to wait for a free device")
    p.add_argument("--grow", action="store_true", help="provision another device of this type if none is free")
    p.add_argument("--ttl", type=float, default=4.0, help="lease hours (default 4)")
    p.add_argument("--renew", metavar="UDID", help="extend an existing lease instead of claiming")
    p.set_defaults(func=cmd_claim)

    p = add_subcommand(sub, "release", "release leases", global_options)
    p.add_argument("udid", nargs="?")
    p.add_argument("--mine", action="store_true", help="release every lease owned by this agent")
    p.add_argument("--all", action="store_true", help="release every lease in this project's set")
    p.set_defaults(func=cmd_release)

    p = add_subcommand(sub, "leases", "show leases across all sets", global_options)
    p.add_argument("--reap", action="store_true", help="delete stale leases")
    p.set_defaults(func=cmd_leases)
    return parser


def main(argv=None, simctl=None, env=None, stdout=None, cwd=None):
    env = os.environ if env is None else env
    stdout = sys.stdout if stdout is None else stdout
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    home = simset_home(env)
    ctx = Context(simctl=simctl or Simctl(), home=home, env=env, stdout=stdout,
                  cwd=Path(cwd or os.getcwd()), json=args.json, project_arg=args.project,
                  registry=Registry(home), leases=Leases(home))
    try:
        return args.func(ctx, args)
    except SimctlError as error:
        stdout.write(f"error: {error}\n")
        return EXIT_SIMCTL
    except (UsageError, StateError, PlanningError, LeaseError) as error:
        stdout.write(f"error: {error}\n")
        return EXIT_USER
