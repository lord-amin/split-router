#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

mkdir -p build
echo "Building splitroute-helper..."
go build -o build/splitroute-helper ./cmd/splitroute-helper
echo "Building splitroute-probe..."
go build -o build/splitroute-probe ./cmd/splitroute-probe

echo "Checking Python GTK4 + Libadwaita runtime..."
python3 -c "import gi; gi.require_version('Gtk', '4.0'); gi.require_version('Adw', '1'); import cairo; print('Runtime OK')"

echo "Build complete:"
echo "  $ROOT/build/splitroute-helper"
echo "  $ROOT/build/splitroute-probe"
