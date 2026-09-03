#!/usr/bin/env bash
# End-to-end check against the real simctl. Creates and destroys a throwaway [simset-smoke] set.
set -euo pipefail

SIMSET="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/scripts/simset.py"
PROJECT="$(mktemp -d)/smoke-app"
export SIMSET_HOME="$(mktemp -d)"
mkdir -p "$PROJECT"
cleanup() { "$SIMSET" --project "$PROJECT" destroy --yes >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "== configure"
"$SIMSET" --project "$PROJECT" configure --id simset-smoke --roster "iPhone 17 Pro" --json | tee /dev/stderr | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["id"]=="simset-smoke"; assert len(d["created"])==1'
test -f "$PROJECT/.simset.json"
grep -q "simset:start" "$PROJECT/CLAUDE.md"

echo "== claim + boot"
CLAIM=$("$SIMSET" --project "$PROJECT" claim phone --label smoke --boot --json)
UDID=$(echo "$CLAIM" | python3 -c 'import json,sys; print(json.load(sys.stdin)["udid"])')
xcrun simctl list devices -j | python3 -c "import json,sys; devs=[d for v in json.load(sys.stdin)['devices'].values() for d in v]; d=[x for x in devs if x['udid']=='$UDID'][0]; assert d['name']=='[simset-smoke] iPhone 17 Pro', d; assert d['state']=='Booted', d"

echo "== xcodebuild can see it"
xcrun xcodebuild -version >/dev/null
xcrun simctl io "$UDID" screenshot --type=png "$SIMSET_HOME/shot.png" >/dev/null
test -s "$SIMSET_HOME/shot.png"

echo "== second claim contends, grow provisions #2"
set +e; "$SIMSET" --project "$PROJECT" claim phone --json >/dev/null; CODE=$?; set -e
test "$CODE" -eq 3
"$SIMSET" --project "$PROJECT" claim phone --grow --json | python3 -c 'import json,sys; assert json.load(sys.stdin)["name"]=="[simset-smoke] iPhone 17 Pro #2"'

echo "== release + list"
"$SIMSET" --project "$PROJECT" release --mine --json | python3 -c 'import json,sys; assert len(json.load(sys.stdin)["released"])==2'
"$SIMSET" --project "$PROJECT" list --json | python3 -c 'import json,sys; d=json.load(sys.stdin); assert len(d["devices"])==2; assert all(x["lease"] is None for x in d["devices"])'

echo "== destroy"
"$SIMSET" --project "$PROJECT" destroy --yes --json | python3 -c 'import json,sys; assert len(json.load(sys.stdin)["deleted"])==2'
if xcrun simctl list devices | grep -q "\[simset-smoke\]"; then echo "leftover devices"; exit 1; fi
test ! -f "$PROJECT/.simset.json"
echo "SMOKE OK"
