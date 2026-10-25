# STATUS.md — checklists-mcp-server

Built 2026-10-25 by opencode for Asdale Dynamics, LLC.

## What was built

- `server.py` — zero-dependency MCP server (Python 3.10+ stdlib, JSON-RPC 2.0 over stdio, protocol 2024-11-05). 9 tools:
  - `create_checklist` (items or built-in templates: launch-day, travel, deep-clean, book-publishing/KDP)
  - `list_checklists`, `get_checklist`, `check_item`, `uncheck_item`, `add_item`, `reset_checklist`, `report_remaining`, `delete_checklist`
  - Items can be referenced by 1-based number or title text; optional note on check
- JSON storage (`./checklists_data/` by default, overridable via argv): one file per checklist, atomic writes via temp-file + `os.replace`, auto-backup copy before each rewrite then cleaned up on success
- Path safety: strict slug `^[a-z0-9][a-z0-9_-]{0,63}$` on both slugs and user store paths, traversal/`~`/absolute/symlink refusal in `safe_path()`
- Graceful protocol errors: invalid JSON and LSP-style `Content-Length` framing return JSON-RPC errors instead of crashing or hanging
- `samples/` with four pre-seeded checklists (also present in `checklists_data/`)
- `test_server.py` — 21 unit tests
- `README.md` — usage + 5-minute smoke-test guide; credits Asdale Dynamics, LLC
- `LICENSE` — MIT, copyright line exactly `Copyright (c) 2026 Asdale Dynamics, LLC`
- `.gitignore` — test/dbg dirs and OS droppings, not user data

## How to run

```bash
python3 server.py                      # stdio, data in ./checklists_data
python3 server.py /path/to/data_dir    # custom storage location
python3 -m unittest                    # test suite
```

Configure any MCP client with:
`{"command": "python3", "args": ["/abs/path/to/server.py"]}`

## Verification results (all performed before the commit)

- `python3 -m unittest`: **21/21 passed** — round-trips (save/load, check/uncheck/reset, numeric & title refs, delete), storage (backup lifecycle, atomic replace), security (slug validation, traversal, `~`, absolute paths, symlinks), and real-subprocess stdio smoke tests (initialize → create → check, LSP-framing rejection, invalid-JSON handling)
- Manual stdio smoke: initialize, list_checklists, and get_checklist on a fresh folder produced correct JSON-RPC results and rendered checkbox text (ran live; `travel` showed 0/7 with all items listed)
- Traversal smoke (`tools/call create_checklist` with `template="../../etc"`): returned a clean `-32000` error naming valid templates; no files written outside the storage dir

## Known gaps

- Single user / single write process at a time (uses one per-process lock; no cross-process file locking)
- No item reordering, due dates, or priorities
- No migration/versioning field on the JSON documents yet (v1 shape only)
- Tool error semantics use a plain -32000 code rather than the newer MCP 2025 error payload shapes
- No 2025-03-26 protocol features (resources, prompts, subscriptions) — declared empty capability lists so basic clients negotiate cleanly
