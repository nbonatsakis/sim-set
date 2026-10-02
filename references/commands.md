# simset command reference

Derived from `scripts/simsetlib/cli.py`. Every subcommand accepts the two global
options both before and after the subcommand name (e.g. `simset --json list` and
`simset list --json` are equivalent):

- `--project <path>` — project root to operate on. Default: walk up from the
  current directory looking for `.simset.json`; most commands error if none is
  found (`configure` is the exception — it will create one).
- `--json` — emit machine-readable JSON instead of human-readable lines. Human
  output goes to stdout, one line per item; JSON output is a single
  `json.dumps(..., indent=2)` document.

Exit codes, used consistently across subcommands:

- `0` — success (`EXIT_OK`)
- `1` — user or state error: bad arguments, no manifest, unknown device type,
  a destructive command run without `--yes` (`EXIT_USER`)
- `2` — an `xcrun simctl` call failed (`EXIT_SIMCTL`)
- `3` — contention: `claim` found nothing free and was not told to `--grow` or
  `--wait` (`EXIT_CONTENTION`)

Every error path follows the same `--json` contract: without `--json`, a
human-readable `error: <message>` line goes to **stderr** (not stdout), so
piping stdout is always safe even on failure. With `--json`, the error is a
JSON document on **stdout** instead: `{"error": "<message>", "exit_code": N}`,
where `N` matches the process exit code. This applies uniformly — the
exception handlers in `main` (missing manifest, unknown device type, lease
errors, `SimctlError`, a malformed CLAUDE.md), `claim`'s contention message,
and `migrate`/`prune` dry runs all go through the same helper.

## configure

    simset configure [--id ID] [--roster "Device Type"]... [--runtime 27.0] [--claude-md FILE] [--no-claude-md]

Creates or updates the project's `.simset.json`, provisions any roster device
that does not already exist, registers the set in `~/.simset/registry.json`,
and (unless `--no-claude-md`) injects the marker-delimited section from
`references/claude-md-section.md` into the project's `CLAUDE.md`. Never
deletes a device. Safe to re-run.

- `--id ID` — set id. Default: the existing manifest's id, else the project
  directory name.
- `--roster "Device Type"` — repeatable. Replaces the whole roster. Each entry
  is auto-aliased: the first `iPhone*` type gets alias `phone`, the next
  `iPhone*` gets `phone-small`, the first `iPad*` type gets `tablet`; any type
  beyond those (a second tablet, a third phone) gets no alias and must be
  claimed by its exact type name. Omit this flag to keep the existing roster,
  or fall back to the default roster on first configure.
- `--runtime 27.0` — iOS runtime to pin (a version prefix). Default: the
  existing manifest's pin, else the newest installed iOS runtime, stored as
  major.minor. A legacy `"latest"` manifest is converted to a concrete pin on
  the next configure.
- `--claude-md FILE` — instructions file to inject into, relative to the
  project. Default `CLAUDE.md`; use `CLAUDE.local.md` for repos you don't own.
- `--no-claude-md` — skip the instructions edit.

Devices of the set that sit on another runtime are reported in `off_runtime`
and are not duplicated; move them with `simset migrate --yes`.

Default roster when none exists yet:

- `iPhone 17 Pro` → alias `phone`
- `iPhone 17e` → alias `phone-small`
- `iPad Pro 13-inch (M5)` → alias `tablet`

JSON output:

    {
      "id": "triton",
      "project": "/path/to/project",
      "runtime": "27.0",
      "pinned": "27.0",
      "created": [{"name": "[triton] iPhone 17 Pro", "udid": "..."}],
      "roster": [{"type": "iPhone 17 Pro", "alias": "phone"}, ...],
      "claude_md": "/path/to/project/CLAUDE.md",
      "off_runtime": []
    }

`created` is empty when every roster device already exists. `claude_md` is
`null` when `--no-claude-md` was passed.

## list

    simset list [--all]

