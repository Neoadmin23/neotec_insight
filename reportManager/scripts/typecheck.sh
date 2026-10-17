#!/usr/bin/env bash
# v2.90.0 — the type check passes only on a clean compile.
#
# The previous version compared filtered `src/... error TS` lines against a
# baseline and swallowed tsc's exit status. A compiler failure that printed no
# such line (a bad tsconfig, TS5083, a crash) produced an empty diff and
# "typecheck OK". There were also 11 real errors hidden in the baseline; they
# are fixed, and the baseline is gone.
#
# Now: tsc must exist, must exit 0, and must print no diagnostic at all.
set -uo pipefail
cd "$(dirname "$0")/.."

TSC=node_modules/.bin/tsc
[ -x "$TSC" ] || { echo "FAIL: tsc missing — run npm ci." >&2; exit 1; }

out="$("$TSC" --noEmit -p tsconfig.json 2>&1)"
status=$?
if [ "$status" -ne 0 ] || printf '%s' "$out" | grep -q "error TS"; then
  printf '%s\n' "$out" >&2
  echo "FAIL: TypeScript reported errors (tsc exit $status)." >&2
  exit 1
fi
echo "typecheck OK — clean compile (tsc exit 0, no diagnostics)"
