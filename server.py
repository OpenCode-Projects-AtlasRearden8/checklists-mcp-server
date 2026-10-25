#!/usr/bin/env python3
"""checklists-mcp-server: a tiny MCP server (stdio, Python stdlib only).

Exposes reusable checklists your AI agent can manage: create checklist
templates, check off items with notes, reset for the next run, and report
what's left. State is stored as JSON on disk with path safety and
auto-backup before every write.

MCP protocol: JSON-RPC 2.0 over stdio, minimal handshake (initialize,
initialized, tools/list, tools/call). No third-party dependencies.

Copyright (c) 2026 Asdale Dynamics, LLC. MIT licensed.
"""

import json
import os
import re
import shutil
import sys
import tempfile
import threading
import unicodedata
from pathlib import Path

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "checklists-mcp-server"
SERVER_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def safe_slug(name: str) -> str:
    """Return a filesystem-safe slug, or raise ValueError."""
    if not isinstance(name, str):
        raise ValueError("name must be a string")
    if "/" in name or "\\" in name:
        raise ValueError("name cannot contain path separators")
    slug = unicodedata.normalize("NFKD", name)
    slug = "".join(c for c in slug if unicodedata.category(c) != "Mn")
    slug = slug.lower().replace(" ", "-")
    slug = re.sub(r"[^a-z0-9_-]", "", slug)
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    if not slug:
        raise ValueError("name must contain at least one letter or number")
    if not SLUG_RE.match(slug):
        raise ValueError(f"name {slug!r} is not a safe slug")
    return slug


def safe_path(base: Path, user_path: str) -> Path:
    """Resolve user_path under base, refusing escapes and symlinks."""
    if not isinstance(user_path, str):
        raise ValueError("path must be a string")
    if os.path.isabs(user_path) or user_path.startswith("~"):
        raise ValueError("absolute paths and ~ are not allowed")
    candidate = (base / user_path).resolve()
    base_resolved = base.resolve()
    if candidate != base_resolved and not candidate.is_relative_to(base_resolved):
        raise ValueError(f"path escapes storage directory: {user_path!r}")
    if candidate.exists() and candidate.is_symlink():
        raise ValueError("symlinks are not allowed")
    return candidate


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


class Store:
    """JSON file storage with single-object documents and atomic writes."""

    def __init__(self, storage_dir: Path):
        self.base = Path(storage_dir).resolve()
        self.base.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path_for(self, kind: str, slug: str) -> Path:
        if not SLUG_RE.match(slug):
            raise ValueError(f"invalid slug: {slug!r}")
        return safe_path(self.base / kind, slug + ".json")

    def exists(self, kind: str, slug: str) -> bool:
        return self._path_for(kind, slug).exists()

    def load(self, kind: str, slug: str):
        path = self._path_for(kind, slug)
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def save(self, kind: str, slug: str, doc: dict) -> None:
        path = self._path_for(kind, slug)
        path.parent.mkdir(parents=True, exist_ok=True)
        backup = None
        if path.exists():
            tmp = Path(tempfile.mkstemp(dir=path.parent, suffix=".bak")[1])
            tmp.unlink()
            shutil.copy2(path, tmp)
            backup = tmp
        with self._lock:
            fd, tmp_write = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(doc, f, ensure_ascii=False, indent=2)
                    f.write("\n")
                os.replace(tmp_write, path)
            except BaseException:
                try:
                    os.unlink(tmp_write)
                except OSError:
                    pass
                raise
            finally:
                if backup is not None and backup.exists():
                    backup.unlink()

    def delete(self, kind: str, slug: str) -> bool:
        path = self._path_for(kind, slug)
        if path.exists():
            path.unlink()
            return True
        return False

    def list_slugs(self, kind: str):
        d = safe_path(self.base / kind, "")
        if not d.exists():
            return []
        return sorted(
            p.stem
            for p in d.glob("*.json")
            if p.is_file() and not p.name.endswith(".bak.json")
            and not p.name.endswith(".tmp")
        )


# ---------------------------------------------------------------------------
# Checklist model
# ---------------------------------------------------------------------------


class ChecklistError(Exception):
    pass