Without `--all`, requires a configured project and lists only its devices.
With `--all`, lists every simulator on the machine, grouped by set id, plus an
`unmanaged` group for devices that don't match the `[set] Type` naming
convention. Marks leases as stale the same way `doctor` and `leases` do.

Each device row (used here and in every other command's `devices`/`delete`/
`created` lists that include full device info):

    {
      "udid": "...",
      "name": "[triton] iPhone 17 Pro",
      "type": "iPhone 17 Pro",
      "state": "Booted",
      "runtime": "iOS-26-3",
      "available": true,
      "lease": {"owner_pid": 4242, "owner_source": "env", "label": "onboarding fix", "expires_at": "...", "stale": false}
    }

`lease` is `null` when nothing has claimed the device. `type` is looked up
from the live device type identifier and falls back to the type name parsed
out of the device's name.

JSON output, project mode:

    {"id": "triton", "project": "/path/to/project", "devices": [<device row>, ...]}

JSON output, `--all`:

    {
      "sets": {
        "triton": {"project": "/path/to/project", "devices": [<device row>, ...]},
        "other-set": {"project": null, "devices": [...]}
      },
      "unmanaged": [<device row>, ...]
    }

`project` under a set is `null` when the set has devices but no registry entry
(an orphan — see `doctor`).

## claim

    simset claim <phone|phone-small|tablet|"Device Type"> [--label TEXT] [--boot] [--wait SECONDS] [--grow] [--ttl HOURS] [--renew UDID]

Leases one unleased device of the given size or exact type from the project's
set. `<target>` is resolved through the size-alias rules below, then must
name a type that is actually in the roster (add it first with `simset add` if
not). Only devices on the pinned runtime are candidates; if every device of
that type is on another runtime, `claim` exits `1` pointing at `simset
migrate` (with `--grow` it provisions a new device on the pinned runtime
instead).

- `--label TEXT` — free-text note shown in `list`/`leases` output.
- `--boot` — boot the claimed device if it isn't already `Booted`. `claim`
  does not return until `xcrun simctl bootstatus <udid> -b` confirms the
  device has finished booting (SpringBoard is up), so the returned udid is
  immediately usable for screenshots, AXe, and app installs.
- `--wait SECONDS` — if nothing is free, poll every 2 seconds until one frees
  up or the timeout elapses.
- `--grow` — if nothing is free, provision `[id] <type> #N` (next free index)
  and claim that instead. Capped at 5 grows per invocation (`UsageError` past
  that, in case something else keeps the device permanently contended).
- `--wait` and `--grow` together: `claim` polls until the `--wait` deadline
  first, and only falls through to `--grow` if the wait times out (or
  immediately, if no `--wait` was given). Passing both is "wait, then grow if
  still stuck" — it never grows before the wait is exhausted.
- If every matching device is still leased after `--wait`/`--grow` don't
  resolve it, `claim` exits `3` (see the error contract above).
- `--ttl HOURS` — lease lifetime, default `4.0`. A lease is stale once its
  owner PID is dead or `expires_at` has passed; `leases --reap` and every
  `claim` call drop stale leases before allocating.
- `--reuse` — if the caller already holds a live lease on a device of this
  type in the set (on the pinned runtime), renew and return it instead of
  claiming another; otherwise claim as usual. For drivers and scripts that are
  invoked once per step. The payload carries `"reused": true|false`. A reused,
  already-booted device is not healed (that would kill the running app).
- `--no-heal` — with `--boot`, skip the Device Hub input repair (see `heal`).
  By default, once booted, a device Device Hub has attached to is healed and
  the payload carries `"healed": true` (`false` when there was nothing to do).
- `--renew UDID` — instead of claiming, extend an existing lease's
  `expires_at` by `--ttl` hours. Errors (exit `1`) if the device no longer
  exists.

