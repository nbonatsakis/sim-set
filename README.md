# sim-set

`simset` namespaces iOS Simulator devices per project (`[<set-id>] <Device Type>`) so several coding agents can build and test different apps at once without booting, erasing, or stealing each other's simulators. Devices stay in the default CoreSimulator device set, so xcodebuild, Xcode, AXe, XcodeBuildMCP, idb, and the Claude Code Desktop simulator pane all keep working unchanged.

## Install

```bash
git clone https://github.com/nbonatsakis/sim-set ~/dev/ai/skills/sim-set
~/dev/ai/skills/sim-set/scripts/install.sh
```

## Learn more

See `SKILL.md` for usage and `references/commands.md` for the full command
reference. The design rationale lives in
`docs/superpowers/specs/2026-09-03-sim-set-design.md`. `simset ui` additionally
depends on a small baguette fork until its farm name-filter change merges
upstream — see `SKILL.md`'s Live view section.
