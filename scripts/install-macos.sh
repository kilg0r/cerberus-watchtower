#!/usr/bin/env bash
# Install Watchtower shell integration on macOS - the Install-Watchtower.ps1
# equivalent: a LaunchAgent that starts the server headless at login (same
# pattern as the content manager) plus a `watchtower` function in ~/.zshrc.
#
#   scripts/install-macos.sh              # install both
#   scripts/install-macos.sh --no-agent   # shell function only
#   scripts/install-macos.sh --uninstall  # remove both

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="tech.cerberuslabs.watchtower"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$HOME/Library/Logs/CerberusWatchtower"
ZSHRC="$HOME/.zshrc"
MARKER="# --- cerberus watchtower ---"

no_agent=0
uninstall=0
for arg in "$@"; do
  case "$arg" in
    --no-agent)  no_agent=1 ;;
    --uninstall) uninstall=1 ;;
    -h|--help)   sed -n '2,9p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

unload_agent() { launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true; }

strip_zsh_block() {
  [ -f "$ZSHRC" ] || return 0
  # delete the marker block, inclusive
  /usr/bin/sed -i '' "/^${MARKER}$/,/^# --- end watchtower ---$/d" "$ZSHRC"
}

if [ "$uninstall" -eq 1 ]; then
  unload_agent
  rm -f "$PLIST" && echo "removed LaunchAgent $LABEL"
  strip_zsh_block && echo "removed the watchtower function from ~/.zshrc"
  echo "Done. The repo itself is untouched; run scripts/watchtower.sh to start it by hand."
  exit 0
fi

if [ ! -x "$ROOT/.venv/bin/python" ]; then
  echo "venv not found at $ROOT/.venv - install it first (see README)." >&2
  exit 1
fi

# --- LaunchAgent: server starts headless at login ---
if [ "$no_agent" -eq 0 ]; then
  mkdir -p "$LOG_DIR" "$(dirname "$PLIST")"
  cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>

    <key>ProgramArguments</key>
    <array>
        <string>$ROOT/.venv/bin/python</string>
        <string>-m</string>
        <string>watchtower</string>
    </array>

    <key>WorkingDirectory</key>
    <string>$ROOT</string>

    <!-- launchd hands over a bare PATH; the scanners shell out to git and lsof,
         and the AI features need the claude CLI in ~/.local/bin. -->
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>$HOME/.local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <false/>

    <key>ProcessType</key>
    <string>Interactive</string>

    <key>StandardOutPath</key>
    <string>$LOG_DIR/stdout.log</string>

    <key>StandardErrorPath</key>
    <string>$LOG_DIR/stderr.log</string>
</dict>
</plist>
PLIST_EOF

  plutil -lint "$PLIST" >/dev/null
  unload_agent                       # idempotent: replace any previous load
  launchctl bootstrap "gui/$UID" "$PLIST"
  echo "LaunchAgent: $LABEL (server starts at login, logs in $LOG_DIR)"
fi

# --- shell function ---
strip_zsh_block
cat >> "$ZSHRC" <<ZSH_EOF
$MARKER
watchtower() { "$ROOT/scripts/watchtower.sh" "\$@"; }
# --- end watchtower ---
ZSH_EOF
echo "shell function: watchtower  (open a new shell, or: source ~/.zshrc)"

echo
echo "  watchtower              start if needed + open the dashboard"
echo "  watchtower --stop       shut it down"
echo "  watchtower --status     report without changing anything"
echo "Uninstall with: scripts/install-macos.sh --uninstall"
