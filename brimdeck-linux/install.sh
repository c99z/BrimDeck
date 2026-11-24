#!/usr/bin/env bash
# Installs BrimDeck for Linux for the current user (no root needed).
#   ./install.sh               launcher in ~/.local/bin, entry in the app menu
#   ./install.sh --autostart   also start it at login
#   ./install.sh --uninstall   remove launcher, menu entry and autostart (settings stay in ~/.config/brimdeck)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="$HOME/.local/bin/brimdeck"
APPS="$HOME/.local/share/applications/brimdeck.desktop"
ICON="$HOME/.local/share/icons/hicolor/128x128/apps/brimdeck.png"
AUTO="$HOME/.config/autostart/brimdeck.desktop"
UUID=brimdeck@brimdeck.local
EXT="${XDG_DATA_HOME:-$HOME/.local/share}/gnome-shell/extensions/$UUID"

if [[ "${1:-}" == "--uninstall" ]]; then
  "$BIN" --quit 2>/dev/null || true
  if [[ -d "$EXT" ]]; then
    gnome-extensions disable "$UUID" 2>/dev/null || true
    rm -f "$EXT/metadata.json" "$EXT/extension.js" "$EXT/stylesheet.css"
    rmdir "$EXT" 2>/dev/null || true
  fi
  rm -f "$BIN" "$APPS" "$ICON" "$AUTO"
  echo "BrimDeck removed. Settings and history remain in ~/.config/brimdeck and ~/.local/share/brimdeck."
  exit 0
fi

missing=()
python3 -c 'import gi; gi.require_version("Gtk","3.0"); from gi.repository import Gtk' 2>/dev/null || missing+=(python3-gi gir1.2-gtk-3.0)
python3 -c 'import cairo' 2>/dev/null || missing+=(python3-gi-cairo)
python3 -c 'import psutil' 2>/dev/null || missing+=(python3-psutil)
python3 -c 'import gi; gi.require_version("Wnck","3.0")' 2>/dev/null || missing+=(gir1.2-wnck-3.0)
if (( ${#missing[@]} )); then
  echo "Missing packages: sudo apt install ${missing[*]}"
  exit 1
fi

mkdir -p "$(dirname "$BIN")" "$(dirname "$APPS")" "$(dirname "$ICON")"
cat > "$BIN" <<SH
#!/usr/bin/env bash
exec python3 "$HERE/brimdeck.py" "\$@"
SH
chmod +x "$BIN"
cp "$HERE/data/brimdeck.png" "$ICON"
DESKTOP="[Desktop Entry]
Type=Application
Name=BrimDeck
Comment=Dynamic Island: AI quotas, music, system monitor, clipboard
Exec=$BIN
Icon=brimdeck
Categories=Utility;
StartupNotify=false
Actions=Toggle;Settings;Quit;

[Desktop Action Toggle]
Name=Toggle panel
Exec=$BIN --toggle

[Desktop Action Settings]
Name=Settings
Exec=$BIN --settings

[Desktop Action Quit]
Name=Quit
Exec=$BIN --quit
"
printf '%s' "$DESKTOP" > "$APPS"
gtk-update-icon-cache -q "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
update-desktop-database -q "$HOME/.local/share/applications" 2>/dev/null || true

if [[ "${1:-}" == "--autostart" ]]; then
  mkdir -p "$(dirname "$AUTO")"
  printf '%s' "$DESKTOP" | sed 's/^StartupNotify=false/StartupNotify=false\nX-GNOME-Autostart-enabled=true\nX-GNOME-Autostart-Delay=3/' > "$AUTO"
  echo "Autostart enabled."
fi
if [[ "$(gnome-shell --version 2>/dev/null || true)" == "GNOME Shell 46."* ]]; then
  install -d "$EXT"
  install -m 644 "$HERE"/gnome-extension/{metadata.json,extension.js,stylesheet.css} "$EXT/"
  if gnome-extensions info "$UUID" >/dev/null 2>&1; then
    gnome-extensions enable "$UUID"
  else
    # The Shell discovers newly installed extensions at session startup.
    python3 - "$UUID" <<'PY'
import sys
from gi.repository import Gio
settings = Gio.Settings.new("org.gnome.shell")
uuid = sys.argv[1]
enabled = settings.get_strv("enabled-extensions")
if uuid not in enabled:
    settings.set_strv("enabled-extensions", [*enabled, uuid])
disabled = settings.get_strv("disabled-extensions")
if uuid in disabled:
    settings.set_strv("disabled-extensions", [item for item in disabled if item != uuid])
Gio.Settings.sync()
PY
    echo "The BrimDeck strip will appear beside the clock after GNOME Shell reloads."
  fi
fi
echo "Installed. Start it with: brimdeck   (toggle: brimdeck --toggle, settings: brimdeck --settings)"
