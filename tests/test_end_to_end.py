#!/usr/bin/env python3
"""End-to-end test of the uploader and receiver without a Mac or a live gateway.

- Builds fake Scrawl bundles (real PNGs, real file layout) in a temp captures folder.
- Runs the real Mac push script, with `ssh` replaced by a shim that runs the receiver
  locally with SSH_ORIGINAL_COMMAND set, exactly as sshd's forced command would.
- The webhook is Hermes's real WebhookAdapter on a free port with the route from
  server/prompt.txt (agent runs are captured, not started). If Hermes isn't importable,
  a stand-in with the same HMAC V2 check is used.

Run: python3 tests/test_end_to_end.py
"""

import asyncio
import hashlib
import hmac
import http.server
import io
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUSH = ROOT / "mac" / "scrawl-hermes-push"
RECEIVE = ROOT / "server" / "scrawl-hermes-receive"
SECRET = "test-secret-" + "x" * 20


def png(path):
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * 4 for _ in range(4))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def make_bundle(captures, name, age=60):
    b = captures / f"{name}.scrawl"
    (b / "frames").mkdir(parents=True)
    png(b / "contact-sheet.png")
    png(b / "frames" / "frame-01.png")
    png(b / "frames" / "frame-02.png")
    (b / "scrawl.json").write_text(json.dumps({"keyframes": 2}))
    (b / "summary.md").write_text(f"[scrawl 0:12 · Chrome “Atlas” · {b}]\n- #1 (0:03) “This label overlaps the river.”\n")
    (b / "recording.mp4").write_bytes(b"\x00" * 4096)
    old = time.time() - age
    for p in [b, *b.rglob("*")]:
        os.utime(p, (old, old))
    return b


class StandIn(http.server.BaseHTTPRequestHandler):
    prompts = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        stamp = self.headers.get("X-Webhook-Timestamp", "")
        good = hmac.compare_digest(self.headers.get("X-Webhook-Signature-V2", ""), hmac.new(
            SECRET.encode(), stamp.encode() + b"." + body, hashlib.sha256).hexdigest())
        if not good:
            self.send_response(401); self.end_headers(); return
        data = json.loads(body)
        StandIn.prompts.append(f"{data['path']} {data['contact_sheet']} {data['summary']}")
        self.send_response(202); self.end_headers(); self.wfile.write(b'{"status":"accepted"}')

    def log_message(self, *args):
        pass


def start_webhook(tmp):
    hermes_home = tmp / "hermes-home"
    os.environ["HERMES_HOME"] = str(hermes_home)
    (hermes_home / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "server" / "scrawl-follow.py", hermes_home / "scripts" / "scrawl-follow.py")
    import sqlite3
    db = sqlite3.connect(hermes_home / "state.db")
    db.executescript("""CREATE TABLE sessions (id TEXT, source TEXT, chat_id TEXT);
        CREATE TABLE messages (session_id TEXT, role TEXT, timestamp REAL);""")
    db.executemany("INSERT INTO sessions VALUES (?,?,?)",
                   [("a", "discord", "111"), ("b", "discord", "222"), ("c", "telegram", "333")])
    now = time.time()
    db.executemany("INSERT INTO messages VALUES (?,?,?)",
                   [("a", "user", now - 600), ("b", "user", now - 60), ("b", "assistant", now),
                    ("c", "user", now - 5)])
    db.commit(); db.close()
    sys.path.insert(0, str(Path.home() / ".hermes/hermes-agent"))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    try:
        from gateway.config import PlatformConfig
        from gateway.platforms.webhook import WebhookAdapter
    except Exception as err:
        print("note: Hermes not importable, using a stand-in webhook:", err)
        server = http.server.HTTPServer(("127.0.0.1", port), StandIn)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return port, StandIn.prompts, "stand-in (HMAC V2)", None
    adapter = WebhookAdapter(PlatformConfig(enabled=True, extra={
        "host": "127.0.0.1", "port": port, "secret": SECRET,
        "routes": {"scrawl": {"events": ["scrawl.capture"], "script": "scrawl-follow.py",
                              "deliver": "discord", "deliver_extra": {"chat_id": "{target_chat_id}"},
                              "prompt": (ROOT / "server" / "prompt.txt").read_text()}}}))
    prompts = []

    async def capture(event):
        prompts.append(event.text)
    adapter.handle_message = capture
    loop = asyncio.new_event_loop()
    ready = threading.Event()

    def serve():
        asyncio.set_event_loop(loop)
        if loop.run_until_complete(adapter.connect()):
            ready.set()
        loop.run_forever()
    threading.Thread(target=serve, daemon=True).start()
    if not ready.wait(20):
        raise SystemExit("Hermes webhook adapter did not start")
    return port, prompts, "Hermes WebhookAdapter", adapter


