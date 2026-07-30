#!/usr/bin/env python3
"""Scan every Claude Code session across all project directories and print a
tab-separated table of session metadata. Primary runtime on Unix (Linux/macOS),
where python3 is near-universal. Behaviour mirrors scan-sessions.ps1: same args,
and the same rows in the same order, so the two stay interchangeable.

    scan-sessions.py                      # all sessions, newest-active first
    scan-sessions.py --query "netsuite"   # only sessions matching the query
    scan-sessions.py --query "bug" --limit 20
    scan-sessions.py --min-size-kb 0      # include empty/aborted stubs too
    scan-sessions.py --pick e5faf172      # resume command for that session id
    scan-sessions.py --since 7d           # active in the last 7 days
    scan-sessions.py --since 2026-07-01 --before 2026-07-15

Test hooks (all default to the real values, so they're no-ops in normal use):

    FIND_SESSION_ROOT  scan root, instead of ~/.claude/projects
    FIND_SESSION_HOME  path treated as home for the `~` dir label
    FIND_SESSION_NOW   "now" in epoch ms, so relative --since/--before are fixed

These exist so both scripts can be aimed at a fixture store and diffed against
each other. PowerShell's $HOME is read-only, so without an env var there is no
way to point scan-sessions.ps1 anywhere but the real store — and therefore no way
to test the two implementations against identical input.
"""
import argparse
import calendar
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


class Parser(argparse.ArgumentParser):
    """Report bad args as a single `ERROR: …` line on stdout, exit 2.

    argparse's default is a usage block on stderr; scan-sessions.ps1 has no
    equivalent, and the callers of these scripts read stdout. Matching the
    existing `ERROR: --pick …` convention keeps every failure discoverable the
    same way on both platforms.
    """

    def error(self, message):
        sys.stdout.buffer.write(("ERROR: " + message + "\n").encode("utf-8"))
        raise SystemExit(2)


def nonneg_int(s):
    try:
        v = int(s)
    except ValueError:
        raise argparse.ArgumentTypeError("expects an integer, got '%s'" % s)
    if v < 0:
        # A negative offset would silently wrap the list slice below and return
        # the LAST rows instead of erroring — quietly wrong beats loudly wrong
        # only for the person who never notices.
        raise argparse.ArgumentTypeError("must be >= 0, got %d" % v)
    return v


def nonneg_float(s):
    try:
        v = float(s)
    except ValueError:
        raise argparse.ArgumentTypeError("expects a number, got '%s'" % s)
    if v < 0:
        raise argparse.ArgumentTypeError("must be >= 0, got %s" % v)
    return v


# allow_abbrev=False: argparse otherwise accepts any unambiguous prefix, so
# `--lim 3` would work here and be an unknown-argument error in
# scan-sessions.ps1. Caught by test-parity.py.
ap = Parser(allow_abbrev=False)
ap.add_argument("--query", default="")
ap.add_argument("--limit", type=nonneg_int, default=15)
# Pagination: skip the first N ranked rows, then show --limit of them. Row
# numbers stay global (offset+1, offset+2, …) for display purposes.
ap.add_argument("--offset", type=nonneg_int, default=0)
# Hide aborted/empty session stubs by default. Real sessions are >~20 KB;
# empty starts are <3 KB. Pass --min-size-kb 0 to show everything.
ap.add_argument("--min-size-kb", type=nonneg_float, default=3.0)
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
# Copy the resume command to the clipboard as well as printing it. Only
# meaningful with --pick; a no-op for a listing.
ap.add_argument("--copy", action="store_true")
# Time window, either end optional. Each takes a relative age ("7d", "12h",
# "2w") or an absolute local date ("2026-07-01"). The window is half-open:
# --since is inclusive, --before is exclusive, so `--since D --before D` is
# empty and adjacent windows don't double-count a session.
ap.add_argument("--since", default="")
ap.add_argument("--before", default="")
args = ap.parse_args()

