#!/usr/bin/env bash
# Links this skill into ~/.claude/skills and puts `simset` on PATH.
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILLS_HOME="${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}"
BIN_DIR="${SIMSET_BIN_DIR:-$HOME/.local/bin}"

mkdir -p "$SKILLS_HOME" "$BIN_DIR"
chmod +x "$SKILL_DIR/scripts/simset.py"
ln -sfn "$SKILL_DIR" "$SKILLS_HOME/sim-set"
ln -sfn "$SKILL_DIR/scripts/simset.py" "$BIN_DIR/simset"

echo "skill:  $SKILLS_HOME/sim-set -> $SKILL_DIR"
echo "binary: $BIN_DIR/simset"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "note: $BIN_DIR is not on PATH; add it so agents can run 'simset'" ;;
esac
"$BIN_DIR/simset" doctor || true
