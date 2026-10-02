# sim-set

`simset` namespaces iOS Simulator devices per project (`[<set-id>] <Device Type>`) so several coding agents can build and test different apps at once without booting, erasing, or stealing each other's simulators. Devices stay in the default CoreSimulator device set, so xcodebuild, Xcode and Device Hub, AXe, MobileBuildMCP, and idb all keep working unchanged.

## Install

```bash
git clone https://github.com/nbonatsakis/sim-set ~/dev/ai/skills/sim-set
~/dev/ai/skills/sim-set/scripts/install.sh
```

## Learn more

See `SKILL.md` for usage and `references/commands.md` for the full command
reference. The design rationale lives in
`docs/superpowers/specs/2026-09-03-sim-set-design.md`. The 2026-10 Xcode 27
update (runtime pinning, `migrate`, shutdown on release, Device Hub replacing
the baguette live view) is described in `SKILL.md`.
