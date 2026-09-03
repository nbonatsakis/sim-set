"""argparse front end. Every subcommand is `cmd_<name>(ctx, args) -> int`."""
import argparse
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from . import baguette
from .claudemd import SectionError, remove_from_claude_md, update_claude_md
from .leases import LeaseError, Leases, find_owner_pid
from .naming import parse_name
from .planning import (PlanningError, grow_op, matching_devices, plan_provision, plan_prune, resolve_devicetype,
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
    stderr: object
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


def emit_error(ctx, message, exit_code):
    """Write an error consistently: JSON on stdout when --json, else plain text on stderr."""
    if ctx.json:
        ctx.stdout.write(json.dumps({"error": message, "exit_code": exit_code}) + "\n")
    else:
        ctx.stderr.write(f"error: {message}\n")


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
    return {"owner_pid": lease.owner_pid, "owner_source": lease.owner_source, "label": lease.label,
            "expires_at": lease.expires_at, "stale": leases.is_stale(lease)}


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
    """Return (pid, source) — source is "env", "claude-ancestor", or "parent-pid"."""
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


MAX_GROWS_PER_CLAIM = 5


def refetch_device(ctx, lease):
    device = find_device(ctx.simctl.list_devices(), lease.udid)
    if device is None:
        ctx.leases.release(lease.udid)
        raise UsageError(f"device {lease.udid} disappeared after being claimed; lease released")
    return device


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
    grows = 0
    pid, source = owner_pid(ctx)
    while True:
        devices = ctx.simctl.list_devices()
        candidates = matching_devices(devices, manifest.id, type_name)
        lease = ctx.leases.claim(candidates, manifest.id, pid, source, args.label or "", args.ttl)
        if lease:
            break
        if args.wait and time.monotonic() < deadline:
            time.sleep(2)
            continue
        if args.grow:
            if grows >= MAX_GROWS_PER_CLAIM:
                raise UsageError(f"gave up after growing [{manifest.id}] {type_name} {MAX_GROWS_PER_CLAIM} times; still contended")
            grows += 1
            runtime = resolve_runtime(ctx.simctl.list_runtimes(), manifest.runtime)
            op = grow_op(manifest, devices, ctx.simctl.list_devicetypes(), runtime, type_name)
            ctx.simctl.create(op.name, op.devicetype_id, op.runtime_id)
            continue
        emit_error(ctx, f"every [{manifest.id}] {type_name} is leased; retry with --grow or --wait <seconds>", EXIT_CONTENTION)
        return EXIT_CONTENTION

    warning = None
    if source == "parent-pid":
        warning = f"no claude ancestor found; lease bound to short-lived pid {pid}. Set SIMSET_OWNER_PID to a long-lived process id."

    device = refetch_device(ctx, lease)
    if args.boot and device.get("state") != "Booted":
        ctx.simctl.boot(device["udid"])
        ctx.simctl.bootstatus(device["udid"])
        device = refetch_device(ctx, lease)
    payload = claim_payload(ctx, device, lease, type_names)
    if warning:
        if ctx.json:
            payload["warning"] = warning
        else:
            ctx.stderr.write(f"warning: {warning}\n")
    emit(ctx, payload, [f"claimed {device['name']}", f"udid {device['udid']}", f"state {device['state']}",
                        f"lease until {lease.expires_at} (pid {lease.owner_pid})"])
    return EXIT_OK


def cmd_release(ctx, args):
    if args.mine:
        pid, _source = owner_pid(ctx)
        released = ctx.leases.release_owned(pid)
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


def resolve_targets(ctx, manifest, devices, target, allow_all):
    members = set_devices(devices, manifest.id)
    if target == "all":
        if not allow_all:
            raise UsageError("'all' is not allowed for this command")
        return members
    by_udid = find_device(devices, target)
    if by_udid is not None:
        if by_udid not in members:
            raise UsageError(f"{target} ({by_udid['name']}) is outside set [{manifest.id}]")
        return [by_udid]
    matches = matching_devices(devices, manifest.id, resolve_type(manifest, target))
    if not matches:
        raise UsageError(f"no device matching {target!r} in set [{manifest.id}]; see `simset list`")
    return matches


def require_yes(ctx, args, payload, lines):
    if args.yes:
        return None
    emit(ctx, {**payload, "dry_run": True}, lines + ["re-run with --yes to apply"])
    return EXIT_USER


def rows_for(ctx, udids):
    type_names = type_names_by_id(ctx.simctl.list_devicetypes())
    devices = ctx.simctl.list_devices()
    leases_by_udid = {lease.udid: lease for lease in ctx.leases.all()}
    return [device_row(d, leases_by_udid.get(d["udid"]), type_names, ctx.leases)
            for d in devices if d["udid"] in udids]


def lifecycle(ctx, args, action, allow_all):
    _, manifest = load_project(ctx)
    targets = resolve_targets(ctx, manifest, ctx.simctl.list_devices(), args.target, allow_all)
    for device in targets:
        if action == "boot" and device.get("state") != "Booted":
            ctx.simctl.boot(device["udid"])
            ctx.simctl.bootstatus(device["udid"])
        elif action == "shutdown" and device.get("state") != "Shutdown":
            ctx.simctl.shutdown(device["udid"])
        elif action == "erase":
            if device.get("state") == "Booted":
                ctx.simctl.shutdown(device["udid"])
            ctx.simctl.erase(device["udid"])
    rows = rows_for(ctx, {d["udid"] for d in targets})
    emit(ctx, {"action": action, "devices": rows}, [f"{action}: " + format_row(r) for r in rows])
    return EXIT_OK


def cmd_boot(ctx, args):
    return lifecycle(ctx, args, "boot", allow_all=True)


def cmd_shutdown(ctx, args):
    return lifecycle(ctx, args, "shutdown", allow_all=True)


def cmd_erase(ctx, args):
    return lifecycle(ctx, args, "erase", allow_all=False)


def cmd_add(ctx, args):
    root, manifest = load_project(ctx)
    devicetypes = ctx.simctl.list_devicetypes()
    resolve_devicetype(devicetypes, args.type)
    if args.type not in manifest.roster_types():
        manifest.roster.append(RosterEntry(args.type, args.alias))
    elif args.alias:
        for entry in manifest.roster:
            if entry.type == args.type:
                entry.alias = args.alias
    write_manifest(root, manifest)
    runtime = resolve_runtime(ctx.simctl.list_runtimes(), manifest.runtime)
    ops = plan_provision(manifest, ctx.simctl.list_devices(), devicetypes, runtime)
    created = [{"name": op.name, "udid": ctx.simctl.create(op.name, op.devicetype_id, op.runtime_id)} for op in ops]
    emit(ctx, {"created": created, "roster": manifest.to_dict()["roster"]},
         [f"created {c['name']} ({c['udid']})" for c in created] or ["device already present"])
    return EXIT_OK


def delete_devices(ctx, devices):
    deleted = []
    for device in devices:
        if device.get("state") == "Booted":
            ctx.simctl.shutdown(device["udid"])
        ctx.simctl.delete(device["udid"])
        ctx.leases.release(device["udid"])
        deleted.append({"name": device["name"], "udid": device["udid"]})
    return deleted


def cmd_remove(ctx, args):
    root, manifest = load_project(ctx)
    targets = resolve_targets(ctx, manifest, ctx.simctl.list_devices(), args.target, allow_all=False)
    plan = [{"name": d["name"], "udid": d["udid"]} for d in targets]
    blocked = require_yes(ctx, args, {"delete": plan}, [f"would delete {p['name']} ({p['udid']})" for p in plan])
    if blocked:
        return blocked
    deleted = delete_devices(ctx, targets)
    removed_types = {parse_name(d["name"]).type_name for d in targets}
    remaining_types = {parse_name(d["name"]).type_name for d in set_devices(ctx.simctl.list_devices(), manifest.id)}
    manifest.roster = [e for e in manifest.roster if e.type not in removed_types or e.type in remaining_types]
    write_manifest(root, manifest)
    emit(ctx, {"deleted": deleted, "roster": manifest.to_dict()["roster"]}, [f"deleted {d['name']}" for d in deleted])
    return EXIT_OK


def cmd_destroy(ctx, args):
    root, manifest = load_project(ctx)
    targets = set_devices(ctx.simctl.list_devices(), manifest.id)
    plan = [{"name": d["name"], "udid": d["udid"]} for d in targets]
    blocked = require_yes(ctx, args, {"delete": plan, "manifest": str(manifest_path(root))},
                          [f"would delete {p['name']} ({p['udid']})" for p in plan]
                          + [f"would remove {manifest_path(root)} and the CLAUDE.md section, and unregister [{manifest.id}]"])
    if blocked:
        return blocked
    deleted = delete_devices(ctx, targets)
    ctx.leases.release_set(manifest.id)
    ctx.registry.unregister(manifest.id)
    remove_from_claude_md(root)
    manifest_path(root).unlink(missing_ok=True)
    emit(ctx, {"deleted": deleted, "id": manifest.id}, [f"deleted {d['name']}" for d in deleted] + [f"set [{manifest.id}] destroyed"])
    return EXIT_OK


def brief(device):
    return {"name": device["name"], "udid": device["udid"], "state": device.get("state")}


def cmd_prune(ctx, args):
    if not args.keep and not args.keep_nothing:
        raise UsageError("prune needs at least one --keep, or --keep-nothing to delete every unmanaged simulator")
    devicetypes = ctx.simctl.list_devicetypes()
    plan = plan_prune(ctx.simctl.list_devices(), args.keep or [], type_names_by_id(devicetypes), include_booted=args.shutdown)
    payload = {"delete": [brief(d) for d in plan.delete], "skipped_booted": [brief(d) for d in plan.skipped_booted],
               "kept": [brief(d) for d in plan.kept], "managed": [brief(d) for d in plan.managed]}
    lines = [f"would delete {d['name']} ({d['udid']})" for d in payload["delete"]] or ["nothing to delete"]
    lines += [f"skipping booted {d['name']} (use --shutdown)" for d in payload["skipped_booted"]]
    lines += [f"keeping {len(plan.kept)} kept and {len(plan.managed)} managed devices"]
    blocked = require_yes(ctx, args, payload, lines)
    if blocked:
        return blocked
    deleted = delete_devices(ctx, plan.delete)
    emit(ctx, {**payload, "deleted": deleted}, [f"deleted {d['name']}" for d in deleted] or ["nothing deleted"])
    return EXIT_OK


def cmd_ui(ctx, args):
    _, manifest = load_project(ctx)
    for device in set_devices(ctx.simctl.list_devices(), manifest.id):
        if device.get("state") != "Booted":
            ctx.simctl.boot(device["udid"])
    status = baguette.ensure_running(args.port, ctx.home)
    url = baguette.farm_url(args.port, None if args.all else manifest.id)
    if status == "missing":
        emit_error(ctx, f"booted [{manifest.id}] devices, but cannot open the UI. {baguette.INSTALL_HINT}", EXIT_USER)
        return EXIT_USER
    if status == "timeout":
        emit_error(ctx, f"baguette did not answer on port {args.port} after 10s", EXIT_USER)
        return EXIT_USER
    baguette.open_url(url)
    emit(ctx, {"url": url, "baguette": status}, [f"opened {url} (baguette {status})"])
    return EXIT_OK


def cmd_doctor(ctx, args):
    checks = []

    def check(name, ok, detail):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    try:
        runtimes = ctx.simctl.list_runtimes()
        check("simctl", True, "xcrun simctl reachable")
    except SimctlError as error:
        runtimes = []
        check("simctl", False, str(error))
    ios = [r for r in runtimes if r.get("platform") == "iOS" and r.get("isAvailable")]
    check("ios-runtime", ios, ", ".join(sorted(r["version"] for r in ios)) or "no available iOS runtime; run `xcodebuild -downloadPlatform iOS`")

    binary = shutil.which("baguette")
    if not binary:
        check("baguette", False, baguette.INSTALL_HINT.splitlines()[0])
    elif baguette.is_running(baguette.DEFAULT_PORT):
        supported = baguette.supports_query_filter(baguette.DEFAULT_PORT)
        check("baguette", supported,
              f"{binary} running; ?q= filter " + ("supported" if supported else "NOT supported, build the fork"))
    else:
        check("baguette", True, f"{binary} (not running; `simset ui` starts it)")

    registered = ctx.registry.sets()
    missing = [sid for sid, info in registered.items() if not Path(info["project"]).exists()]
    check("registry", not missing, ", ".join(f"[{s}] project path missing" for s in missing) or f"{len(registered)} registered sets")

    devices = ctx.simctl.list_devices() if checks[0]["ok"] else []
    seen = {parse_name(d["name"]).set_id for d in devices if parse_name(d["name"])}
    orphans = sorted(seen - set(registered))
    check("orphan-sets", not orphans, ("orphan sets with devices but no registry entry: " + ", ".join(f"[{s}]" for s in orphans)) if orphans else "every managed set is registered")

    stale = [l for l in ctx.leases.all() if ctx.leases.is_stale(l)]
    check("leases", not stale, f"{len(stale)} stale leases; run `simset leases --reap`" if stale else f"{len(ctx.leases.all())} active leases")

    ok = all(c["ok"] for c in checks)
    emit(ctx, {"ok": ok, "checks": checks}, [("ok   " if c["ok"] else "FAIL ") + f"{c['name']}: {c['detail']}" for c in checks])
    return EXIT_OK if ok else EXIT_USER


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

    for name, func, help_text in [("boot", cmd_boot, "boot devices in this set"),
                                  ("shutdown", cmd_shutdown, "shut down devices in this set"),
                                  ("erase", cmd_erase, "erase a device in this set (shuts it down first)")]:
        p = add_subcommand(sub, name, help_text, global_options)
        p.add_argument("target", help="udid | alias | device type | all (boot/shutdown only)")
        p.set_defaults(func=func)

    p = add_subcommand(sub, "add", "add a device type to this set", global_options)
    p.add_argument("type", help="exact device type name, e.g. \"iPhone 17 Pro Max\"")
    p.add_argument("--alias", help="short alias for claim")
    p.set_defaults(func=cmd_add)

    p = add_subcommand(sub, "remove", "delete devices from this set", global_options)
    p.add_argument("target", help="udid | alias | device type")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_remove)

    p = add_subcommand(sub, "destroy", "delete the whole set, its manifest, and its CLAUDE.md section", global_options)
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_destroy)

    p = add_subcommand(sub, "prune", "delete unmanaged simulators except a keep list; never touches [set] devices", global_options)
    p.add_argument("--keep", action="append", help="device name or device type to keep; repeatable")
    p.add_argument("--keep-nothing", action="store_true", help="required in place of --keep to delete every unmanaged simulator")
    p.add_argument("--shutdown", action="store_true", help="also shut down and delete booted unmanaged devices")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_prune)

    p = add_subcommand(sub, "ui", "boot this set and open the baguette farm filtered to it", global_options)
    p.add_argument("--all", action="store_true", help="open the unfiltered farm")
    p.add_argument("--port", type=int, default=baguette.DEFAULT_PORT)
    p.set_defaults(func=cmd_ui)

    p = add_subcommand(sub, "doctor", "check Xcode, runtimes, baguette, registry, and leases", global_options)
    p.set_defaults(func=cmd_doctor)
    return parser


def main(argv=None, simctl=None, env=None, stdout=None, stderr=None, cwd=None):
    env = os.environ if env is None else env
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    home = simset_home(env)
    ctx = Context(simctl=simctl or Simctl(), home=home, env=env, stdout=stdout, stderr=stderr,
                  cwd=Path(cwd or os.getcwd()), json=args.json, project_arg=args.project,
                  registry=Registry(home), leases=Leases(home))
    try:
        return args.func(ctx, args)
    except SimctlError as error:
        emit_error(ctx, str(error), EXIT_SIMCTL)
        return EXIT_SIMCTL
    except (UsageError, StateError, PlanningError, LeaseError, SectionError) as error:
        emit_error(ctx, str(error), EXIT_USER)
        return EXIT_USER
