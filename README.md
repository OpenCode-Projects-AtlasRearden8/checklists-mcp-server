# checklists-mcp-server

A tiny MCP server that puts **reusable checklists** under your AI agent's
control: create list templates, check off items with notes, reset for the
next run, and report what's left. Storage is plain JSON on your own disk —
local-first, no accounts, no network calls, no secrets.

Built by [Asdale Dynamics, LLC](https://dylanasdale.github.io) — Dylan
Asdale's beginner-friendly AI education project, explaining AI "like a
friend would." Part of a companion series of local MCP servers
(`notes-mcp-server`, `bookshelf-mcp-server`) that share storage patterns but
never overlap: notes hold prose, bookshelf tracks books, checklists track
*reusable step lists*.

## Features

- **9 tools**: create, list, get, check/uncheck items, add items, reset, report remaining, delete
- **4 built-in templates**: launch-day, travel, deep-clean, book-publishing (KDP)
- **Flexible references**: check off item `3` or check off `"charge phone"` by title text
- **Notes**: attach a note when checking an item ("carry-on only")
- **Reset for reuse**: keep notes but untick everything for the next run
- **JSON storage**: one file per checklist, atomic writes, `.bak` auto-backup before rewrite
- **Path safety**: strict slug validation, traversal/~-expansion/symlink refusal
- **Zero dependencies**: Python 3.10+ standard library only, stdio transport

Smoke-tested against common foot-guns too: LSP-style `Content-Length`
framing is rejected with a clear error instead of hanging.

## Quick start

```bash
python3 server.py
```

Run `python3 server.py` directly to pump JSON-RPC over stdio, or point your
MCP client (opencode, Claude Desktop, etc.) at it:

```json
{
  "mcpServers": {
    "checklists": {
      "command": "python3",
      "args": ["/absolute/path/to/checklists-mcp-server/server.py"]
    }
  }
}
```

Data lands in `./checklists_data/` by default — pass another path as the
first argument if you prefer: `python3 server.py /home/me/mylists`.

## Tools

| Tool | Purpose |
|------|---------|
| `create_checklist` | New checklist from items or a built-in template |
| `list_checklists` | All checklists with progress counts |
| `get_checklist` | One checklist rendered as checkbox text |
| `check_item` / `uncheck_item` | Tick or untick by number or title; optional note |
| `add_item` | Append a new step |
| `reset_checklist` | Untick everything for the next run (notes kept by default) |
| `report_remaining` | What's left: counts, percent, open items |
| `delete_checklist` | Remove permanently (prefer reset) |

## Smoke test (5 minutes)

1. Run the unit test suite:

   ```bash
   python3 -m unittest -v
   ```

2. Talk to it over stdio by hand:

   ```bash
   python3 server.py <<'EOF'
   {"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}
   {"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"create_checklist","arguments":{"name":"Launch Day","template":"launch-day"}}}
   {"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"check_item","arguments":{"slug":"launch-day","ref":1,"note":"locked"}}}
   {"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"report_remaining","arguments":{"slug":"launch-day"}}}
   {"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"get_checklist","arguments":{"slug":"launch-day"}}}
   EOF
   ```

   Expect tool responses containing the checked item and the "Still left"
   summary. Then open `checklists_data/launch-day.json` — that's your whole
   stored state.

3. Try the rejection cases from a clean copy of the data folder:

   ```bash
   echo '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"create_checklist","arguments":{"name":"hack","template":"../../etc"}}}' | (python3 server.py /tmp/clean_data)
   ```

   Expect an error message naming valid templates — no files created
   anywhere outside your storage dir.

## Sample checklists

Four ready-made JSON files ship in `samples/` (also pre-seeded in
`checklists_data/`): launch-day, travel, deep-clean, book-publishing-kdp.
Copy them into whatever data directory you point the server at.

## Privacy & security

- All data stays in your local folder in plain JSON you own.
- No secrets, telemetry, or network calls of any kind.
- Slugs are validated against a strict pattern; relative-path traversal,
  `~` expansion, and symlink tricks are refused by `safe_path()`.

## License

MIT — see [LICENSE](LICENSE). Copyright (c) 2026 Asdale Dynamics, LLC.