Owner identity for a new lease: `SIMSET_OWNER_PID` if set in the environment
(`owner_source: "env"`), else the nearest ancestor process named `claude`
(`"claude-ancestor"`), else the immediate parent PID (`"parent-pid"`) — see
`find_owner_pid` in `simsetlib/leases.py`. The `"parent-pid"` fallback binds
the lease to a process that, for a Claude Code Bash call, is dead before the
lease is ever read again, so `claim` also prints a warning in that case
(stderr, or a `"warning"` key in the JSON payload) pointing at
`SIMSET_OWNER_PID`.

JSON output (claim or renew) — a device row plus `set`:

    {
      "udid": "...", "name": "[triton] iPhone 17 Pro", "type": "iPhone 17 Pro",
      "state": "Booted", "runtime": "iOS-26-3", "available": true,
      "lease": {"owner_pid": 4242, "owner_source": "env", "label": "onboarding fix",
                "expires_at": "...", "stale": false},
      "set": "triton",
      "destination": "platform=iOS Simulator,id=..."
    }

`destination` is ready to pass to `xcodebuild -destination`.

## release

    simset release <udid> | --mine | --all [--keep-booted]

At least one of a `udid`, `--mine`, or `--all` is required (else exit `1`).
If more than one is given, `--mine` wins over `--all`, which wins over a
`udid`. `--mine` releases every lease owned by the calling agent's PID;
`--all` releases every lease in the current project's set, regardless of
owner. Released devices that are booted and no longer leased are shut down
unless `--keep-booted` is passed.

JSON output:

    {"released": ["udid1", "udid2"], "shutdown": ["udid1"]}

## leases

    simset leases [--reap [--shutdown-idle]]

Without `--reap`, lists every lease across every set (not scoped to a
project). With `--reap`, deletes stale ones, shuts down their devices if
booted, and reports what was removed. `--shutdown-idle` also shuts down every
booted `[set]` device (any set) that nobody leases; unmanaged devices are
never touched.

Lease record fields (also the schema of `~/.simset/leases/<udid>.json`):

    {
      "udid": "...", "name": "[triton] iPhone 17 Pro", "set_id": "triton",
      "owner_pid": 4242, "owner_source": "env", "label": "onboarding fix",
      "claimed_at": "2026-09-03T16:00:00Z", "expires_at": "2026-09-03T20:00:00Z"
    }

JSON output:

    {"leases": [{...lease fields..., "stale": false}, ...]}

`--reap` JSON output:

    {"reaped": [{...lease fields...}, ...], "shutdown": ["udid", ...]}

## boot / shutdown / erase

    simset boot <udid|alias|"Device Type"|all>
    simset shutdown <udid|alias|"Device Type"|all>
    simset erase <udid|alias|"Device Type">

All three are scoped to the current project's set; a `udid` outside the set
is rejected (exit `1`). `all` is accepted by `boot` and `shutdown` (every
device in the set) but not by `erase`, which always targets a single
resolved device or type match. `boot` does not return until
`xcrun simctl bootstatus <udid> -b` confirms the device has finished booting.
`erase` shuts the device down first if it is booted, then always issues
`simctl erase` (even if the device was already shut down).

JSON output (same shape for all three):

    {"action": "boot", "devices": [<device row>, ...]}

Human lines are `<action>: <state>  <udid>  <name>` per affected device.

## add

    simset add "Device Type" [--alias NAME]

Adds a device type to the project's roster (or updates the alias of an
existing roster entry for that type) and provisions the device if it doesn't
already exist. The type name must be a real `xcrun simctl list devicetypes`
name, or this exits `1`.

JSON output:

    {"created": [{"name": "[triton] iPad mini (A17 Pro)", "udid": "..."}], "roster": [<roster entry>, ...]}

`created` is empty and the human line reads `device already present` when the
device already exists.

## remove

    simset remove <udid|alias|"Device Type"> [--yes]

Deletes the matching device(s) from the project's set and drops the roster
entry for each removed type, but only if that type has no device of its own
still in the set. A roster entry for a type this command didn't touch is
never dropped, even if that type's device was deleted outside simset (Xcode,
a manual `simctl delete`, a failed `configure`) — the manifest keeps it so
the next `configure` reprovisions it. Without `--yes`, prints the delete
plan and exits `1` (dry run); with `--yes`, shuts down any booted target,
deletes it, releases its lease, and rewrites the manifest.

