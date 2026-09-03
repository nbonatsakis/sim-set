"""argparse front end. Every subcommand is `cmd_<name>(ctx, args) -> int`."""
import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from .claudemd import update_claude_md
from .leases import LeaseError, Leases
from .naming import parse_name
from .planning import (PlanningError, plan_provision, resolve_devicetype, resolve_runtime, set_devices,
                       type_names_by_id)
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
