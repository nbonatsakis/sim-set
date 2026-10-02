<!-- simset:start -->
## iOS simulators (managed by sim-set)

This project owns the simulator set `[{{SET_ID}}]`, pinned to the iOS runtime in `.simset.json`. Other agents may be working in this repo at the same time, so every simulator goes through `simset`.

Claim, use, release:

- Claim before touching a simulator: `simset claim phone --label "<what you are doing>" --boot --json`. Aliases are `phone`, `phone-small`, `tablet`, or an exact device type such as `"iPhone 17 Pro Max"`. `--boot` returns only once the device has finished booting.
- Use the returned `udid` and `destination` everywhere. Never pick a device by name and never use the `booted` alias: several simulators are usually booted and the same device names exist on more than one runtime.
- If every device of a size is taken, `--wait 300` waits for one and `--grow` provisions another (both: wait first, then grow).
- Leases last 4 hours (`--ttl`); renew with `simset claim --renew <udid>`. When done, `simset release --mine`, which also shuts the device down.
- If `claim` says the devices are on another runtime, run `simset migrate --yes`. Never create, delete, erase, or boot simulators outside this set.

Tools, always with the claimed udid:

- Build and test: `xcodebuild -scheme <Scheme> -destination "<destination>" build` (or `test`). After tests, check that the executed test count is above zero.
- Build, install, launch with log capture: `mobilebuildmcp simulator build-and-run --simulator-id <udid> --scheme <Scheme> --project-path <path>`. Pass `--simulator-id` on every `mobilebuildmcp` call; never rely on its session defaults or on "first booted simulator".
- Drive the UI: `mobilebuildmcp ui-automation snapshot-ui --simulator-id <udid>` returns compact element refs, then `tap --element-ref eN`, `type-text`, `swipe`, or `batch`. Run `wait-for-ui --predicate settled` before tapping a screen that animates. Use `axe <cmd> --udid <udid>` for anything the snapshot doesn't cover.
- Screenshots and appearance: `xcrun simctl io <udid> screenshot <file>`, `xcrun simctl ui <udid> appearance dark|light`.
- Xcode's own tools (`xcrun mcpbridge`, the `device-interaction` skill): pass the claimed udid as `deviceIdentifier`. Never let them fall back to the "current destination".

Xcode 27 facts:

- There is no Simulator.app; Device Hub replaced it. Never run `open -a Simulator`. Everything above works headless. To watch a device, `open -a DeviceHub`.
- Tab bar items are `AXRadioButton`s whose value is 1 when selected. Liquid Glass controls may expose no selected trait, so verify a selection by its effect.
- `simctl privacy` has no notifications service. To reset push permission, uninstall and reinstall the app.
- A local `.storekit` configuration only applies when Xcode launches the app (Run, or Xcode MCP `RunProject`). An app launched with `simctl launch` or `mobilebuildmcp` hits the real sandbox.
- If `claim` warns about `SIMSET_OWNER_PID`, no `claude` ancestor process was found. Set `SIMSET_OWNER_PID` to a long-lived pid so the lease isn't reaped early.
<!-- simset:end -->
