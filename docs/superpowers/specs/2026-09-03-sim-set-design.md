# sim-set: project-scoped iOS simulator sets for concurrent agents

Date: 2026-09-03
Status: approved

## Problem

Nick runs many iOS apps at once, each driven by one or more coding agents. Agents grab whatever simulators they need from the shared CoreSimulator device list and trample each other: one agent boots or erases a device another is using, several agents pile onto the same "iPhone 17 Pro", and the global device list fills with junk.

## Goal

A Claude Code skill, `sim-set`, that:

- provisions a named, project-scoped set of simulators at several sizes
- lets agents claim an exclusive device from that set and release it
- tells agents, through the project's CLAUDE.md, to only ever use devices from the project's set
- doubles as a general simulator manager (list everything by set, prune the global list down to a keep-list plus managed sets)
- opens a per-project live view of the set's devices in a browser via a patched baguette

Everything must keep working with existing tools: `xcodebuild` (build and test), Xcode, AXe, XcodeBuildMCP, idb, the Claude Code Desktop simulator pane.

## Decisions already made (with evidence)

- **Devices live in the default CoreSimulator device set.** `simctl --set <dir>` gives perfect isolation, but on Xcode 26.3 `xcodebuild` cannot resolve a custom-set device as a destination (verified: `-destination id=<udid>` fails with "Unable to find a device matching the provided destination specifier"), AXe 1.7.1 hardcodes the default set, and XcodeBuildMCP has no device-set support. Isolation is therefore by naming convention plus leases, not by CoreSimulator.
- **Namespacing is a bracket prefix in the device name**: `[<set-id>] <Device Type Name>`. Verified simctl accepts `[`, `]`, `/`, `:` in names.
- **One set per project; agents lease devices.** Multiple agents on one project claim distinct devices from the same set.
- **Default roster**: iPhone 17 Pro, iPhone 16e (smallest current; Xcode 26.3 has no iPhone 17e device type), iPad Pro 13-inch (M5). Newest available iOS runtime.
- **Enforcement is instructions only.** No hooks. `configure` writes a CLAUDE.md section.
- **UI is baguette's `/farm` view, patched to filter by name.** Baguette (`tddworks/baguette`, Apache-2.0, Swift, Xcode 26) streams every booted default-set simulator with input, but its farm filter is family/OS/state only, has no URL parameters, and the server sends `frame-ancestors 'none'` so it cannot be embedded. A fork at `nbonatsakis/baguette` adds a name filter; the skill points at the fork until upstream merges.
- **Implementation is Python 3 stdlib**: a thin entry script plus a small `simsetlib` package with one module per responsibility, matching Nick's other skills.

## Architecture

```
~/dev/ai/skills/sim-set/            public repo nbonatsakis/sim-set
  SKILL.md                          skill entry: when to use, workflows, command map
  scripts/simset.py                 the CLI (stdlib only)
  scripts/install.sh                symlinks skill into ~/.claude/skills and `simset` onto PATH
  references/commands.md            full command reference and JSON shapes
  references/claude-md-section.md   the template injected into project CLAUDE.md
  tests/test_simset.py              unit tests with a fake simctl runner
  tests/smoke.sh                    integration smoke against real simctl
  docs/superpowers/specs|plans/     this spec and the implementation plan

~/dev/forks/baguette/               fork nbonatsakis/baguette, branch farm-name-filter

~/.simset/                          machine-local state
  registry.json                     set-id -> project path, created_at
  leases/<udid>.json                one lease per claimed device
  leases.lock                       flock target for all lease mutations
  baguette.pid                      pid of the baguette server simset started (if any)

<project>/.simset.json              committed manifest: id, roster, runtime policy
<project>/CLAUDE.md                 gains a marker-delimited simset section
```

### simset.py structure

One file, organized as small pure functions over data plus a thin subprocess layer:

