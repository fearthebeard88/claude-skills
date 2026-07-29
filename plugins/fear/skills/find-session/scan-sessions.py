#!/usr/bin/env python3
"""Scan every Claude Code session across all project directories and print a
tab-separated table of session metadata. Primary runtime on Unix (Linux/macOS),
where python3 is near-universal. Behaviour mirrors scan-sessions.ps1 exactly —
same args, byte-identical output — so the two stay interchangeable.

    scan-sessions.py                      # all sessions, newest-active first
    scan-sessions.py --query "netsuite"   # only sessions matching the query
    scan-sessions.py --query "bug" --limit 20
    scan-sessions.py --min-size-kb 0      # include empty/aborted stubs too
    scan-sessions.py --pick e5faf172      # resume command for that session id
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--query", default="")
ap.add_argument("--limit", type=int, default=15)
# Pagination: skip the first N ranked rows, then show --limit of them. Row
# numbers stay global (offset+1, offset+2, …) for display purposes.
ap.add_argument("--offset", type=int, default=0)
# Hide aborted/empty session stubs by default. Real sessions are >~20 KB;
# empty starts are <3 KB. Pass --min-size-kb 0 to show everything.
ap.add_argument("--min-size-kb", type=float, default=3.0)
# Off by default to keep output lean; add the preview column only when titles
# collide and you need it to disambiguate.
ap.add_argument("--preview", action="store_true")
# Resume mode: print just the ready-to-run resume command for one session.
# Takes the session's short id (the first column of the listing) — an id is a
# STABLE handle, so it stays correct even if another live session's mtime
# reorders the ranking between the list and the pick. A bare 1-4 digit number
# is still accepted as a row index into the same ranking, but that form drifts
# under concurrent sessions; prefer the id.
ap.add_argument("--pick", default="")
args = ap.parse_args()

root = Path.home() / ".claude" / "projects"
# The session we're running inside — exclude it; you're already attached to it.
# Absent when run outside Claude Code, so this is a no-op there.
current = os.environ.get("CLAUDE_CODE_SESSION_ID")

# How many leading hex chars of the session uuid form the short id / pick handle.
ID_LEN = 8

pick = args.pick.strip()
# A 1-4 digit token is a row index; anything else is an id prefix. Short ids are
# ID_LEN chars, so the two forms can't collide.
pick_by_row = bool(pick) and re.fullmatch(r"\d{1,4}", pick) is not None
pick_by_id = bool(pick) and not pick_by_row
# An id lookup is exact, so the stub filter can only get in its way — the id
# came from a listing, and forcing 0 means the caller never has to re-pass the
# --min-size-kb they listed with.
min_size_kb = 0.0 if pick_by_id else args.min_size_kb

# Wrapper text that a user record can carry instead of a real prompt: slash
# commands, local-command plumbing, injected reminders. Never a useful title.
WRAPPER_RE = re.compile(
    r"^<(local-command-caveat|local-command-stdout|local-command-stderr|"
    r"command-name|command-message|command-args|system-reminder)\b"
    r"|^Caveat: The messages below",
    re.IGNORECASE,
)
# A slash-command invocation inside one of those wrappers. Weakest fallback, but
# "/fear:find-session" still identifies a session that holds no prose at all.
COMMAND_RE = re.compile(r"<command-name>\s*(/?[^<\s]+)\s*</command-name>", re.IGNORECASE)


def emit(text):
    # Write UTF-8 with LF, bypassing Windows text-mode CRLF translation and the
    # locale codepage — keeps output byte-identical to scan-sessions.ps1.
    sys.stdout.buffer.write((text + "\n").encode("utf-8"))


def clean(s):
    """Collapse whitespace so multi-line prompts render on one line."""
    return re.sub(r"\s+", " ", s).strip() if s else None


def truncate(s, n):
    return s[:n] + "..." if len(s) > n else s


def user_text(rec):
    """Text a user record carries, or None for tool results and injected context.

    Fallback source for sessions that carry no aiTitle and no inline
    lastPrompt — see the `last-prompt` note in the scan loop. Returns wrapper
    text as-is; the caller classifies it.
    """
    if rec.get("isSidechain") or rec.get("isMeta"):
        return None
    msg = rec.get("message")
    if not isinstance(msg, dict):
        return None
    content = msg.get("content")
    if isinstance(content, str):
        parts = [content]
    elif isinstance(content, list):
        # Text blocks only — a tool_result block is machine output, not a prompt.
        parts = [b.get("text") or "" for b in content
                 if isinstance(b, dict) and b.get("type") == "text"]
    else:
        return None
    return clean(" ".join(p for p in parts if p))


sessions = []
# Resumable sessions live exactly one level down: projects/<dir>/<id>.jsonl.
# Do NOT recurse deeper — nested subagents/ and workflows/ folders hold
# transcript artifacts (e.g. journal.jsonl) that aren't resumable sessions.
if root.is_dir():
    for proj_dir in sorted(root.iterdir()):
        if not proj_dir.is_dir():
            continue
        # Sorted so file order is deterministic and matches the PowerShell
        # script's (Get-ChildItem sorts by name); only exact ties depend on it.
        for f in sorted(proj_dir.glob("*.jsonl")):
            sid = f.stem
            # Skip subagent sidechain transcripts — not resumable sessions.
            if sid.startswith("agent-"):
                continue
            # Skip the current session — no point resuming what you're in.
            if current and sid == current:
                continue
            # Picking by id needs exactly one file's contents, so don't parse
            # the rest — this is what makes --pick <id> near-instant.
            if pick_by_id and not sid.lower().startswith(pick.lower()):
                continue
            try:
                st = f.stat()
            except OSError:
                continue
            # Skip aborted/empty stubs unless asked to keep them.
            if st.st_size / 1024 < min_size_kb:
                continue

            title = prompt = cwd = branch = relocated = None
            fallback = fallback_cmd = None
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for line in text.splitlines():
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if not isinstance(rec, dict):
                    continue
                t = rec.get("type")
                # Titles/prompts are re-emitted as the session grows — keep last.
                if t == "ai-title" and rec.get("aiTitle"):
                    title = rec["aiTitle"]
                # A last-prompt record comes in two shapes: one carries the text
                # inline as `lastPrompt`, the other is just a `leafUuid` pointer
                # with no text at all. The pointer form leaves us nothing to show,
                # so track the last real user message as a fallback — otherwise
                # those sessions list as "(untitled)" with an empty preview and
                # can never match a query.
                if t == "last-prompt" and rec.get("lastPrompt"):
                    prompt = rec["lastPrompt"]
                if t == "user":
                    ut = user_text(rec)
                    if ut:
                        m = COMMAND_RE.search(ut)
                        if m:
                            fallback_cmd = m.group(1)
                        elif not WRAPPER_RE.search(ut):
                            fallback = ut
                # Take the LAST cwd, not the first: a /cd relocates the session,
                # and we want the directory it ended up in (the resumable one),
                # not the dead-end origin it started in.
                if rec.get("cwd"):
                    cwd = rec["cwd"]
                # A relocation records its destination explicitly — trust it over
                # any transient trailing cwd.
                if t == "relocated" and rec.get("relocatedCwd"):
                    relocated = rec["relocatedCwd"]
                if not branch and rec.get("gitBranch") is not None:
                    branch = rec["gitBranch"]

            cwd = relocated or cwd
            if not cwd:
                # Lossy fallback: decode the folder name (only for the rare
                # session that somehow lacks a cwd record).
                cwd = re.sub(r"^([A-Za-z])--", r"\1:\\", proj_dir.name).replace("-", "\\")
            # Fallback chain, strongest first: the AI's own title, the inline
            # last prompt, the last real user prose, the last slash command.
            prompt = clean(prompt)
            fallback = fallback or fallback_cmd
            title = clean(title) or prompt or (truncate(fallback, 80) if fallback else None) or "(untitled)"
            preview_full = prompt or fallback or ""
            preview = truncate(preview_full, 140)

            sessions.append({
                "id": sid,
                "shortId": sid[:ID_LEN],
                # Deterministic tie-break for equal mtimes. Dashes stripped so
                # the key is pure lowercase hex — PowerShell's culture-aware
                # string sort and Python's ordinal sort agree on that alphabet,
                # but not on where '-' lands.
                "sortKey": sid.replace("-", ""),
                "title": title,
                "preview": preview,
                "cwd": cwd,
                "branch": clean(branch) or "",
                "lastActive": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                "_mtime": st.st_mtime,
                "sizeKB": round(st.st_size / 1024, 1),
            })

# --- resume mode: by id ------------------------------------------------------
# An id is resolved against every session, independent of --query/--offset/
# --limit ranking, so it can never point at the wrong row.
if pick_by_id:
    matches = sorted(sessions, key=lambda s: s["sortKey"])
    if len(matches) == 1:
        s = matches[0]
        emit(f'cd "{s["cwd"]}" && claude --resume {s["id"]}')
    elif not matches:
        emit(f"ERROR: --pick {pick} matched no session")
    else:
        ids = ", ".join(s["shortId"] for s in matches)
        emit(f"ERROR: --pick {pick} is ambiguous ({len(matches)} matches: {ids})")
    sys.exit(0)

# --- filter + rank -----------------------------------------------------------
q = args.query.strip().lower()
if q:
    terms = q.split()
    scored = []
    for s in sessions:
        hay = f"{s['title']} {s['preview']} {s['cwd']} {s['branch']}".lower()
        title_lc = s["title"].lower()
        score = 0
        for term in terms:
            if term in title_lc:
                score += 3
            elif term in hay:
                score += 1
        if score > 0:
            scored.append((score, s))
    # sortKey ascending breaks mtime ties identically in both scripts; without
    # it, equal-mtime rows order by directory listing and the two drift apart.
    scored.sort(key=lambda x: (-x[0], -x[1]["_mtime"], x[1]["sortKey"]))
    ranked = [s for _, s in scored]
else:
    ranked = sorted(sessions, key=lambda s: (-s["_mtime"], s["sortKey"]))

# --- resume mode: by row -----------------------------------------------------
# Legacy/interactive form. Drifts if another live session's mtime reorders the
# ranking between the listing and the pick — --pick <id> is the safe handle.
if pick_by_row:
    n = int(pick)
    if 1 <= n <= len(ranked):
        s = ranked[n - 1]
        emit(f'cd "{s["cwd"]}" && claude --resume {s["id"]}')
    else:
        emit(f"ERROR: --pick {n} out of range (1-{len(ranked)})")
    sys.exit(0)

# --- list mode ---------------------------------------------------------------
# Tab-separated, display-only columns plus the short id. No full uuids, no full
# paths, no JSON keys. title has whitespace collapsed already, so it can't
# contain a tab/newline.
home = str(Path.home())


def dirlabel(cwd):
    # Case-insensitive so it matches scan-sessions.ps1 (PowerShell -eq ignores
    # case) — Windows records the same home dir with varying drive-letter case.
    if cwd.lower() == home.lower():
        return "~"
    return cwd.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] or cwd


lines = []
for s in ranked[args.offset : args.offset + args.limit]:
    cols = [s["shortId"], s["title"], dirlabel(s["cwd"]), s["lastActive"]]
    if args.preview:
        cols.append(s["preview"])
    lines.append("\t".join(cols))
emit("\n".join(lines))