Dry-run JSON output:

    {"delete": [{"name": "...", "udid": "..."}, ...], "dry_run": true}

Applied JSON output:

    {"deleted": [{"name": "...", "udid": "..."}, ...], "roster": [<roster entry>, ...]}

## destroy

    simset destroy [--yes]

Deletes every device in the project's set, releases their leases, unregisters
the set, removes the `CLAUDE.md` section, and deletes `.simset.json`. Whole
sets only — there is no target argument. Without `--yes`, prints the plan and
exits `1`.

Dry-run JSON output:

    {"delete": [{"name": "...", "udid": "..."}, ...], "manifest": "/path/to/.simset.json", "dry_run": true}

Applied JSON output:

    {"deleted": [{"name": "...", "udid": "..."}, ...], "id": "triton"}

## prune

    simset prune (--keep "Device Type or full name"... | --keep-nothing) [--shutdown] [--yes]

Deletes unmanaged (non-`[set] ...`-named) default-set devices that don't
match the keep list. A device whose name parses as `[set] ...` is never
touched, whether or not that set is registered — `prune` decides by name
pattern alone. `--keep` matches by exact device name or by device type name;
repeatable. At least one `--keep` is required, or pass `--keep-nothing` to
explicitly opt into deleting every unmanaged simulator on the machine;
`prune --yes` with neither exits `1` before planning anything, so a missing
or mistyped `--keep` can never wipe every hand-made simulator by accident.
Booted unmanaged devices are skipped unless `--shutdown`, which also shuts
them down before deleting. Without `--yes`, prints the plan and exits `1`.

Dry-run JSON output:

    {
      "delete": [{"name": "...", "udid": "...", "state": "Shutdown"}, ...],
      "skipped_booted": [{"name": "...", "udid": "...", "state": "Booted"}, ...],
      "kept": [{"name": "...", "udid": "...", "state": "..."}, ...],
      "managed": [{"name": "[triton] iPhone 17 Pro", "udid": "...", "state": "..."}, ...],
      "dry_run": true
    }

Applied JSON output adds `"deleted": [{"name": "...", "udid": "..."}, ...]` to
the same payload.

## heal

    simset heal <udid|alias|device type|all>

For each matching booted device in the set: if
`notifyutil -g com.apple.coredevice.dtuhidd.active` reads `1` (Xcode 27
Device Hub attached its HID daemon), set it to `0` and run `launchctl
kickstart -k system/com.apple.backboardd`, then wait for SpringBoard to come
back under a new pid plus a 2 s settle. Order matters: clearing the state
after the restart kills the services again. Running apps are killed. The
repair holds until Device Hub is relaunched.

JSON output:

    {"devices": [{"udid": "...", "name": "[triton] iPhone 17 Pro", "healed": true}]}

## migrate

    simset migrate [--runtime 27.0] [--yes]

