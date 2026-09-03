<!-- simset:start -->
## iOS simulators (managed by sim-set)

This project owns the simulator set `[{{SET_ID}}]`. Rules for any agent working here:

- Before using a simulator, claim one: `simset claim phone --label "<what you are doing>" --boot --json`. Aliases: `phone`, `phone-small`, `tablet`, or an exact device type name such as `"iPhone 17 Pro Max"`.
- Use the returned `udid` everywhere: `xcodebuild -destination "platform=iOS Simulator,id=<udid>"`, AXe `--udid <udid>`, XcodeBuildMCP `simulatorId`, `xcrun simctl <cmd> <udid>`.
- Never create, delete, erase, or boot simulators outside this set, and never pick a device by bare name like "iPhone 17 Pro". Always go through `simset`.
- If every device of a size is taken: `simset claim <size> --grow` provisions another, `--wait 300` waits for one.
- Release when done: `simset release --mine`.
- `simset list` shows this project's devices and who holds them. `simset ui` opens a live view of them in the browser.
<!-- simset:end -->