def new_checklist(name: str, items, description: str = ""):
    slug = safe_slug(name)
    titles = []
    for it in items:
        title = str(it).strip() if isinstance(it, str) else str(it)
        if not title:
            raise ChecklistError("items cannot be empty")
        titles.append(title)
    if not titles:
        raise ChecklistError("a checklist needs at least one item")
    return {
        "name": name,
        "slug": slug,
        "description": description,
        "items": [
            {"title": t, "done": False, "note": ""} for t in titles
        ],
        "history": [],
    }


def check_item(checklist, ref, note: str = ""):
    """Check an item off by 1-based position or title substring."""
    item = find_item(checklist, ref)
    item["done"] = True
    if note:
        item["note"] = str(note)
    checklist["history"].append(
        {"action": "check", "ref": ref, "at_item": item["title"]}
    )
    return item


def uncheck_item(checklist, ref):
    item = find_item(checklist, ref)
    item["done"] = False
    checklist["history"].append(
        {"action": "uncheck", "ref": ref, "at_item": item["title"]}
    )
    return item


def find_item(checklist, ref):
    if isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit()):
        idx = int(ref)
        items = checklist["items"]
        if 1 <= idx <= len(items):
            return items[idx - 1]
        raise ChecklistError(
            f"item number {idx} out of range (1..{len(items)})"
        )
    if isinstance(ref, str) and ref.strip():
        needle = ref.strip().lower()
        matches = [i for i in checklist["items"] if needle in i["title"].lower()]
        if len(matches) == 1:
            return matches[0]
        raise ChecklistError(
            f"'{ref}' matches {len(matches)} items; use more of the title or a number"
        )
    raise ChecklistError("ref must be a 1-based number or title text")


def reset_checklist(checklist, keep_notes: bool = True):
    for item in checklist["items"]:
        item["done"] = False
        if not keep_notes:
            item["note"] = ""
    checklist["history"].append({"action": "reset", "keep_notes": keep_notes})
    return checklist


def report(checklist):
    items = checklist["items"]
    done = sum(1 for i in items if i["done"])
    remaining = [
        {"n": k + 1, "title": i["title"]}
        for k, i in enumerate(items)
        if not i["done"]
    ]
    return {
        "name": checklist["name"],
        "slug": checklist["slug"],
        "total": len(items),
        "done": done,
        "remaining": len(items) - done,
        "percent": round(done / len(items) * 100) if items else 0,
        "remaining_items": remaining,
    }


def render_text(checklist):
    lines = []
    r = report(checklist)
    lines.append(f"{checklist['name']} — {r['done']}/{r['total']} done ({r['percent']}%)")
    if checklist.get("description"):
        lines.append(checklist["description"])
    lines.append("")
    for k, item in enumerate(checklist["items"]):
        mark = "x" if item["done"] else " "
        lines.append(f"[{mark}] {k + 1}. {item['title']}")
        if item["note"]:
            lines.append(f"      note: {item['note']}")
    lines.append("")
    if r["remaining"]:
        lines.append(f"Still left: {', '.join(f'{i['n']}. {i['title']}' for i in r['remaining_items'])}")
    else:
        lines.append("All done!")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool definitions + handlers
# ---------------------------------------------------------------------------

