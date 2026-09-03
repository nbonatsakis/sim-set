---
name: sim-set
description: Manage project-scoped iOS simulator sets so several coding agents can work on several apps at once without trampling each other's simulators. Use whenever a task involves picking, booting, creating, deleting, or listing iOS simulators; configuring a project for simulators; pruning the global simulator list; or opening a live view of a project's simulators. Triggers on "simulator", "simulators", "simctl", "sim set", "simset", "which simulator", "boot a simulator", "clean up simulators", "prune simulators", "configure simulators for this project", "simulator UI", "baguette".
---

# sim-set

`simset` namespaces ordinary CoreSimulator devices by name (`[<set-id>] <Device Type>`), so every existing tool (xcodebuild, Xcode, AXe, XcodeBuildMCP, idb, the Claude Code Desktop simulator pane) keeps working while each project owns its own devices and agents lease them exclusively.

Devices live in the default device set on purpose: `xcodebuild` cannot target devices in a custom `simctl --set` set, and AXe and XcodeBuildMCP cannot see them either.

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

`configure` writes `.simset.json` (commit it), creates any missing roster devices on the newest iOS runtime, registers the set in `~/.simset/registry.json`, and injects a marker-delimited section into the project's `CLAUDE.md` telling agents to claim devices through `simset`. Re-running is safe and never deletes anything.

Default roster: `iPhone 17 Pro` (`phone`), `iPhone 16e` (`phone-small`), `iPad Pro 13-inch (M5)` (`tablet`).

## Agent workflow (what the injected CLAUDE.md section says)

```bash
UDID=$(simset claim phone --label "fix onboarding" --boot --json | jq -r .udid)
xcodebuild -scheme App -destination "platform=iOS Simulator,id=$UDID" build
axe describe-ui --udid "$UDID"
simset release --mine
```

`claim --boot` does not return until the device has finished booting (via `xcrun simctl bootstatus -b`), so it's safe to screenshot or drive the UI immediately after. If every device of a size is leased: `simset claim phone --wait 300` waits up to 5 minutes for one to free up; `--grow` provisions `[id] iPhone 17 Pro #2` instead. Passing both waits first and only grows if the wait times out. Leases expire after 4 hours (`--ttl`) or when the owning `claude` process exits; run `simset claim --renew <udid>` before that if you're still working. If `claim` warns about `SIMSET_OWNER_PID`, it means no `claude` ancestor process was found — set that environment variable to a long-lived pid.

## Manage simulators globally

```bash
simset list --all                                   # every simulator grouped by set + unmanaged
simset prune --keep "iPhone 17 Pro" --keep "iPad Pro 13-inch (M5)"   # dry run
simset prune --keep "iPhone 17 Pro" --yes           # delete the rest of the unmanaged devices
simset prune --keep-nothing --yes                   # delete every unmanaged simulator
simset leases --reap                                # drop stale leases
simset doctor                                       # Xcode, runtimes, baguette, registry, leases
```

`prune` never touches a device whose name matches `[set] ...`, registered or not. It also refuses to run at all unless you pass at least one `--keep`, or `--keep-nothing` to explicitly opt into deleting every unmanaged simulator.

## Live view

```bash
simset ui          # boots this project's devices, opens the baguette farm filtered to [id]
simset ui --all    # unfiltered farm
```

Requires baguette with the `?q=` farm filter. Until upstream merges it, build the fork:

```bash
git clone https://github.com/nbonatsakis/baguette ~/dev/forks/baguette
cd ~/dev/forks/baguette && git checkout farm-name-filter && swift build -c release
ln -sf ~/dev/forks/baguette/.build/release/baguette ~/.local/bin/baguette
```

After the merge, `brew install baguette` is enough. `simset doctor` reports whether the running baguette supports the filter.

## Reference

- `references/commands.md` for every command, flag, exit code, and JSON shape.
- `references/claude-md-section.md` is the template injected into CLAUDE.md.
- All machine state lives in `~/.simset` (override with `SIMSET_HOME`). Set `SIMSET_OWNER_PID` to control which process a lease is bound to.