- `Simctl` class: the only place that shells out. Methods `list_devices()`, `list_devicetypes()`, `list_runtimes()`, `create(name, devicetype, runtime)`, `delete(udid)`, `boot(udid)`, `bootstatus(udid)` (blocks until the device is fully booted, via `simctl bootstatus -b`), `shutdown(udid)`, `erase(udid)`. All return parsed JSON or raise `SimctlError` with stderr attached. Constructed with a `run` callable so tests inject a fake.
- Naming: `device_name(set_id, devicetype_name, index)` and `parse_name(name) -> (set_id, devicetype_name, index) | None`. Pattern: `^\[(?P<set>[^\]]+)\] (?P<type>.+?)(?: #(?P<n>\d+))?$`.
- Manifest: `load_manifest(project_root)`, `write_manifest(...)`. Schema below.
- Registry: `load_registry()`, `register(set_id, project_root)`, `unregister(set_id)`.
- Planning (pure): `plan_provision(manifest, devices, devicetypes, runtimes)` returns the list of create operations needed so `configure` is idempotent; `plan_prune(devices, keep_names, managed_set_ids)` returns deletions.
- Leases: `Leases` class over `~/.simset/leases` with `claim(candidates, owner_pid, label, ttl)`, `release(udid)`, `reap()`, `all()`. Every mutation runs inside `with flock(leases.lock)`.
- Size aliases: `phone` -> the roster's first iPhone that is not the small phone; `phone-small` -> iPhone 16e (or the roster's smallest iPhone); `tablet` -> the roster's first iPad. Any exact device type name is also accepted.
- CLAUDE.md injection: `inject_section(text, section) -> text` replaces content between `<!-- simset:start -->` and `<!-- simset:end -->` or appends the block. Idempotent: running twice yields identical output.
- CLI: `argparse` subcommands, JSON output with `--json`, human output otherwise. Exit code 0 success, 1 user error, 2 simctl error, 3 lock/lease contention (used by `--wait` timeout).

### Data shapes

`.simset.json`

```json
{
  "id": "triton",
  "roster": [
    {"type": "iPhone 17 Pro", "alias": "phone"},
    {"type": "iPhone 16e", "alias": "phone-small"},
    {"type": "iPad Pro 13-inch (M5)", "alias": "tablet"}
  ],
  "runtime": "latest"
}
```

`runtime` is `latest` or a version string like `26.3`. Resolution: pick the newest available iOS runtime whose version matches the prefix.

`~/.simset/registry.json`

```json
{"sets": {"triton": {"project": "/Users/nick/dev/projects/triton", "created_at": "2026-09-03T16:00:00Z"}}}
```

`~/.simset/leases/<udid>.json`

```json
{"udid": "...", "name": "[triton] iPhone 17 Pro", "set": "triton", "owner_pid": 4242, "owner_source": "env", "label": "onboarding fix", "claimed_at": "...", "expires_at": "..."}
```

Default TTL is 4 hours, renewable via `simset claim --renew <udid>`. A lease is stale when its owner PID is not alive or `expires_at` has passed.

`simset claim --json` output

```json
{"udid": "...", "name": "[triton] iPhone 17 Pro", "type": "iPhone 17 Pro", "state": "Booted", "set": "triton", "lease": {"owner_pid": 4242, "owner_source": "env", "expires_at": "..."}}
```

## Commands

All commands accept `--project <path>` (default: walk up from cwd to the nearest `.simset.json`, else cwd) and `--json`.

- `configure [--id ID] [--roster "iPhone 17 Pro" ...] [--runtime latest|26.3] [--no-claude-md]`
  Writes or updates `.simset.json`, provisions missing roster devices (never deletes), registers the set, injects the CLAUDE.md section. Prints what it created. Safe to re-run.
- `list [--all]`
  Project's devices with state, type, and lease holder. `--all` lists every simulator grouped by set id, with an "unmanaged" group, and marks stale leases.
