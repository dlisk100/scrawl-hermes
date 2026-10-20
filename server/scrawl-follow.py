#!/usr/bin/env python3
"""Webhook route script: send each Scrawl read to the chat you used most recently.

Hermes runs this with the webhook payload on stdin. It looks up, read-only, the last chat
(channel or thread) where you sent Hermes a message on the delivery platform, and adds it to
the payload as `target_chat_id`, which the route's deliver_extra uses. If it finds nothing,
it leaves `target_chat_id` empty and Hermes falls back to that platform's home channel.

Config (environment, optional): SCRAWL_HERMES_PLATFORM (default discord),
SCRAWL_HERMES_FOLLOW_HOURS (default 12: older activity falls back to home).
"""

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

payload = json.load(sys.stdin)
platform = os.environ.get("SCRAWL_HERMES_PLATFORM", "discord")
hours = float(os.environ.get("SCRAWL_HERMES_FOLLOW_HOURS", "12"))
home = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
target = ""
try:
    db = sqlite3.connect(f"file:{home / 'state.db'}?mode=ro", uri=True, timeout=5)
    row = db.execute(
        """SELECT s.chat_id, m.timestamp FROM messages m JOIN sessions s ON s.id = m.session_id
           WHERE s.source = ? AND m.role = 'user' AND s.chat_id IS NOT NULL
           ORDER BY m.timestamp DESC LIMIT 1""", (platform,)).fetchone()
    if row and time.time() - float(row[1]) < hours * 3600:
        target = str(row[0])
except Exception as err:  # never block the capture on this lookup
    print(f"scrawl-follow: lookup failed, using home channel: {err}", file=sys.stderr)
payload["target_chat_id"] = target
print(json.dumps(payload))