def main():
    tmp = Path(tempfile.mkdtemp(prefix="scrawl-hermes-test-"))
    port, prompts, kind, adapter = start_webhook(tmp)
    captures, state, inbox, shim = tmp / "captures", tmp / "state", tmp / "inbox", tmp / "bin"
    captures.mkdir(); shim.mkdir()
    (tmp / "secret").write_text(SECRET)
    # ssh shim: drop ssh options and host, run the receiver as sshd's forced command would.
    (shim / "ssh").write_text(f"""#!/bin/bash
while [ $# -gt 0 ]; do case "$1" in -i|-p|-o) shift 2;; *) break;; esac; done
shift  # user@host
export SSH_ORIGINAL_COMMAND="$*"
exec python3 {RECEIVE}
""")
    (shim / "ssh").chmod(0o755)
    hook_url = f"http://127.0.0.1:{port}/webhooks/scrawl"
    env = dict(os.environ, PATH=f"{shim}:{os.environ['PATH']}", SCRAWL_CAPTURES_DIR=str(captures),
               SCRAWL_HERMES_STATE=str(state), SCRAWL_HERMES_REMOTE="hermes@test",
               SCRAWL_HERMES_CONFIG=str(tmp / "none"), SCRAWL_HERMES_INBOX=str(inbox),
               SCRAWL_HERMES_SECRET_FILE=str(tmp / "secret"), SCRAWL_HERMES_WEBHOOK_URL=hook_url,
               SCRAWL_HERMES_WAIT_SECONDS="4")

    def run(*args):
        r = subprocess.run(["bash", str(PUSH), *args], env=env, capture_output=True, text=True, timeout=60)
        time.sleep(0.5)  # let the adapter hand the event over
        return r

    results = []

    def check(name, cond):
        results.append((name, bool(cond)))
        print(("PASS " if cond else "FAIL ") + name)

    r = run("--check")
    check("ping answers through the forced command", '"pong": true' in r.stdout)

    make_bundle(captures, "scrawl-1792432139176-1a2b")
    run()
    got = inbox / "scrawl-1792432139176-1a2b"
    check("capture lands in the inbox", (got / "contact-sheet.png").is_file() and (got / "frames/frame-02.png").is_file())
    check("video is not sent by default", not (got / "recording.mp4").exists())
    check("Hermes accepted it once (signature, route, event filter)", len(prompts) == 1)
    text = prompts[-1] if prompts else ""
    check("Hermes prompt has the server path, contact sheet and transcript",
          str(got) in text and str(got / "contact-sheet.png") in text and "overlaps the river" in text)
    check("Mac marks it sent", (state / "sent/scrawl-1792432139176-1a2b").exists())
    if adapter is not None:
        targets = [d.get("deliver_extra", {}).get("chat_id") for d in adapter._delivery_info.values()]
        check("reply goes to the Discord chat used last (not Telegram, not an older one)", targets == ["222"])

    run()
    check("no duplicate delivery on rerun", len(prompts) == 1)

    partial = captures / "scrawl-1792432140000-1a2b.scrawl"
    (partial / "frames").mkdir(parents=True)
    run()
    check("half-written capture is not sent", not (inbox / "scrawl-1792432140000-1a2b").exists())
    shutil.rmtree(partial)
    make_bundle(captures, "scrawl-1792432140000-1a2b")
    run()
    check("it is sent once finished", (inbox / "scrawl-1792432140000-1a2b/.notified").exists())

    env["SCRAWL_HERMES_WEBHOOK_URL"] = "http://127.0.0.1:9/webhooks/scrawl"  # nothing listens
    make_bundle(captures, "scrawl-1792432150000-1a2b")
    run()
    check("not marked sent while Hermes is down", not (state / "sent/scrawl-1792432150000-1a2b").exists())
    env["SCRAWL_HERMES_WEBHOOK_URL"] = hook_url
    before = len(prompts)
    run()
    check("retry notifies once Hermes is back", len(prompts) == before + 1
          and (state / "sent/scrawl-1792432150000-1a2b").exists())

    def receive(command, data):
        e = dict(env, SSH_ORIGINAL_COMMAND=command)
        return subprocess.run(["python3", str(RECEIVE)], input=data, env=e, capture_output=True, timeout=30)
    evil = io.BytesIO()
    with tarfile.open(fileobj=evil, mode="w") as t:
        info = tarfile.TarInfo("../../escape.txt"); info.size = 3; t.addfile(info, io.BytesIO(b"bad"))
    r = receive("receive scrawl-1792432160000-1a2b", evil.getvalue())
    check("path traversal is refused", b'"ok": false' in r.stdout and not (tmp / "escape.txt").exists())
    r = receive("receive ../../etc", b"")
    check("bad bundle name is refused", b"not a Scrawl bundle name" in r.stdout)
    r = receive("cat /etc/passwd", b"")
    check("other commands are refused", b"usage" in r.stdout)
    link = io.BytesIO()
    with tarfile.open(fileobj=link, mode="w") as t:
        info = tarfile.TarInfo("summary.md"); info.type = tarfile.SYMTYPE; info.linkname = "/etc/passwd"
        t.addfile(info)
    r = receive("receive scrawl-1792432170000-1a2b", link.getvalue())
    check("symlinks are not unpacked", b'"ok": false' in r.stdout
          and not (inbox / "scrawl-1792432170000-1a2b").exists())
    check("no staging leftovers", not (inbox / ".staging").exists())

    (tmp / "secret").write_text("wrong")
    make_bundle(captures, "scrawl-1792432180000-1a2b")
    before = len(prompts)
    run()
    check("wrong secret is rejected and not marked sent", len(prompts) == before
          and not (state / "sent/scrawl-1792432180000-1a2b").exists())

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed" + (f"; failed: {failed}" if failed else ""))
    print("webhook under test:", kind)
    shutil.rmtree(tmp, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
