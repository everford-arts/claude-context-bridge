# ClaudeCode Connect

A small, persistent key-value context store that bridges a remote **Claude.ai** session and a local **Claude Code** instance, exposed over **Tailscale Funnel**.

Built by [Everford](https://github.com/everford-arts) for homelab/self-hosted use.

## What it is

ClaudeCode Connect is a lightweight MCP (Model Context Protocol) server backed by SQLite. It exposes a handful of tools — `get_context`, `set_context`, `log_session`, `get_history` — that both Claude.ai (in the browser, via a custom connector) and Claude Code (on your machine, via a local MCP registration) can call. Either side can read what the other last wrote.

It is deliberately simple: one table of key/value context notes, one table of session log entries. No message queue, no websockets, no live streaming — just durable notes one Claude instance leaves for the other.

## The problem it solves

Claude.ai and Claude Code don't share memory. If you're planning something in a Claude.ai chat on your phone and want your local Claude Code instance to pick up the work later, the default option is copy-pasting context by hand between the two — error-prone, easy to forget, and it doesn't scale past a couple of handoffs.

ClaudeCode Connect gives both sides a shared, durable place to leave notes: "here's what I decided, here's what's blocked, here's what to do next." Claude Code checks it at the start of a session; Claude.ai can write to it any time, from anywhere, over Tailscale.

## Architecture

- **Storage**: a single SQLite database with two tables — `context` (key/value pairs with `updated_at`/`updated_by` metadata) and `sessions` (an append-only log of session summaries).
- **Transport**: [FastMCP](https://github.com/jlowin/fastmcp) over streamable HTTP, run as a systemd service bound only to `127.0.0.1`.
- **Exposure**: the local port is published to the internet via [Tailscale Funnel](https://tailscale.com/kb/1223/funnel), so Claude.ai (which runs in Anthropic's cloud, not on your tailnet) can reach it. The only thing standing between the public internet and your data is an unguessable secret path segment in the URL.
- **Scope, on purpose**: this is a store for status notes and task handoffs, not live telemetry. It doesn't proxy your filesystem, your shell, or any other service — it just holds short text values you write to it deliberately. Treat every value as something you'd be fine with existing in a durable, internet-reachable log, and never write secrets (API keys, passwords, tokens, private file contents) into it.

## Available tools

| Tool | Description |
|---|---|
| `get_context(key?)` | Read one context value by key, or list every stored key/value pair if no key is given. |
| `set_context(key, value, updated_by)` | Write or overwrite a context value, tagged with who wrote it. |
| `log_session(source, summary, details?)` | Append an entry to the session history — a short record of what a given Claude instance just did. |
| `get_history(limit?, source?)` | Read back recent session log entries, newest first, optionally filtered by source. |
| `conversation_search` | Not part of this server — this is Claude.ai's own built-in conversation search, unrelated to ClaudeCode Connect's MCP tools. Mentioned here only to avoid confusion if you see it referenced elsewhere. |

## The handoff pattern

The convention that makes this useful is just a naming discipline on top of `set_context` / `get_context`:

1. From Claude.ai, write a context entry whose **value** starts with the literal string `TASK for claude-code:` followed by the task description, and pass `updated_by="claude-ai"`.
2. At the start of a Claude Code session (or on a recurring check-in), read context and look for any value beginning with `TASK for claude-code:`. Treat it as a pending task handed off from the remote session.
3. Once picked up, Claude Code does the work, then calls `log_session` with a summary of what it did — so the next `get_history` call (from either side) shows the loop closed.
4. Optionally, Claude Code updates or clears the context key it consumed, tagged `updated_by="claude-code"`, so Claude.ai can see the task was picked up on its next check.

This is the entire coordination protocol — no special "task" table, just a string convention on top of the generic key/value store.

## Setup

### Prerequisites

- A machine to host the bridge (a homelab server, NAS, or any always-on box), with Python 3.10+.
- [Tailscale](https://tailscale.com/) installed and logged in on that machine, with **Funnel** enabled for your tailnet (Tailscale admin console → the node → Funnel, or `tailscale funnel` will prompt you if it isn't enabled yet).
- A Claude.ai account with access to custom MCP connectors (Settings → Connectors).
- Claude Code installed locally, if you want the local side of the handoff.

### Install

```bash
git clone https://github.com/everford-arts/claude-context-bridge.git
cd claude-context-bridge
python3 -m venv venv
source venv/bin/activate
pip install fastmcp
```

### Configure

Create a `.env` file next to `server.py`:

```bash
BRIDGE_SECRET=<YOUR_RANDOM_SECRET>   # e.g. `openssl rand -hex 24`
BRIDGE_HOST=127.0.0.1                # never bind 0.0.0.0 — Funnel handles public exposure
BRIDGE_PORT=8765
BRIDGE_DB=/path/to/bridge.db         # optional, defaults next to server.py
```

`BRIDGE_SECRET` becomes part of the MCP endpoint path (`/mcp-<secret>/`) — it's the only thing gating access once the port is Funneled to the public internet, so generate something long and random and don't commit it.

### Run as a service

A minimal systemd unit:

```ini
[Unit]
Description=ClaudeCode Connect bridge
After=network.target

[Service]
Type=simple
User=<YOUR_USER>
WorkingDirectory=/path/to/claude-context-bridge
ExecStart=/path/to/claude-context-bridge/venv/bin/python server.py
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now claudecode-connect.service
```

### Expose via Tailscale Funnel

```bash
tailscale funnel --bg 8765
```

This publishes `https://<your-tailnet-node>.ts.net/` → `127.0.0.1:8765` on the host. Your full bridge URL, including the secret path, will look like:

```
<YOUR_FUNNEL_URL>/mcp-<YOUR_SECRET>/
```

Redact both the Funnel hostname and the secret path in anything you share publicly (issue trackers, screenshots, etc.) — either one alone is enough to reach the bridge.

### Register the connector in Claude.ai

1. Claude.ai → **Settings → Connectors → Add custom connector**.
2. Paste your full bridge URL (Funnel hostname + `/mcp-<secret>/` path).
3. Save. Claude.ai should now list `get_context`, `set_context`, `log_session`, and `get_history` as available tools in that connector.

### Register with Claude Code

Add the server to Claude Code's MCP config (`~/.claude.json`, under `mcpServers`), pointing at the **local** address rather than the Funnel URL, since Claude Code runs on the same host:

```json
{
  "mcpServers": {
    "claudecode-connect": {
      "type": "http",
      "url": "http://127.0.0.1:8765/mcp-<YOUR_SECRET>/"
    }
  }
}
```

## Usage example

A full handoff, end to end:

1. **Claude.ai** (on your phone, mid-conversation about a project): calls
   ```
   set_context(
     key="pending_task",
     value="TASK for claude-code: refactor the backup script to use findmnt-based device resolution instead of hardcoded /dev/sdX paths.",
     updated_by="claude-ai"
   )
   ```
2. Later, you open a **Claude Code** session on your server. At the start of the session it calls `get_context(key="pending_task")` and sees the `TASK for claude-code:` entry.
3. Claude Code does the refactor, then calls
   ```
   log_session(
     source="claude-code",
     summary="Refactored backup script to use findmnt for device resolution",
     details="Replaced hardcoded /dev/sdb1 references with findmnt lookups by mount point; tested against a simulated device-letter swap."
   )
   ```
4. It clears the handoff by calling `set_context(key="pending_task", value="none pending", updated_by="claude-code")`.
5. Next time you're in Claude.ai, a `get_history(source="claude-code")` call shows the completed session summary — confirmation the task was picked up and finished, with no copy-pasting required.

## Contributing

Issues and pull requests are welcome. If you hit a bug or have a feature idea, open an issue describing it; if you want to submit a fix, fork the repo, make your change on a branch, and open a PR against `main` with a short description of what changed and why. There's no formal process beyond that — this is a small project.

## License

MIT — see [LICENSE](LICENSE). Copyright (c) 2026 Eric Ford (Everford).

## Acknowledgments

The architecture, design decisions, and integration of ClaudeCode Connect — the key-value handoff model, the Funnel-based exposure approach, and the scoping decisions around what this bridge should and shouldn't do — are the author's. Claude (Anthropic) assisted with implementation: writing the server code, wiring up the MCP tools, and drafting deployment scripting and documentation.
