#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
"$ROOT/scripts/build.sh"

APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICON_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
mkdir -p "$APP_DIR" "$ICON_DIR"

rm -f "$APP_DIR/blueiprouter.desktop" "$ICON_DIR/blueiprouter.svg"
cp "$ROOT/assets/splitroute.svg" "$ICON_DIR/splitroute.svg"

cat > "$APP_DIR/splitroute.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=SplitRoute
Comment=Linux per-destination routing, split tunneling and nftables kill switch
Exec=$ROOT/scripts/run.sh
Icon=splitroute
Terminal=false
Categories=Network;System;Settings;
StartupNotify=true
EOF

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$APP_DIR" || true
fi

echo "Installed desktop launcher: $APP_DIR/splitroute.desktop"
echo "Launch SplitRoute from your Applications menu or run: $ROOT/scripts/run.sh"
echo "To install the root helper & systemd service, open SplitRoute and complete the Setup Assistant (or pass --system)."

if [[ "${1:-}" == "--system" ]]; then
  pkexec sh -c "
    (systemctl disable --now blueiprouter-helper.service || true) &&
    install -d -o root -g root -m 755 /usr/local/libexec /etc/splitroute /run/splitroute &&
    install -o root -g root -m 755 '$ROOT/build/splitroute-helper' /usr/local/libexec/splitroute-helper &&
    install -o root -g root -m 755 '$ROOT/build/splitroute-probe' /usr/local/libexec/splitroute-probe &&
    (setcap cap_net_raw=ep /usr/local/libexec/splitroute-probe || true)
  "
  echo "System helper & probe installed."
fi
