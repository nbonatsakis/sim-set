---
name: sim-set
description: Manage project-scoped iOS simulator sets so several coding agents can work on several apps at once without trampling each other's simulators. Use whenever a task involves picking, booting, creating, deleting, or listing iOS simulators; configuring a project for simulators; pruning the global simulator list; or moving a project to a new iOS runtime. Triggers on "simulator", "simulators", "simctl", "sim set", "simset", "which simulator", "boot a simulator", "clean up simulators", "prune simulators", "configure simulators for this project", "migrate simulators", "iOS 27 simulator".
---

# sim-set

`simset` namespaces ordinary CoreSimulator devices by name (`[<set-id>] <Device Type>`), so every existing tool (xcodebuild, Xcode and Device Hub, AXe, MobileBuildMCP, idb) keeps working while each project owns its own devices and agents lease them exclusively.

Devices live in the default device set on purpose: `xcodebuild` cannot target devices in a custom `simctl --set` set, and AXe and MobileBuildMCP cannot see them either.

## Install

```bash
git clone https://github.com/nbonatsakis/sim-set ~/dev/ai/skills/sim-set
~/dev/ai/skills/sim-set/scripts/install.sh
```

That links the skill into `~/.claude/skills/sim-set` and `simset` into `~/.local/bin`. Python 3.11+ and Xcode with an iOS runtime are required. Nothing else.

## Configure a project

Run inside the project (or pass `--project <path>`):

```bash
simset configure                       # id = directory name, default roster
simset configure --id ck --roster "iPhone 17 Pro" --roster "iPad mini (A17 Pro)"
```

`configure` writes `.simset.json` (commit it), pins the set to the newest installed iOS runtime (major.minor, e.g. `27.0`; `--runtime 26.3` to choose), creates any missing roster devices on it, registers the set in `~/.simset/registry.json`, and injects a marker-delimited section into the project's `CLAUDE.md` telling agents how to claim and drive devices (sim-set rules, Xcode 27 facts, MobileBuildMCP/AXe usage). For a repo you don't own, `--claude-md CLAUDE.local.md` keeps the instructions out of the shared file. Re-running is safe and never deletes anything; it keeps the existing pin.

Default roster: `iPhone 17 Pro` (`phone`), `iPhone 17e` (`phone-small`), `iPad Pro 13-inch (M5)` (`tablet`).

### Moving to a new Xcode runtime

`claim` only hands out devices on the pinned runtime. After installing a new iOS runtime:

```bash
simset migrate --runtime 27.0          # dry run: what would move
simset migrate --runtime 27.0 --yes    # repin, then `xcrun simctl upgrade` each unleased device in place
```

`migrate` keeps names and udids (no duplicates), skips leased devices, and can't downgrade.

## Agent workflow (what the injected CLAUDE.md section says)

```bash
CLAIM=$(simset claim phone --label "fix onboarding" --boot --json)
UDID=$(jq -r .udid <<<"$CLAIM"); DEST=$(jq -r .destination <<<"$CLAIM")
xcodebuild -scheme App -destination "$DEST" build
mobilebuildmcp ui-automation snapshot-ui --simulator-id "$UDID"
simset release --mine                  # also shuts the device down
```

`claim --boot` does not return until the device has finished booting (via `xcrun simctl bootstatus -b`), so it's safe to screenshot or drive the UI immediately after. If every device of a size is leased: `simset claim phone --wait 300` waits up to 5 minutes for one to free up; `--grow` provisions `[id] iPhone 17 Pro #2` instead. Passing both waits first and only grows if the wait times out. Leases expire after 4 hours (`--ttl`) or when the owning `claude` process exits; run `simset claim --renew <udid>` before that if you're still working. If `claim` warns about `SIMSET_OWNER_PID`, it means no `claude` ancestor process was found — set that environment variable to a long-lived pid.

## Manage simulators globally

```bash
simset list --all                                   # every simulator grouped by set + unmanaged
simset prune --keep "iPhone 17 Pro" --keep "iPad Pro 13-inch (M5)"   # dry run
simset prune --keep "iPhone 17 Pro" --yes           # delete the rest of the unmanaged devices
simset prune --keep-nothing --yes                   # delete every unmanaged simulator
simset leases --reap                                # drop stale leases, shut their devices down
simset leases --reap --shutdown-idle                # also shut down every booted [set] device nobody leases
simset doctor                                       # runtimes, axe + mobilebuildmcp, runtime pin, registry, leases
```

`prune` never touches a device whose name matches `[set] ...`, registered or not. It also refuses to run at all unless you pass at least one `--keep`, or `--keep-nothing` to explicitly opt into deleting every unmanaged simulator.

## Device Hub input repair

While Xcode 27's Device Hub runs, it attaches a HID daemon to every booted simulator and iOS 27 then disconnects the legacy input services AXe, MobileBuildMCP, and baguette inject into: hardware buttons die and taps can report success while landing nowhere. `claim --boot` checks `notifyutil -g com.apple.coredevice.dtuhidd.active` and, if set, clears it and restarts backboardd (the repair from tddworks/baguette #77); JSON gains `"healed": true`. `--no-heal` skips it. `simset heal <udid|alias|all>` does it on demand (restarts SpringBoard, so running apps die). Separately, short-lived tap commands lose their first event to the daemon's activation gap (AXe #71): pass `--post-delay 0.5` to `mobilebuildmcp ui-automation tap`, and set `AXE_HID_STABILIZATION_MS=250` for direct `axe`. Hardware buttons only work through `baguette press --udid <udid> --button home`.

## Watching devices

Xcode 27 has no Simulator.app; `open -a DeviceHub` shows every simulator in one window. Agents never need a window: simctl, AXe, and MobileBuildMCP drive devices headless. (The old `simset ui` baguette farm view was removed in favor of Device Hub.)

## Reference

- `references/commands.md` for every command, flag, exit code, and JSON shape.
- `references/claude-md-section.md` is the template injected into CLAUDE.md.
- All machine state lives in `~/.simset` (override with `SIMSET_HOME`). Set `SIMSET_OWNER_PID` to control which process a lease is bound to.
