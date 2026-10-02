<!-- simset:start -->
## iOS simulators (managed by sim-set)

This project owns the simulator set `[{{SET_ID}}]`, pinned to the iOS runtime in `.simset.json`. Other agents may be working in this repo at the same time, so every simulator goes through `simset`.

Claim, use, release:

- Claim before touching a simulator: `simset claim phone --label "<what you are doing>" --boot --json`. Aliases are `phone`, `phone-small`, `tablet`, or an exact device type such as `"iPhone 17 Pro Max"`. `--boot` returns only once the device has finished booting, and it also reclaims input from Device Hub (see below).
- Use the returned `udid` and `destination` everywhere. Never pick a device by name and never use the `booted` alias: several simulators are usually booted and the same device names exist on more than one runtime.
- Scripts and drivers that run once per step use `simset claim phone --reuse --boot --json`: it returns the device you already hold instead of claiming another. Don't cache udids in files; a cached udid outlives its lease.
- If every device of a size is taken, `--wait 300` waits for one and `--grow` provisions another (both: wait first, then grow).
- Leases last 4 hours (`--ttl`); renew with `simset claim --renew <udid>`. When done, `simset release --mine`, which also shuts the device down.
- If `claim` says the devices are on another runtime, run `simset migrate --yes`. Never create, delete, erase, or boot simulators outside this set.

Tools, always with the claimed udid:

- Build and test: `xcodebuild -scheme <Scheme> -destination "<destination>" build` (or `test`). After tests, check that the executed test count is above zero.
- Build, install, launch with log capture: `mobilebuildmcp simulator build-and-run --simulator-id <udid> --scheme <Scheme> --project-path <path>`. Pass `--simulator-id` on every `mobilebuildmcp` call; never rely on its session defaults or on "first booted simulator".
- Drive the UI: start with `mobilebuildmcp ui-automation snapshot-ui --simulator-id <udid>` (compact element refs; `wait-for-ui` fails until a snapshot exists), then `tap --element-ref eN --post-delay 0.5`, `type-text`, `swipe`. Always pass `--post-delay 0.5` to taps: without it, taps on Xcode 27 report success and land nowhere (AXe #71). Run `wait-for-ui --predicate settled` before tapping a screen that animates, and re-snapshot after every tap to confirm it took effect. Use `axe <cmd> --udid <udid>` for anything the snapshot doesn't cover (`AXE_HID_STABILIZATION_MS=250` is set globally for the same bug).
- Hardware buttons (home, lock, side): `baguette press --udid <udid> --button home`. AXe and MobileBuildMCP button presses don't reach iOS 27 devices.
- Screenshots and appearance: `xcrun simctl io <udid> screenshot <file>`, `xcrun simctl ui <udid> appearance dark|light`.
- Xcode's own tools (`xcrun mcpbridge`, the `device-interaction` skill): pass the claimed udid as `deviceIdentifier`. Never let them fall back to the "current destination".

Xcode 27 facts:

- There is no Simulator.app; Device Hub replaced it. Never run `open -a Simulator`. Everything above works headless. To watch a device, `open -a DeviceHub`.
- While Device Hub is running it attaches to every booted simulator and cuts off the input path the tools above use. `simset claim --boot` repairs that automatically. If taps or buttons stop working mid-session (for example after Device Hub was relaunched), run `simset heal <udid>`. It restarts SpringBoard, so relaunch the app afterwards. Never quit Device Hub: that shuts down every booted simulator, including other agents'.
- Tab bar items are `AXRadioButton`s whose value is 1 when selected. Liquid Glass controls may expose no selected trait, so verify a selection by its effect.
- `simctl privacy` has no notifications service. To reset push permission, uninstall and reinstall the app.
- A local `.storekit` configuration only applies when Xcode launches the app (Run, or Xcode MCP `RunProject`). An app launched with `simctl launch` or `mobilebuildmcp` hits the real sandbox.
- If `claim` warns about `SIMSET_OWNER_PID`, no `claude` ancestor process was found. Set `SIMSET_OWNER_PID` to a long-lived pid so the lease isn't reaped early.
<!-- simset:end -->
