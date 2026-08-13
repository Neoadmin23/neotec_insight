#!/usr/bin/env bash
# v2.64.2 — a type check that cannot silently pass.
#
# The `setEvidence is not defined` crash shipped because the check was
#   npx tsc ... | grep <pattern> || echo CLEAN
# and typescript was not installed at the time. tsc produced nothing, grep
# matched nothing, and the `||` branch printed CLEAN. A check whose failure
# mode is indistinguishable from success is worse than no check at all.
#
# Two changes: the binary must exist, and the comparison is against a
# committed baseline of the errors this codebase already had, so a new one
# stands out instead of being lost among fifty old ones.
set -euo pipefail
cd "$(dirname "$0")/.."

[ -x node_modules/.bin/tsc ] || { echo "FAIL: tsc missing — run npm install." >&2; exit 1; }

cur="$(node_modules/.bin/tsc --noEmit -p tsconfig.json 2>&1 \
  | grep -E '^src/.*error TS' | sed -E 's/\([0-9]+,[0-9]+\)//' | sort -u || true)"
new="$(comm -13 scripts/typecheck-baseline.txt <(printf '%s\n' "$cur"))"

if [ -n "$new" ]; then
  echo "---- NEW type errors (not in baseline) ----" >&2
  printf '%s\n' "$new" >&2
  exit 1
fi
echo "typecheck OK — no new errors against baseline ($(wc -l < scripts/typecheck-baseline.txt) known)"
