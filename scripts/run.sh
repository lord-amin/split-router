#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ ! -x "$ROOT/build/splitroute-helper" || ! -x "$ROOT/build/splitroute-probe" ]]; then
  "$ROOT/scripts/build.sh"
fi

export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m splitroute_app "$@"