Moves the set's devices onto the pinned runtime in place with `xcrun simctl
upgrade <udid> <runtime>`, keeping names and udids. `--runtime` changes the
pin first. Without `--yes` it is a dry run (exit `1`, `dry_run: true`).
Booted devices are shut down before upgrading; leased devices are skipped;
devices on a newer runtime than the pin can't be downgraded and are reported.

JSON output:

    {
      "runtime": "27.0", "pinned": "27.0",
      "upgrade": [{"name": "...", "udid": "...", "state": "Shutdown"}],
      "skipped_leased": [], "newer_than_pin": [],
      "upgraded": [...]
    }

## doctor

    simset doctor

Runs these checks and exits `1` if any fail:

- `simctl` — `xcrun simctl` is reachable
- `ios-runtime` — at least one available iOS runtime exists
- `axe`, `mobilebuildmcp`, `baguette` — the recommended UI/run/button tools are on PATH
- `runtime-pin` — (inside a configured project) every device of the set is on
  the pinned runtime
- `registry` — every entry in `~/.simset/registry.json` still points at a
  project directory that exists
- `orphan-sets` — every `[set]`-named device on the machine belongs to a
  registered set
- `leases` — no stale leases outstanding

JSON output:

    {
      "ok": false,
      "checks": [
        {"name": "simctl", "ok": true, "detail": "xcrun simctl reachable"},
        {"name": "ios-runtime", "ok": true, "detail": "26.3"},
        {"name": "axe", "ok": true, "detail": "/opt/homebrew/bin/axe"},
        {"name": "mobilebuildmcp", "ok": true, "detail": "/opt/homebrew/bin/mobilebuildmcp"},
        {"name": "runtime-pin", "ok": true, "detail": "[triton] pinned to iOS 27.0"},
        {"name": "registry", "ok": true, "detail": "1 registered sets"},
        {"name": "orphan-sets", "ok": true, "detail": "every managed set is registered"},
        {"name": "leases", "ok": true, "detail": "0 active leases"}
      ]
    }

## Size alias resolution

`phone`, `phone-small`, and `tablet` (and any custom alias set via
`configure --roster` or `add --alias`) resolve against the project's roster
via `Manifest.type_for_alias` (`scripts/simsetlib/state.py`):

- an explicit alias match in the roster wins first
- else `phone` resolves to the roster's first `iPhone*` type
- else `phone-small` resolves to the roster's second `iPhone*` type, falling
  back to the first if there's only one
- else `tablet` resolves to the roster's first `iPad*` type
- anything else (including an alias with no match) is treated as a literal
  device type name. How that's checked depends on the command: `configure`
  and `add` validate it against the real `xcrun simctl list devicetypes`
  output (exit `1` if unknown); `claim` requires it to already be in the
  project's roster (exit `1` if not, with a hint to `simset add` it first);
  `boot`/`shutdown`/`erase` don't validate the name at all — they just report
  `no device matching '<target>' in set [<id>]` (exit `1`) if nothing in the
  set currently matches it

`configure --roster` assigns default aliases while building a custom roster
(`default_alias_for` in `scripts/simsetlib/cli.py`): the first `iPhone*`
entry gets `phone`, the next `iPhone*` gets `phone-small`, the first `iPad*`
entry gets `tablet`, and anything past those three gets no alias.

## Data shapes

`.simset.json` (project manifest, commit it):

    {
      "id": "triton",
      "roster": [
        {"type": "iPhone 17 Pro", "alias": "phone"},
        {"type": "iPhone 17e", "alias": "phone-small"},
        {"type": "iPad Pro 13-inch (M5)", "alias": "tablet"}
      ],
      "runtime": "27.0"
    }

`runtime` is a version prefix like `"27.0"`; resolution picks the newest
available iOS runtime whose version equals or starts with that prefix.
`"latest"` is still accepted (older manifests) and is replaced by a concrete
pin on the next `configure`.

`~/.simset/registry.json`:

    {"sets": {"triton": {"project": "/Users/nick/dev/projects/triton", "created_at": "2026-09-03T16:00:00Z"}}}

`~/.simset/leases/<udid>.json` — see the `leases` section above for the full
field list (`udid`, `name`, `set_id`, `owner_pid`, `owner_source`, `label`,
`claimed_at`, `expires_at`).

## Environment

- `SIMSET_HOME` — overrides `~/.simset` for all machine-local state
  (registry, leases).
- `SIMSET_OWNER_PID` — when set to a numeric pid, `claim` binds new leases to
  that pid instead of walking the process tree for an ancestor named
  `claude`.
- `CLAUDE_SKILLS_DIR` — where `scripts/install.sh` symlinks the skill
  (default `~/.claude/skills`).
- `SIMSET_BIN_DIR` — where `scripts/install.sh` symlinks the `simset` binary
  (default `~/.local/bin`).
