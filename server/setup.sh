#!/bin/bash
# Set up the Hermes side: inbox, webhook secret and route, and the restricted SSH key.
#
#   server/setup.sh                       # create secret + webhook route; print next steps
#   server/setup.sh --allow-key "ssh-ed25519 AAAA... scrawl-hermes mac"   # authorize a Mac
#
# Options: --deliver discord --chat-id <channel id>   where Hermes posts its read of each capture
#          --inbox <dir>                               where captures are unpacked (default ~/scrawl-inbox)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
RECEIVER="$HERE/scrawl-hermes-receive"
CONF_DIR="$HOME/.config/scrawl-hermes"
SECRET_FILE="$CONF_DIR/webhook.secret"
inbox="$HOME/scrawl-inbox" deliver="" chat_id="" allow_key=""

while [ $# -gt 0 ]; do
  case "$1" in
    --inbox) inbox="$2"; shift 2 ;;
    --deliver) deliver="$2"; shift 2 ;;
    --chat-id) chat_id="$2"; shift 2 ;;
    --allow-key) allow_key="$2"; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

mkdir -p "$CONF_DIR" "$inbox"
chmod 700 "$CONF_DIR" "$inbox"
chmod 755 "$RECEIVER"

if [ -n "$allow_key" ]; then
  case "$allow_key" in ssh-ed25519\ *|ecdsa-*\ *|ssh-rsa\ *) ;; *) echo "that doesn't look like a public key" >&2; exit 2 ;; esac
  line="command=\"SCRAWL_HERMES_INBOX='$inbox' SCRAWL_HERMES_SECRET_FILE='$SECRET_FILE' /usr/bin/python3 '$RECEIVER'\",restrict $allow_key"
  mkdir -p "$HOME/.ssh"; chmod 700 "$HOME/.ssh"
  touch "$HOME/.ssh/authorized_keys"; chmod 600 "$HOME/.ssh/authorized_keys"
  key_body=$(printf '%s' "$allow_key" | awk '{print $2}')
  if grep -qF "$key_body" "$HOME/.ssh/authorized_keys"; then
    echo "That key is already in authorized_keys; not adding it twice."
  else
    printf '%s\n' "$line" >> "$HOME/.ssh/authorized_keys"
    echo "Key allowed. It can only run the capture receiver: no shell, no forwarding."
  fi
  exit 0
fi

if [ ! -s "$SECRET_FILE" ]; then
  umask 077
  python3 -c 'import secrets; print(secrets.token_hex(32))' > "$SECRET_FILE"
fi

PROMPT_FILE="$HERE/prompt.txt"
args=(scrawl --events scrawl.capture --skills scrawl --secret "$(cat "$SECRET_FILE")"
      --description "Scrawl captures sent from a Mac" --prompt "$(cat "$PROMPT_FILE")")
[ -n "$deliver" ] && args+=(--deliver "$deliver")
[ -n "$chat_id" ] && args+=(--deliver-chat-id "$chat_id")
hermes webhook subscribe "${args[@]}" >/dev/null
echo "Webhook route 'scrawl' is set (secret in $SECRET_FILE)."
echo "Host key for the Mac installer:"
cat /etc/ssh/ssh_host_ed25519_key.pub
