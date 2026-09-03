<!-- simset:start -->
## iOS simulators (managed by sim-set)

This project owns the simulator set `[{{SET_ID}}]`. Rules for any agent working here:

- Before using a simulator, claim one: `simset claim phone --label "<what you are doing>" --boot --json`. Aliases: `phone`, `phone-small`, `tablet`, or an exact device type name such as `"iPhone 17 Pro Max"`. With `--boot`, `claim` does not return until the device has finished booting (SpringBoard is up), so it's safe to run a screenshot or AXe command immediately after.
- Use the returned `udid` everywhere: `xcodebuild -destination "platform=iOS Simulator,id=<udid>"`, AXe `--udid <udid>`, XcodeBuildMCP `simulatorId`, `xcrun simctl <cmd> <udid>`.
- Never create, delete, erase, or boot simulators outside this set, and never pick a device by bare name like "iPhone 17 Pro". Always go through `simset`.
- If every device of a size is taken: `simset claim <size> --wait 300` waits up to 5 minutes for one to free up; `simset claim <size> --grow` provisions another. Passing both waits first and only grows if the wait times out.
- Leases expire after 4 hours by default (`--ttl HOURS` to change it). Working longer than that? Run `simset claim --renew <udid>` before it expires, or another agent may be handed the same device.
- Release when done: `simset release --mine`.
- `simset list` shows this project's devices and who holds them. `simset ui` opens a live view of them in the browser.
- If `claim` warns about `SIMSET_OWNER_PID`, it means no ancestor process named `claude` could be found, so the lease was bound to a short-lived pid and may be reaped early. Set the `SIMSET_OWNER_PID` environment variable to a long-lived process id to fix this.
<!-- simset:end -->
