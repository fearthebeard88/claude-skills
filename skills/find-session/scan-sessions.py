#!/usr/bin/env python3
"""Scan every Claude Code session across all project directories and print a
JSON array of session metadata. Primary runtime on Unix (Linux/macOS), where
python3 is near-universal. Behaviour mirrors scan-sessions.ps1 exactly — same
args, same JSON output shape — so the two stay interchangeable.

    scan-sessions.py                      # all sessions, newest-active first
    scan-sessions.py --query "netsuite"   # only sessions matching the query
    scan-sessions.py --query "bug" --limit 20
    scan-sessions.py --min-size-kb 0      # include empty/aborted stubs too
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
ap.add_argument("--limit", type=int, default=40)
# Pagination: skip the first N ranked rows, then show --limit of them. Row
# numbers stay global (offset+1, offset+2, …) so --pick lines up across pages.
ap.add_argument("--offset", type=int, default=0)
# Hide aborted/empty session stubs by default. Real sessions are >~20 KB;
# empty starts are <3 KB. Pass --min-size-kb 0 to show everything.
ap.add_argument("--min-size-kb", type=float, default=3.0)
# Off by default to keep output lean; add the preview column only when titles
# collide and you need it to disambiguate.
ap.add_argument("--preview", action="store_true")
# Resume mode: with --pick N, skip the listing and print just the ready-to-run
# resume command for row N (1-based) of the SAME ranking. Pass the identical
# --query / --min-size-kb you listed with so the row numbers line up. This is
# what keeps id + cwd (the expensive fields) out of the every-browse output.
ap.add_argument("--pick", type=int, default=0)
args = ap.parse_args()

root = Path.home() / ".claude" / "projects"
# The session we're running inside — exclude it; you're already attached to it.
# Absent when run outside Claude Code, so this is a no-op there.
current = os.environ.get("CLAUDE_CODE_SESSION_ID")


def emit(text):
    # Write UTF-8 with LF, bypassing Windows text-mode CRLF translation and the
    # locale codepage — keeps output byte-identical to scan-sessions.ps1.
    sys.stdout.buffer.write((text + "\n").encode("utf-8"))


def clean(s):
    """Collapse whitespace so multi-line prompts render on one line."""
    return re.sub(r"\s+", " ", s).strip() if s else None


sessions = []
# Resumable sessions live exactly one level down: projects/<dir>/<id>.jsonl.
# Do NOT recurse deeper — nested subagents/ and workflows/ folders hold
# transcript artifacts (e.g. journal.jsonl) that aren't resumable sessions.
if root.is_dir():
    for proj_dir in sorted(root.iterdir()):
        if not proj_dir.is_dir():
            continue
        for f in proj_dir.glob("*.jsonl"):
            sid = f.stem
            # Skip subagent sidechain transcripts — not resumable sessions.
            if sid.startswith("agent-"):
                continue
            # Skip the current session — no point resuming what you're in.
            if current and sid == current:
                continue
            try:
                st = f.stat()
            except OSError:
                continue
            # Skip aborted/empty stubs unless asked to keep them.
            if st.st_size / 1024 < args.min_size_kb:
                continue

            title = prompt = cwd = branch = None
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
                if t == "last-prompt" and rec.get("lastPrompt"):
                    prompt = rec["lastPrompt"]
                # cwd / branch are stable — take the first non-empty we see.
                if not cwd and rec.get("cwd"):
                    cwd = rec["cwd"]
                if not branch and rec.get("gitBranch") is not None:
                    branch = rec["gitBranch"]

            if not cwd:
                # Lossy fallback: decode the folder name (only for the rare
                # session that somehow lacks a cwd record).
                cwd = re.sub(r"^([A-Za-z])--", r"\1:\\", proj_dir.name).replace("-", "\\")
            title = clean(title) or clean(prompt) or "(untitled)"
            preview_full = clean(prompt) or ""
            preview = preview_full[:140] + "..." if len(preview_full) > 140 else preview_full

            sessions.append({
                "id": sid,
                "title": title,
                "preview": preview,
                "cwd": cwd,
                "branch": clean(branch) or "",
                "lastActive": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                "_mtime": st.st_mtime,
                "sizeKB": round(st.st_size / 1024, 1),
            })

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
    scored.sort(key=lambda x: (x[0], x[1]["_mtime"]), reverse=True)
    ranked = [s for _, s in scored]
else:
    ranked = sorted(sessions, key=lambda s: s["_mtime"], reverse=True)

# --- resume mode -------------------------------------------------------------
# Print only the chosen row's resume command; id + cwd cost is paid once, here.
if args.pick:
    if 1 <= args.pick <= len(ranked):
        s = ranked[args.pick - 1]
        emit(f'cd "{s["cwd"]}" && claude --resume {s["id"]}')
    else:
        emit(f"ERROR: --pick {args.pick} out of range (1-{len(ranked)})")
    sys.exit(0)

# --- list mode ---------------------------------------------------------------
# Tab-separated, display-only columns — no UUIDs, no full paths, no JSON keys.
# title has whitespace collapsed already, so it can't contain a tab/newline.
home = str(Path.home())


def dirlabel(cwd):
    if cwd == home:
        return "~"
    return cwd.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] or cwd


lines = []
for s in ranked[args.offset : args.offset + args.limit]:
    cols = [s["title"], dirlabel(s["cwd"]), s["lastActive"]]
    if args.preview:
        cols.append(s["preview"])
    lines.append("\t".join(cols))
emit("\n".join(lines))
