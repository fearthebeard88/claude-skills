---
name: find-session
description: Search and resume past Claude Code sessions across ALL project directories, not just the current one. Use when the user wants to find, list, search, or resume a previous session and can't remember which folder it started in — e.g. "find my session about X", "show all my sessions", "resume that conversation from another directory", "which session was I in when…". This is the cross-directory counterpart to the built-in /resume, which only sees the current directory.
---

# Find Session

Built-in `/resume` only lists sessions started in the *current* directory. This
skill searches every session across every directory and helps the user jump
back into one.

## How sessions are stored

`~/.claude/projects/<encoded-cwd>/<session-id>.jsonl` — one folder per working
directory, one `.jsonl` per session. Each session's AI title, real `cwd`, and
last-active time are read out for display (plus the last-prompt preview on
demand).

## Step 1 — Scan (portable dispatch)

There is no single scripting runtime guaranteed on every machine (native Claude
Code installs bundle none). So detect what's available and use it, in this
order — **stop at the first hit:**

| Runtime found on PATH | Command to run |
|---|---|
| `python3` / `python` | `<py> "<skill-dir>/scan-sessions.py" <args>` |
| `pwsh` / `powershell` | `<ps> -NoProfile -ExecutionPolicy Bypass -File "<skill-dir>/scan-sessions.ps1" <args>` |
| none of the above | **agent-native fallback** (Step 1b) |

`<skill-dir>` is printed when the skill loads. **Detect AND verify** each
candidate — a name resolving on PATH is not enough. Probe it and use the first
that actually runs; otherwise fall through:

- Python probe: `<py> -c "print(1)"` must print `1` and exit 0.
- PowerShell probe: `<ps> -NoProfile -Command "exit 0"` must exit 0.