# One "now" for the whole run, so two relative bounds can't be anchored a few
# milliseconds apart. FIND_SESSION_NOW (epoch ms) overrides it so tests of a
# relative window are deterministic; see the test hooks in the module docstring.
_now_env = os.environ.get("FIND_SESSION_NOW")
if _now_env:
    try:
        NOW_MS = int(_now_env)
    except ValueError:
        ap.error("FIND_SESSION_NOW must be epoch milliseconds, got '%s'" % _now_env)
else:
    NOW_MS = int(time.time() * 1000)

# No month unit: "m" would be ambiguous between minutes and months, and a
# calendar month isn't a fixed number of milliseconds anyway.
REL_RE = re.compile(r"^(\d+)([hdw])$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
UNIT_MS = {"h": 3_600_000, "d": 86_400_000, "w": 604_800_000}


def time_bound(flag, value):
    """Resolve a --since/--before value to epoch milliseconds, or None."""
    if not value:
        return None
    m = REL_RE.match(value)
    if m:
        return NOW_MS - int(m.group(1)) * UNIT_MS[m.group(2)]
    if DATE_RE.match(value):
        try:
            d = datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            ap.error("argument %s: '%s' is not a real date" % (flag, value))
        # Naive datetime, so .timestamp() reads it as LOCAL midnight — which
        # matches the lastActive column, also rendered in local time. A user
        # asking for "since 2026-07-01" means their own July 1st.
        return int(d.timestamp() * 1000)
    ap.error("argument %s: expects a relative age (7d, 12h, 2w) or a date "
             "(YYYY-MM-DD), got '%s'" % (flag, value))


SINCE_MS = time_bound("--since", args.since)
BEFORE_MS = time_bound("--before", args.before)

root = Path(os.environ.get("FIND_SESSION_ROOT") or Path.home() / ".claude" / "projects")
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
# Record timestamps look like 2026-07-29T19:38:58.753Z — fixed width, UTC,
# always exactly three fractional digits (all 5341 in the reference store
# matched). That fixed width plus the trailing Z is what makes a plain STRING
# comparison chronologically correct, which lets the scan track the maximum
# without parsing every record. Anything not matching exactly is ignored, so a
# format change degrades to file mtime rather than sorting wrongly — variable
# fractional widths would break string ordering ("…58.7Z" sorts below "…58Z").
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
TS_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"


def emit(text):
    # Write UTF-8 with LF, bypassing Windows text-mode CRLF translation and the
    # locale codepage — keeps output byte-identical to scan-sessions.ps1.
    sys.stdout.buffer.write((text + "\n").encode("utf-8"))


def clean(s):
    """Collapse whitespace so multi-line prompts render on one line."""
    return re.sub(r"\s+", " ", s).strip() if s else None


def resume_command(s):
    """The resume command, as TWO lines rather than `cd … && claude …`.

    A one-liner needs a separator, and no separator works in every shell a user
    might paste into: `&&` is a parse error in Windows PowerShell 5.1 — the shell
    this skill deliberately targets because it's always present — and `;` isn't a
    separator in cmd, where it becomes a literal argument to `cd`. Two lines run
    sequentially in bash, PowerShell 5.1, PowerShell 7 and cmd alike.

    Losing `&&` means losing its short-circuit, so `claude` would run even if the
    `cd` failed. emit_resume() covers that by refusing to print a command whose
    directory doesn't exist, which is the only common reason the `cd` fails.

    (cmd still needs `cd /d` to change drive as well as directory. That can't be
    expressed portably — `/d` is a bad argument to PowerShell's Set-Location
    alias — so a cmd user crossing drives has to add it. PowerShell, where `cd`
    switches drive on its own, is the realistic paste target on Windows.)
    """
    return 'cd "%s"\nclaude --resume %s' % (s["cwd"], s["id"])


def copy_to_clipboard(text):
    """Best-effort clipboard copy. Returns None on success, else a short reason.

    Deliberately never fatal: failing to copy shouldn't cost the user the command
    itself, which is always printed too.
    """
    # stdout stays LF for parity with scan-sessions.ps1, but the CLIPBOARD gets
    # the platform's own line ending — that text is going into a terminal paste,
    # where Windows expects CRLF. (Left unnormalised, Set-Clipboard stored LF and
    # clip.exe stored CRLF, so the two scripts disagreed on clipboard content.)
    if os.name == "nt":
        text = text.replace("\n", "\r\n")
    if sys.platform == "darwin":
        cands = [["pbcopy"]]
    elif os.name == "nt":
        # clip.exe reads the console codepage, so a non-ASCII path can mangle
        # here. Acceptable: SKILL.md never calls python on Windows, and the
        # command is printed regardless.
        cands = [["clip"]]
    else:
        cands = [["wl-copy"],
                 ["xclip", "-selection", "clipboard"],
                 ["xsel", "--clipboard", "--input"]]
    for c in cands:
        if not shutil.which(c[0]):
            continue
        try:
            p = subprocess.run(c, input=text.encode("utf-8"))
        except OSError as e:
            return str(e)
        if p.returncode == 0:
            return None
        return "%s exited %d" % (c[0], p.returncode)
    return "no clipboard tool found (tried %s)" % ", ".join(c[0] for c in cands)


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
    # An unreadable store shouldn't be a crash — skip what can't be listed and
    # report what can. Mirrors the per-file OSError guards below.
    try:
        proj_dirs = sorted(root.iterdir())
    except OSError:
        proj_dirs = []
    for proj_dir in proj_dirs:
        if not proj_dir.is_dir():
            continue
        # Sorted so file order is deterministic and matches the PowerShell
        # script's (Get-ChildItem sorts by name); only exact ties depend on it.
        try:
            proj_files = sorted(proj_dir.glob("*.jsonl"))
        except OSError:
            continue
        for f in proj_files:
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
            fallback = fallback_cmd = ts_max = None
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
                # MAXIMUM timestamp, not the last one seen: 57 of 73 sessions in
                # the reference store carry out-of-order timestamps, and in 8 of
                # them the final record is not the latest. Taking the last would
                # report those sessions as older than they are.
                ts = rec.get("timestamp")
                if isinstance(ts, str) and TS_RE.match(ts) and (ts_max is None or ts > ts_max):
                    ts_max = ts

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

            # "Last active" is the newest conversation record, not the file's
            # mtime. mtime is reset by anything that rewrites the file without
            # advancing the conversation — sync clients, backup restores, a
            # `cp -r` of ~/.claude — and it also moves when Claude Code appends
            # records that carry no timestamp of their own (ai-title,
            # last-prompt, mode, file-history-snapshot). In the reference store
            # mtime ran up to 168 h ahead of the last real activity, which
            # scrambles a recency-ordered listing. mtime stays as the fallback
            # for the rare session with no usable timestamp (1 of 73 there).
            active_ms = None
            last_active = None
            if ts_max:
                try:
                    dt = datetime.strptime(ts_max, TS_FMT)
                except ValueError:
                    dt = None  # matched the shape but isn't a real date
                if dt is not None:
                    # Integer milliseconds via calendar.timegm: exact, and never
                    # routed through a float, so it matches PowerShell's
                    # ToUnixTimeMilliseconds() bit for bit.
                    active_ms = calendar.timegm(dt.timetuple()) * 1000 + dt.microsecond // 1000
                    # Displayed in local time, because the mtime it replaces was
                    # local — the column would otherwise silently shift by the
                    # UTC offset.
                    last_active = dt.replace(tzinfo=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
            if active_ms is None:
                active_ms = int(round(st.st_mtime * 1000))
                last_active = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")

            sessions.append({
                "id": sid,
                "shortId": sid[:ID_LEN],
                # Deterministic tie-break for equal activeMs. Dashes stripped so
                # the key is pure lowercase hex — PowerShell's culture-aware
                # string sort and Python's ordinal sort agree on that alphabet,
                # but not on where '-' lands.
                "sortKey": sid.replace("-", ""),
                "title": title,
                "preview": preview,
                "cwd": cwd,
                "branch": clean(branch) or "",
                "lastActive": last_active,
                # Sort key: epoch milliseconds of the newest conversation record
                # (or of mtime when there is none). Named for what it is — it is
                # no longer the file's mtime.
                "activeMs": active_ms,
                "sizeKB": round(st.st_size / 1024, 1),
            })

def emit_resume(s):
    """Print the resume command, or say why there isn't one.

    Checking the directory first is what makes the two-line command safe: the
    `cd` can't silently fail and leave `claude` to start a fresh session in the
    wrong place. A recorded cwd goes missing when the project folder is renamed,
    deleted, or lived on a share that isn't mounted.
    """
    if not os.path.isdir(s["cwd"]):
        # ASCII hyphen, not an em-dash: scan-sessions.ps1 can't put non-ASCII in
        # a double-quoted literal (see the encoding trap in SKILL.md), and the
        # two scripts' messages should read identically.
        emit("ERROR: session %s (%s) cannot be resumed - its recorded directory "
             "no longer exists: %s" % (s["shortId"], s["id"], s["cwd"]))
        return
    cmd = resume_command(s)
    emit(cmd)
    if args.copy:
        why = copy_to_clipboard(cmd)
        if why:
            emit("NOTE: could not copy to clipboard (%s)" % why)


# --- resume mode: by id ------------------------------------------------------
# An id is resolved against every session, independent of --query/--offset/
# --limit ranking, so it can never point at the wrong row.
if pick_by_id:
    matches = sorted(sessions, key=lambda s: s["sortKey"])
    if len(matches) == 1:
        emit_resume(matches[0])
    elif not matches:
        emit(f"ERROR: --pick {pick} matched no session")
    else:
        ids = ", ".join(s["shortId"] for s in matches)
        emit(f"ERROR: --pick {pick} is ambiguous ({len(matches)} matches: {ids})")
    sys.exit(0)

# --- time window -------------------------------------------------------------
# Applied after the --pick <id> block above, deliberately: an id lookup is exact
# and shouldn't be second-guessed by a window the caller listed with, exactly as
# it already ignores --min-size-kb. --pick <row> ranks first, so it does see this.
if SINCE_MS is not None:
    sessions = [s for s in sessions if s["activeMs"] >= SINCE_MS]
if BEFORE_MS is not None:
    sessions = [s for s in sessions if s["activeMs"] < BEFORE_MS]

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
    # sortKey ascending breaks activeMs ties identically in both scripts; without
    # it, tied rows order by directory listing and the two drift apart.
    scored.sort(key=lambda x: (-x[0], -x[1]["activeMs"], x[1]["sortKey"]))
    ranked = [s for _, s in scored]
else:
    ranked = sorted(sessions, key=lambda s: (-s["activeMs"], s["sortKey"]))

# --- resume mode: by row -----------------------------------------------------
# Legacy/interactive form. Drifts if another live session's mtime reorders the
# ranking between the listing and the pick — --pick <id> is the safe handle.
if pick_by_row:
    n = int(pick)
    if 1 <= n <= len(ranked):
        emit_resume(ranked[n - 1])
    else:
        emit(f"ERROR: --pick {n} out of range (1-{len(ranked)})")
    sys.exit(0)

# --- list mode ---------------------------------------------------------------
# Tab-separated, display-only columns plus the short id. No full uuids, no full
# paths, no JSON keys. title has whitespace collapsed already, so it can't
# contain a tab/newline.
home = os.environ.get("FIND_SESSION_HOME") or str(Path.home())


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
