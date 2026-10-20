#!/bin/bash
# Install the Scrawl → Hermes uploader on a Mac.
#
#   ./install.sh --remote hermes@100.x.y.z --host-key "ssh-ed25519 AAAA..."
#
# --remote    SSH user@host of the Hermes server (a Tailscale address is a good choice)
# --host-key  the server's SSH host key, so the first connection can't be intercepted
#             (on the server: cat /etc/ssh/ssh_host_ed25519_key.pub)
# --port      SSH port (default 22)
# --video     also send recording.mp4/.mov and audio.wav (off by default; they're large)
# --server-setup PATH   path of server/setup.sh on the server; if your normal SSH login
#             works, the installer uses it once to authorize the upload key
#
# Captures already on this Mac are marked as sent, so only new ones go out.
# Uninstall: ./install.sh --uninstall
set -euo pipefail

LABEL="com.scrawl-hermes.push"
CONF_DIR="$HOME/.config/scrawl-hermes"
BIN_DIR="$HOME/.local/share/scrawl-hermes"
STATE="$HOME/Library/Application Support/scrawl-hermes"
CAPTURES="$HOME/Library/Application Support/scrawl/captures"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
KEY="$HOME/.ssh/scrawl_hermes"
HERE="$(cd "$(dirname "$0")" && pwd)"

remote="" host_key="" port=22 video=0 uninstall=0 server_setup=""
while [ $# -gt 0 ]; do
  case "$1" in
    --remote) remote="$2"; shift 2 ;;
    --host-key) host_key="$2"; shift 2 ;;
    --port) port="$2"; shift 2 ;;
    --video) video=1; shift ;;
    --server-setup) server_setup="$2"; shift 2 ;;
    --uninstall) uninstall=1; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
if [ "$uninstall" = 1 ]; then
  rm -f "$PLIST"
  rm -rf "$BIN_DIR"
  echo "Uninstalled. Kept $CONF_DIR, $STATE and $KEY in case you reinstall."
  exit 0
fi

if [ -z "$remote" ] || [ -z "$host_key" ]; then
  echo "usage: $0 --remote user@host --host-key \"ssh-ed25519 AAAA...\"" >&2
  exit 2
fi
host="${remote#*@}"

mkdir -p "$CONF_DIR" "$BIN_DIR" "$STATE/sent" "$HOME/Library/LaunchAgents" "$HOME/.ssh"
chmod 700 "$CONF_DIR" "$HOME/.ssh"
install -m 755 "$HERE/scrawl-hermes-push" "$BIN_DIR/scrawl-hermes-push"

if [ "$port" = 22 ]; then
  echo "$host $host_key" > "$CONF_DIR/known_hosts"
else
  echo "[$host]:$port $host_key" > "$CONF_DIR/known_hosts"
fi

cat > "$CONF_DIR/config" <<EOF
SCRAWL_HERMES_REMOTE="$remote"
SCRAWL_HERMES_SSH_PORT="$port"
SCRAWL_HERMES_INCLUDE_VIDEO="$video"
EOF

[ -f "$KEY" ] || ssh-keygen -q -t ed25519 -N "" -C "scrawl-hermes $(scutil --get LocalHostName 2>/dev/null || hostname)" -f "$KEY"

# Don't flood Hermes with old captures.
if [ -d "$CAPTURES" ]; then
  for b in "$CAPTURES"/scrawl-*.scrawl; do
    [ -d "$b" ] && : > "$STATE/sent/$(basename "$b" .scrawl)"
  done
fi
mkdir -p "$CAPTURES"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>$BIN_DIR/scrawl-hermes-push</string></array>
  <key>WatchPaths</key><array><string>$CAPTURES</string></array>
  <key>StartInterval</key><integer>60</integer>
  <key>RunAtLoad</key><true/>
  <key>ThrottleInterval</key><integer>2</integer>
  <key>StandardOutPath</key><string>$STATE/launchd.log</string>
  <key>StandardErrorPath</key><string>$STATE/launchd.log</string>
</dict>
</plist>
EOF
launchctl bootstrap "gui/$(id -u)" "$PLIST"

pub="$(cat "$KEY.pub")"
echo
if [ -n "$server_setup" ] && ssh -p "$port" -o BatchMode=yes -o ConnectTimeout=10 \
     -o StrictHostKeyChecking=yes -o UserKnownHostsFile="$CONF_DIR/known_hosts" \
     "$remote" "bash '$server_setup' --allow-key '$pub'"; then
  echo "Authorized the upload key on the server using your usual SSH login."
else
  echo "One step left: allow this key on the server, limited to receiving captures."
  echo "On the server run (the key is public, not a secret):"
  echo
  echo "  server/setup.sh --allow-key '$pub'"
fi
echo
if "$BIN_DIR/scrawl-hermes-push" --check 2>&1 | grep -q '"pong": true'; then
  echo "Link checked: the server answers. New captures will be sent automatically."
else
  echo "The server didn't answer yet. After authorizing the key, check with:"
  echo "  $BIN_DIR/scrawl-hermes-push --check"
fi
echo "Log: $STATE/push.log"