- `claim <phone|phone-small|tablet|"Device Type"> [--label TEXT] [--boot] [--wait SECONDS] [--grow] [--ttl HOURS]`
  Reaps stale leases, picks an unleased device matching the alias or type, writes a lease owned by the agent process, optionally boots it. `--boot` does not return until the device is fully booted — `xcrun simctl boot` reports `Booted` well before SpringBoard is up, so `claim --boot` also runs `xcrun simctl bootstatus <udid> -b` before returning, and the same applies to the standalone `boot` command. Owner identity: `SIMSET_OWNER_PID` if set, else the nearest ancestor process whose executable is named `claude` (verified ancestry of a Claude Code Bash call: `zsh -> claude -> zsh -> login -> terminal`; the tool's own shell dies after each call so it must not be the owner), else the parent PID — the lease records which of these applied (`owner_source`), and `claim` warns when it had to fall back to the parent PID, since that process is typically already dead by the time the lease is read again. `--wait` polls every 2 seconds until one frees up or the timeout hits (exit 3 if it never does). `--grow` creates `[id] <type> #N` when none are free, capped at 5 creates per invocation. Given both, `claim` waits out the full `--wait` deadline first and only grows if that times out — it never grows immediately just because `--grow` was also passed. `--renew <udid>` extends an existing lease.
- `release <udid> | --mine | --all`
  Removes leases. `--mine` releases every lease owned by the caller's PID tree; `--all` releases all leases in the project's set.
- `leases [--reap]`
  Shows leases across all sets; `--reap` deletes stale ones.
- `boot <udid|alias|all>`, `shutdown <udid|alias|all>`, `erase <udid|alias>`
  Scoped to the project's set. Refuses UDIDs outside the set.
- `add "<Device Type>" [--alias NAME]`, `remove <udid|alias> [--yes]`
  Add or remove a roster entry and its device.
- `destroy [--yes]`
  Shuts down and deletes every device in the set, drops leases, unregisters, removes the CLAUDE.md section, deletes `.simset.json`. Prints the plan and requires `--yes`.
- `prune (--keep "<Device Type or full name>" ... | --keep-nothing) [--shutdown] [--yes]`
  Deletes every unmanaged default-set device whose name is not in the keep list. Never touches a device whose name parses as `[set] ...` for any registered or unregistered set. A keep list is mandatory: at least one `--keep` is required, or `--keep-nothing` must be passed explicitly to delete every unmanaged simulator. `prune --yes` with neither refuses before planning anything, so a missing or mistyped `--keep` can't wipe every hand-made simulator on the machine. Booted devices are skipped unless `--shutdown`. Prints the plan; requires `--yes`.
- `ui [--all] [--port 8421]`
  Ensures a baguette server is running (starts `baguette serve --port` detached and records the pid), boots the project's devices, opens `http://127.0.0.1:<port>/farm?q=%5B<id>%5D` with `open`. `--all` opens the unfiltered farm.
- `doctor`
  Checks: Xcode and simctl reachable, at least one iOS runtime, `baguette` on PATH and, if it's already running, whether it supports the `?q=` filter (probes the running server's `/farm/farm-filter.js` for `searchFromQuery`), registry entries whose project path no longer exists, devices with `[set]` names that belong to no registered set, stale leases.

## CLAUDE.md section

Template in `references/claude-md-section.md`, rendered with the set id:

```
<!-- simset:start -->
## iOS simulators (managed by sim-set)

This project owns the simulator set `[triton]`. Rules for any agent working here:

- Before using a simulator, claim one: `simset claim phone --label "<what you are doing>" --boot --json`. Aliases: `phone`, `phone-small`, `tablet`, or an exact device type name such as `"iPhone 17 Pro Max"`. With `--boot`, `claim` does not return until the device has finished booting (SpringBoard is up), so it's safe to run a screenshot or AXe command immediately after.
- Use the returned `udid` everywhere: `xcodebuild -destination "platform=iOS Simulator,id=<udid>"`, AXe `--udid <udid>`, XcodeBuildMCP `simulatorId`, `xcrun simctl <cmd> <udid>`.
- Never create, delete, erase, or boot simulators outside this set, and never pick a device by bare name like "iPhone 17 Pro". Always go through `simset`.
- If every device of a size is taken: `simset claim <size> --wait 300` waits up to 5 minutes for one to free up; `simset claim <size> --grow` provisions another. Passing both waits first and only grows if the wait times out.
- Leases expire after 4 hours by default (`--ttl HOURS` to change it). Working longer than that? Run `simset claim --renew <udid>` before it expires, or another agent may be handed the same device.
- Release when done: `simset release --mine`.
- `simset list` shows this project's devices and who holds them. `simset ui` opens a live view of them in the browser.
- If `claim` warns about `SIMSET_OWNER_PID`, it means no ancestor process named `claude` could be found, so the lease was bound to a short-lived pid and may be reaped early. Set the `SIMSET_OWNER_PID` environment variable to a long-lived process id to fix this.
<!-- simset:end -->
```