> ⚠️ **Windows footgun:** Windows ships a `python3` (and `python`) *App Execution
> Alias* stub at `…\WindowsApps\` that resolves on PATH but, when run, only
> prints "Python was not found…" and exits non-zero. The probe above is what
> catches it — when `python3` fails the probe, fall through to `python`, then to
> PowerShell (`powershell.exe` is always present on Windows), then to
> agent-native. Never dispatch to a candidate you haven't probed.

Both scripts share ONE contract, so `<args>` is identical either way:

- `--query "<terms>"` — filter + rank (omit for a plain recency listing)
- `--limit <N>` — max rows (default 40). Honor explicit user requests: "show 30"
  → `--limit 30`; "show everything" → a large `--limit` (e.g. 1000).
- `--offset <N>` — pagination: skip the first N ranked rows. Next page after a
  `--limit 15` listing is `--offset 15`, then `--offset 30`, etc.
- `--min-size-kb <N>` — stub threshold (default 3; pass `0` to include empty/
  aborted session stubs when hunting one that isn't showing up)
- `--preview` — append a `preview` (last-prompt) column. Off by default to keep
  output lean; turn it on only when several titles are similar and you need the
  prompt text to tell them apart.
- `--pick <N>` — **resume mode.** Instead of listing, print just the ready-to-run
  command for row `N` (1-based) of the same ranking. See Step 3.

**List output is tab-separated, display-only**, one row per line:
`title⇥dir⇥last-active` (dir is a short label: `~` for home, else the folder
basename; `+⇥preview` with `--preview`). Already filtered/ranked, newest-active
first when no query. No UUIDs or full paths here — those are the token-heavy
fields, and they're only needed to resume, so they're deferred to `--pick`
(Step 3). If the user's request has no obvious search terms, run with no
`--query`.

The **current session is auto-excluded** — the scripts read
`CLAUDE_CODE_SESSION_ID` from the environment and skip it (no point resuming what
you're already in). Harmless no-op when run outside Claude Code.

> **Keep the two scripts in sync.** `scan-sessions.py` and `scan-sessions.ps1`
> are parallel implementations of the same logic — identical args, **byte-
> identical output** (tab-separated, LF line endings, UTF-8). Any change to one
> must be mirrored in the other, or the dispatch results drift. Language traps
> that already bit us: (1) PowerShell variables are case-insensitive, so the
> preview flag is `$ShowPreview`, not `$Preview` — `$Preview` collides with the
> per-row `$preview` value. (2) Both scripts force LF+UTF-8 explicitly (Python
> via `sys.stdout.buffer`, PowerShell via `[Console]::Out.Write` + a no-BOM
> `UTF8Encoding`) because their defaults disagree on newlines (Python text mode
> → CRLF on Windows; PowerShell → CRLF) — a stray `\r` would also corrupt a
> pasted resume command.

### Step 1b — Agent-native fallback (no runtime available)

Do this only when neither Python nor PowerShell exists. It uses Claude Code's
own always-present tools, so it works anywhere:

1. **Glob** `*/*.jsonl` under `~/.claude/projects` (returns newest-first by
   mtime — that's your recency order). Ignore any `agent-*.jsonl` basenames.
2. **Grep** those files (output_mode `content`, with line/file info) for
   `"(aiTitle|lastPrompt|cwd|gitBranch|timestamp)":` in one call.
3. Assemble per file: `title` = last `aiTitle`; `preview` = last `lastPrompt`
   (truncate ~140 chars); `cwd` = first `cwd`; `lastActive` = last `timestamp`.
   Keep `cwd` here — you'll need it for the resume command (no `--pick` call
   exists in this path).
4. **Skip the current session** — drop the file whose id matches
   `$CLAUDE_CODE_SESSION_ID`. Treat a session with **no `aiTitle` and no
   non-empty `lastPrompt`** as an empty stub and drop it too (the tool-only
   stand-in for the `--min-size-kb` filter).
5. Rank/present exactly as the scripts would (same columns as Step 2).

**When you use this fallback, you MUST prepend this notice to your reply** so the
user knows why it's slower and how to speed it up:

> ⚠️ **Ran the agent-native scan** — no `python3` or PowerShell was found on
> your PATH, so I searched with Claude Code's built-in tools. This works
> everywhere but costs more tokens per run and is less consistent than the
> bundled scripts. Installing **Python 3** or **PowerShell** (either one) makes
> this skill faster and fully deterministic — it'll pick them up automatically.

## Step 2 — Present

Render a **terse numbered list**, one line per session — NOT a markdown table
(the table's header, separator, and per-cell pipes are pure output-token
overhead, and output is the uncached cost that hits the usage limit every run):

```
1. <title>  ·  <dir>  ·  <last active>
2. …
```

Number rows by **global rank**: `offset + 1`, `offset + 2`, … (so page 2 with
`--offset 15` starts at `16.`). Those numbers are what the user passes back to
resume, and `--pick` uses the same global numbering — keep them aligned. `dir`
is the script's short label (`~`/basename); offer the full path on request.

If several titles look alike and you can't tell them apart, re-run with
`--preview` and use the prompt text to disambiguate. If nothing matched, say so
and offer to list recent sessions instead. When more rows exist beyond the page,
mention that they can ask for more (`--limit`) or the next page (`--offset`).

## Step 3 — Resume

When the user picks a row number, get the command by re-running the scanner with
`--pick <N>` **and the same `--query`/`--min-size-kb` you listed with** (so the
row numbers line up). `N` is the global row number as displayed, so it works
straight off a paginated page (`--pick 22` for row 22) — `--pick` ignores
`--offset`/`--limit`. It prints exactly:

```
cd "<cwd>" && claude --resume <id>
```

This one call is where the session's `cwd` + `id` are resolved — that's the whole
reason the list stays lean. `&&` chains cleanly in bash, PowerShell 7, and cmd,
and the stored `cwd` already carries the right path style for the platform the
session ran on. (Row order is stable between the list and the `--pick`: the only
file whose mtime shifts mid-session is the current one, which is auto-excluded.)
In the agent-native path there's no `--pick` — build the same line from the `cwd`
you already assembled.

**You cannot resume it for them from inside this session** — launching
`claude --resume` via a tool call spawns a broken nested instance, not a
terminal handoff. The same is true of the `!` prefix: it runs the command
*inside* the current session, so it spawns the same broken nested instance (it
fails with "No deferred tool marker found in the resumed session"). Do NOT
recommend `!` for resuming. Tell the user to run the command in a **separate,
fresh terminal** (or to exit this session first, then run it). Present the
command in a copy-ready code block.

## Notes

- The current session is excluded from the list (via `CLAUDE_CODE_SESSION_ID`) —
  no point resuming what you're already attached to.
- `agent-*.jsonl` files are subagent sidechains and are excluded everywhere;
  only resumable top-level sessions are shown.
- Empty/aborted stubs are hidden by default (scripts: `--min-size-kb 3`;
  agent-native: the no-title-no-prompt rule). Pass `--min-size-kb 0` to reveal
  them.
- Scanning is depth-1 only (`projects/<dir>/<id>.jsonl`); nested `subagents/`
  and `workflows/` transcript artifacts are never treated as sessions.
- Scale is small (tens–hundreds of sessions), so a full scan every invocation is
  fine — no caching needed.