TEMPLATES = {
    "launch-day": {
        "name": "Launch Day",
        "description": "Checklist for launch day of a product, book, or site.",
        "items": [
            "Confirm launch date and time are locked",
            "Final proofread of all public copy",
            "Test purchase/checkout path end to end",
            "Announcement drafted (email + social)",
            "Support/friction fallback plan ready",
            "Analytics and tracking verified",
            "Rest the night before",
        ],
    },
    "travel": {
        "name": "Travel",
        "description": "Packing and prep for any trip.",
        "items": [
            "Book transportation and lodging",
            "Check id/passport not expired",
            "Charge devices, pack cables",
            "Medications and first aid",
            "Stop mail, water plants, pet plan",
            "Snacks and refillable water bottle",
            "Leave-home final sweep (stove, locks, lights)",
        ],
    },
    "deep-clean": {
        "name": "Deep Clean",
        "description": "Low-energy friendly deep-clean loop for one room at a time.",
        "items": [
            "Gather supplies in one basket",
            "Set a 20-minute timer; smallest surface first",
            "Windows and mirrors",
            "Dust high to low",
            "Vacuum or mop floors last",
            "Take out trash and reset supplies",
            "Note anything to fix later",
        ],
    },
    "book-publishing": {
        "name": "Book Publishing (KDP)",
        "description": "Self-publishing-ready checklist for a print/Kindle book.",
        "items": [
            "Manuscript final pass (spelling, names, facts)",
            "Front/back matter complete (title page, copyright, index)",
            "Cover meets final trim size and DPI",
            "Interior PDF and EPUB rendered and checked",
            "Blurb, categories, and 7 keywords drafted",
            "Print and ebook pricing set",
            "Previewer checked on phone, tablet, and print",
            "ISBN/copyright line verified",
            "Publish, then order a physical proof",
        ],
    },
}


def tool_definitions():
    return [
        {
            "name": "create_checklist",
            "description": (
                "Create a checklist (from your own items, or from a built-in "
                "template like launch-day, travel, deep-clean, book-publishing). "
                "Returns the checklist slug."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Human name; a safe slug is derived from it"},
                    "template": {"type": "string", "description": "Optional built-in template key"},
                    "items": {"type": "array", "items": {"type": "string"}, "description": "Optional item titles (ignored if template given)"},
                    "description": {"type": "string"},
                },
                "required": ["name"],
            },
        },
        {
            "name": "list_checklists",
            "description": "List stored checklists with progress summaries.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "get_checklist",
            "description": "Get one checklist in friendly rendered text plus raw JSON.",
            "inputSchema": {
                "type": "object",
                "properties": {"slug": {"type": "string"}},
                "required": ["slug"],
            },
        },
        {
            "name": "check_item",
            "description": "Check off an item by 1-based number or title text; optional note attached.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "slug": {"type": "string"},
                    "ref": {"description": "1-based item number or part of the title"},
                    "note": {"type": "string"},
                },
                "required": ["slug", "ref"],
            },
        },
        {
            "name": "uncheck_item",
            "description": "Uncheck an item by number or title text.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "slug": {"type": "string"},
                    "ref": {},
                },
                "required": ["slug", "ref"],
            },
        },
        {
            "name": "add_item",
            "description": "Add a new item to an existing checklist.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "slug": {"type": "string"},
                    "title": {"type": "string"},
                },
                "required": ["slug", "title"],
            },
        },
        {
            "name": "reset_checklist",
            "description": "Reset all items to unchecked for the next run (keeps notes by default).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "slug": {"type": "string"},
                    "keep_notes": {"type": "boolean", "default": True},
                },
                "required": ["slug"],
            },
        },
        {
            "name": "report_remaining",
            "description": "Report what's left: counts, percent, and the open items.",
            "inputSchema": {
                "type": "object",
                "properties": {"slug": {"type": "string"}},
                "required": ["slug"],
            },
        },
        {
            "name": "delete_checklist",
            "description": "Permanently delete a checklist. Use reset_checklist for reuse instead.",
            "inputSchema": {
                "type": "object",
                "properties": {"slug": {"type": "string"}},
                "required": ["slug"],
            },
        },
    ]


def slug_input(store, slug):
    if not isinstance(slug, str) or not slug:
        raise ChecklistError("slug is required")
    doc = store.load("checklists", slug)
    if doc is None:
        raise ChecklistError(f"no checklist named {slug!r}; call list_checklists")
    return doc


