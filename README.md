# scrawl-hermes

Send [Scrawl](https://github.com/kitlangton/scrawl) captures from your Mac to a
[Hermes Agent](https://github.com/NousResearch/hermes-agent) server automatically.

You press ⌥⇧S, talk and draw over the problem, press ⌥⇧S again. A few seconds later the capture
is on your Hermes server, and Hermes looks at the contact sheet and keyframes, reads what you
said, and replies in your chat (Discord, Telegram, Slack…). The capture folder stays on the
server, so a follow-up can open any frame.

Nothing in Scrawl or Hermes is patched. This is a small uploader on the Mac, a receiver on the
server, and a Hermes webhook route.

```
Scrawl.app ──writes──▶ ~/Library/Application Support/scrawl/captures/scrawl-….scrawl
                              │  launchd notices the folder change
                              ▼
               scrawl-hermes-push  ── tar over SSH (restricted key) ──▶  scrawl-hermes-receive
                                                                            │ unpack, check, move into the inbox
                                                                            ▼
                                                     signed POST to Hermes on 127.0.0.1:8644/webhooks/scrawl
                                                                            │
                                                                            ▼
                                                     Hermes agent run → reply in your chat
```

## What gets sent

`summary.md`, `scrawl.json`, `contact-sheet.png`, `frames/`, `frames-full/` (when Scrawl cropped)
and `routing.json`. The screen recording and audio stay on the Mac unless you install with
`--video`. Captures that already exist when you install are not sent.

## Security

- **The upload key can do one thing.** On the server it is a forced-command key with
  `restrict`: no shell, no port forwarding, no agent forwarding. It can only run the receiver.
- **The receiver is strict.** It accepts only Scrawl bundle names, unpacks into a staging folder
  with Python's `data` filter (no `..`, absolute paths, links or devices), caps size (1 GiB) and
  file count, requires the three bundle files, and moves the folder into the inbox in one rename.
- **The webhook stays local.** Hermes listens on `127.0.0.1` only; the receiver signs each
  notification with HMAC-SHA256 over timestamp and body (Hermes's generic V2 scheme), so a replay
  outside the five-minute window is refused.
- **The first SSH connection is pinned.** The installer writes the server's host key you pass,
  and every connection uses `StrictHostKeyChecking=yes`.
- **The reading run has few tools.** Hermes runs webhook events with its webhook toolset (vision,
  web, clarify): it can look at the frames and reply, not run commands. Text on your screen is
  untrusted input, so that's the right default. Open a capture from your normal chat to do more
  with it; the folder path is at the end of each reply.
- **The captures are your screen.** Hermes will see whatever was on it. Use a server you control.

A private network such as [Tailscale](https://tailscale.com) between the Mac and the server is a
good fit; plain SSH over the internet works too.

## Requirements

- Mac: Scrawl, and the `ssh`/`tar` that ship with macOS. Nothing to install with Homebrew.
- Server: Hermes Agent with its gateway running, Python 3.10+, and SSH access for your user.

## Install

### 1. Server

```sh
git clone https://github.com/dlisk100/scrawl-hermes
cd scrawl-hermes

# Turn on Hermes's webhook receiver, local only, then restart the gateway from a normal shell.
hermes config set platforms.webhook.enabled true
hermes config set platforms.webhook.extra.host 127.0.0.1
hermes gateway restart

# Create the secret and the "scrawl" route. --deliver/--chat-id choose where replies go.
server/setup.sh --deliver discord --chat-id <channel id>
```

`setup.sh` prints the server's SSH host key; you'll pass it to the Mac installer.
It also loads Scrawl's own agent skill on each run if you have it installed as `scrawl`
(`bunx skills add kitlangton/scrawl`, or copy `skills/scrawl/SKILL.md` from the Scrawl repo
into your Hermes skills).

### 2. Mac

```sh
git clone https://github.com/dlisk100/scrawl-hermes
cd scrawl-hermes
mac/install.sh --remote you@your-server \
  --host-key "ssh-ed25519 AAAA…" \
  --server-setup ~/scrawl-hermes/server/setup.sh
```

The installer creates `~/.ssh/scrawl_hermes`, a launchd agent that runs when the captures folder
changes (and once a minute to retry), and marks your existing captures as already sent. If your
normal SSH login to the server works, `--server-setup` uses it once to authorize the new key.
Otherwise it prints the one command to run on the server.

Check the link any time:

```sh
~/.local/share/scrawl-hermes/scrawl-hermes-push --check
```

Log: `~/Library/Application Support/scrawl-hermes/push.log`.
Uninstall: `mac/install.sh --uninstall`, and remove the `scrawl-hermes` line from the server's
`~/.ssh/authorized_keys`.

## Reliability

A capture counts as sent only when the server says Hermes accepted it. If the server is down,
asleep or unreachable, the Mac tries again every minute. Sending the same capture twice does
nothing: the receiver keeps the first copy and Hermes drops duplicate notification IDs. A capture
Scrawl is still writing is skipped until its files have settled. The server keeps captures for 14
days (`SCRAWL_HERMES_KEEP_DAYS`).

## Test

```sh
python3 tests/test_end_to_end.py
```

Runs the real Mac script and receiver against Hermes's own `WebhookAdapter` on a spare port (or a
stand-in with the same signature check when Hermes isn't importable). The `ssh` hop is replaced by
a shim that sets `SSH_ORIGINAL_COMMAND` the way `sshd` does. It covers delivery, no video by
default, no duplicates, half-written captures, retry after an outage, path traversal, links,
bad names, other commands and a wrong secret.

## Scrawl upstream

Scrawl's built-in send goes to OpenCode sessions. A general "run a command when a capture is done"
setting in Scrawl would make this launchd watcher unnecessary and let any agent receive captures.
This project works today without it.

## License

MIT