## Baguette fork

Repository `nbonatsakis/baguette`, branch `farm-name-filter`, kept rebasable on upstream `main` for a PR.

Change set, minimal:

- `farm-filter.js`: add a `name` string to filter state; predicate also requires `device.name` to contain it (case-insensitive) when set.
- `farm-app.js`: on load, read `URLSearchParams` for `name` (substring) and `udid` (comma list); seed the filter; keep the URL in sync when the user edits the filter so a window is shareable.
- `farm-views.js` and `farm.css`: a text input labeled "Name" at the top of the filter rail.
- Tests under `Tests/Web/` for the predicate and URL seeding, following the existing test style.
- `baguette --version` unchanged; `doctor` detects the fork by fetching `/farm` and checking for the name input's element id.

The skill's SKILL.md documents: install via `~/dev/forks/baguette` and `swift build -c release`, symlink the binary to `~/.local/bin/baguette` (or wherever `install.sh` puts `simset`), and that `brew install baguette` becomes sufficient once upstream merges.

## Error handling

- Every error path follows the same `--json` contract: without `--json`, a human-readable `error: <message>` line goes to stderr; with `--json`, `{"error": "<message>", "exit_code": N}` goes to stdout instead, where `N` is the process exit code. This covers `main`'s exception handlers (`SimctlError`, `UsageError`, `StateError`, `PlanningError`, `LeaseError`, a malformed CLAUDE.md), `claim`'s contention message, and `ui`'s missing/timeout messages — no error path writes to stdout in text mode or to stderr in `--json` mode.
- Any simctl failure raises `SimctlError` with the command and stderr; the CLI reports it (per the contract above) and exits 2.
- Lease files are written atomically (write temp, rename) under the flock. A crashed agent leaves a lease that the next `claim` reaps because its PID is dead.
- `configure` never deletes devices. Only `remove`, `destroy`, and `prune` delete, and each prints its plan and requires `--yes`.
- `prune` cannot delete a managed device even if the registry is missing, because it decides by name pattern, not registry. It also refuses to run with an empty keep list unless `--keep-nothing` is passed, so a missing or mistyped `--keep` can't sweep every hand-made simulator on the machine.
- `ui` degrades: if `baguette` is not on PATH, it prints the install instructions and exits 1 after still booting the devices.

## Testing

- `tests/test_simset.py` (stdlib `unittest`): naming round-trip; provision planning idempotency; prune planning excludes managed names and booted devices; lease claim picks free device, reaps dead PID, respects TTL, `--grow` naming; CLAUDE.md injection idempotency and preservation of surrounding text; runtime resolution (`latest`, prefix). All through a `FakeSimctl` that records calls and returns canned `simctl list -j` documents.
- `tests/smoke.sh`: against the real simctl, `configure --id simset-smoke` in a temp project, `claim phone --json`, `list --json`, `release --mine`, `destroy --yes`, asserting no `[simset-smoke]` devices remain.
- Baguette fork: existing `Tests/Web` runner plus new tests; manual check that `/farm?q=%5Bsimset-smoke%5D` shows only the smoke set.

## Out of scope

- Any hook-based enforcement.
- Driving the UI (taps, swipes): AXe, idb, XcodeBuildMCP, baguette do that.
- Custom `simctl --set` device sets.
- A native macOS window; the browser farm view is the UI.
- Android emulators.