def call_tool(store, name, arguments):
    if name == "create_checklist":
        tpl_key = arguments.get("template")
        if tpl_key:
            if tpl_key not in TEMPLATES:
                raise ChecklistError(
                    f"unknown template {tpl_key!r}; options: {', '.join(sorted(TEMPLATES))}"
                )
            tpl = TEMPLATES[tpl_key]
            name_arg = arguments.get("name") or tpl["name"]
            cl = new_checklist(name_arg, tpl["items"], arguments.get("description", tpl["description"]))
        else:
            items = arguments.get("items") or []
            if not items:
                raise ChecklistError("provide items[] or a template")
            cl = new_checklist(arguments["name"], items, arguments.get("description", ""))
        store.save("checklists", cl["slug"], cl)
        return data_result({"created": cl["slug"], "report": report(cl)})

    if name == "list_checklists":
        out = []
        for slug in store.list_slugs("checklists"):
            doc = store.load("checklists", slug)
            if doc:
                out.append(report(doc))
        return data_result({"checklists": out})

    if name == "get_checklist":
        cl = slug_input(store, arguments["slug"])
        return {"content": [{"type": "text", "text": render_text(cl)}], "metadata": {"checklist": cl}}

    if name in ("check_item", "uncheck_item"):
        cl = slug_input(store, arguments["slug"])
        ref = arguments["ref"]
        if name == "check_item":
            item = check_item(cl, ref, str(arguments.get("note", "")))
        else:
            item = uncheck_item(cl, ref)
        store.save("checklists", cl["slug"], cl)
        return data_result({"item": item, "report": report(cl)})

    if name == "add_item":
        cl = slug_input(store, arguments["slug"])
        title = str(arguments.get("title", "")).strip()
        if not title:
            raise ChecklistError("title cannot be empty")
        cl["items"].append({"title": title, "done": False, "note": ""})
        store.save("checklists", cl["slug"], cl)
        return data_result({"added": title, "report": report(cl)})

    if name == "reset_checklist":
        cl = slug_input(store, arguments["slug"])
        reset_checklist(cl, bool(arguments.get("keep_notes", True)))
        store.save("checklists", cl["slug"], cl)
        return data_result({"reset": cl["slug"], "report": report(cl)})

    if name == "report_remaining":
        cl = slug_input(store, arguments["slug"])
        return data_result(report(cl))

    if name == "delete_checklist":
        slug = arguments["slug"]
        removed = store.delete("checklists", slug)
        if not removed:
            raise ChecklistError(f"no checklist named {slug!r}")
        return data_result({"deleted": slug})

    raise ChecklistError(f"unknown tool: {name}")


def data_result(payload: dict):
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}],
    }


# ---------------------------------------------------------------------------
# JSON-RPC plumbing
# ---------------------------------------------------------------------------


class McpServer:
    def __init__(self, store: Store):
        self.store = store

    def handle(self, message: dict):
        method = message.get("method")
        msg_id = message.get("id")
        try:
            if method == "initialize":
                result = {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                }
            elif method == "notifications/initialized":
                return None
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": tool_definitions()}
            elif method == "tools/call":
                params = message.get("params", {})
                result = call_tool(self.store, params.get("name"), params.get("arguments") or {})
            elif method == "resources/list":
                result = {"resources": []}
            elif method == "prompts/list":
                result = {"prompts": []}
            else:
                return error_response(msg_id, -32601, f"method not found: {method}")
            return {"jsonrpc": "2.0", "id": msg_id, "result": result}
        except (ChecklistError, ValueError) as exc:
            return error_response(msg_id, -32000, str(exc))
        except Exception as exc:  # keep the server alive on unexpected errors
            return error_response(msg_id, -32603, f"internal error: {exc}")


def error_response(msg_id, code, message):
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def write_line(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


class LspFramingError(Exception):
    """Line received was framed with an HTTP-style Content-Length header."""


def decode_message(line: str) -> dict:
    """Accept newline-delimited JSON; reject LSP-style Content-Length framing."""
    if line[:15].lower() == "content-length:":
        raise LspFramingError(
            "LSP-style Content-Length framing detected; use newline-delimited JSON"
        )
    return json.loads(line)


def serve(storage_dir: Path) -> None:
    store = Store(storage_dir)
    server = McpServer(store)
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            message = decode_message(line)
        except json.JSONDecodeError as exc:
            write_line(error_response(None, -32700, f"invalid JSON: {exc}"))
            continue
        except LspFramingError as exc:
            write_line(error_response(None, -32700, str(exc)))
            continue
        response = server.handle(message)
        if response is not None:
            write_line(response)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    storage_dir = Path(argv[0] if argv else "./checklists_data")
    serve(storage_dir)


if __name__ == "__main__":
    main()
