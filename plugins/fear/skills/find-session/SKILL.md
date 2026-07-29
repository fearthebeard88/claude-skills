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

## Step 1 — Scan (pick the runtime by OS)

You already know the platform you're on, so **choose the runtime by OS — no
runtime probing.** Each platform uses its always-present native shell, so there
is no Git Bash dependency on Windows and no runtime assumption elsewhere. This
also makes each platform's command a single, stable form that can be
allow-listed narrowly (see below), so the skill stops prompting for permission.

- **Windows** → run `scan-sessions.ps1` via the **PowerShell tool**:

  ```
  & "<skill-dir>\scan-sessions.ps1" <args>
  ```

  Windows PowerShell 5.1+ ships with every Windows install, so it's always
  there. We never call `python` on Windows — which also sidesteps the Store
  `python3`/`python` *App Execution Alias* stub (a fake that resolves on PATH
  but fails when run).

- **macOS / Linux** → run `scan-sessions.py` via the **Bash tool**:

  ```
  python3 "<skill-dir>/scan-sessions.py" <args>
  ```

  `python3` is near-universal on Unix. Only if it's genuinely missing, fall
  back — try `python`, then `pwsh -NoProfile -File "<skill-dir>/scan-sessions.ps1"`,
  then the agent-native path (Step 1b).

`<skill-dir>` is printed when the skill loads.

> **Permission allow-list:** the Windows command above is invoked identically
> every run, so a single narrow rule in `settings.json` (scoped to the
> `scan-sessions.ps1` path, not a blanket runtime allow) pre-approves it and
> removes the per-run permission prompt.

Both scripts share ONE contract, so `<args>` is identical either way:

- `--query "<terms>"` — filter + rank (omit for a plain recency listing)
- `--limit <N>` — max rows (**default 15 = one page**). Honor explicit user
  requests: "show 30" → `--limit 30`; "show everything" → a large `--limit`
  (e.g. 1000).
- `--offset <N>` — pagination: skip the first N ranked rows. Since a page is 15,
  the next page is `--offset 15`, then `--offset 30`, etc.
- `--min-size-kb <N>` — stub threshold (default 3; pass `0` to include empty/
  aborted session stubs when hunting one that isn't showing up)
- `--preview` — append a `preview` (last-prompt) column. Off by default to keep
  output lean; turn it on only when several titles are similar and you need the
  prompt text to tell them apart.
- `--pick <id>` — **resume mode.** Instead of listing, print just the ready-to-run
  command for that session. Pass the **short id** from column 1. See Step 3.

**List output is tab-separated, display-only**, one row per line:
`short-id⇥title⇥dir⇥last-active` (dir is a short label: `~` for home, else the
folder basename; `+⇥preview` with `--preview`). Already filtered/ranked,
newest-active first when no query. The short id is the first 8 hex chars of the
session uuid — it's the handle you pass to `--pick`. Full uuids and full paths
stay out of the listing; they're only needed to resume, so they're deferred to
`--pick` (Step 3). If the user's request has no obvious search terms, run with
no `--query`.

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
> pasted resume command. (3) Sorts must break ties on `sortKey` (the uuid with
> dashes stripped). Equal mtimes are common, and without an explicit tie-break
> Python falls back to filename order while PowerShell falls back to directory
> order — the two listings silently diverge. Dashes are stripped because
> PowerShell's culture-aware string sort and Python's ordinal sort disagree on
> where `-` lands; they agree on plain lowercase hex. (4) Inside a PowerShell
> hash literal, `-replace '-', ''` needs parentheses — the comma otherwise ends
> the entry and the parse error cascades to the end of the file.
>
> Because `[Console]::Out.Write` writes to the console handle directly, it
> **bypasses the PowerShell pipeline** — `& .\scan-sessions.ps1 | …` receives
> nothing. To capture output for a diff, redirect a child process's stdout
> (`powershell.exe -NoProfile -File scan-sessions.ps1 … > out.txt`).

### Step 1b — Agent-native fallback (no runtime available)

Do this only when neither Python nor PowerShell exists. It uses Claude Code's
own always-present tools, so it works anywhere:

1. **Glob** `*/*.jsonl` under `~/.claude/projects` (returns newest-first by
   mtime — that's your recency order). Ignore any `agent-*.jsonl` basenames.
2. **Grep** those files (output_mode `content`, with line/file info) for
   `"(aiTitle|lastPrompt|cwd|gitBranch|timestamp|relocatedCwd)":` in one call.
3. Assemble per file: `title` = last `aiTitle`; `preview` = last `lastPrompt`
   (truncate ~140 chars); `lastActive` = last `timestamp`. For `cwd`, use the
   **relocation destination** — the last `relocatedCwd` if the session has any
   (a `/cd` relocates the session), otherwise the **last** `cwd`. Never the
   first `cwd`: a relocated session's origin is a dead end (nothing's there and
   resume would fail). Keep this `cwd` — you need it for the resume command (no
   `--pick` call exists in this path).
4. **If a file yields no `aiTitle` and no `lastPrompt` text, don't write it off
   as untitled** — a `last-prompt` record has two shapes, and the
   `{"type":"last-prompt","leafUuid":…}` form carries no text at all. Re-grep
   just that file for `"type":"user"` and fall back, strongest first: the last
   real user prose (skip records with `"isSidechain":true` or `"isMeta":true`,
   skip `tool_result`-only content, skip `<local-command-*>` / `<command-*>` /
   `<system-reminder>` wrappers), then the last `<command-name>` value (e.g.
   `/fear:find-session`). Only a session with none of those is `(untitled)`.
5. **Skip the current session** — drop the file whose id matches
   `$CLAUDE_CODE_SESSION_ID`. Treat a session with **nothing from step 4
   either** as an empty stub and drop it too (the tool-only stand-in for the
   `--min-size-kb` filter).
6. Rank/present exactly as the scripts would (same columns as Step 2), breaking
   equal-timestamp ties on the session uuid so the order is reproducible.

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
`--offset 15` starts at `16.`). Those numbers are just for the user to point at.
**Don't print the short ids** — they're noise to the user — but **do keep the
row-number → short-id mapping from the scanner output**, because the short id
is what Step 3 resumes by. `dir` is the script's short label (`~`/basename);
offer the full path on request.

A listing is **one page of 15 by default** (the script's `--limit` default) —
don't pass `--limit` unless the user asks for a different count. When more rows
exist beyond the page, say so and mention they can ask for the next page
(`--offset 15`) or a larger count (`--limit`).

If several titles look alike and you can't tell them apart, re-run with
`--preview` and use the prompt text to disambiguate. If nothing matched, say so
and offer to list recent sessions instead.

## Step 3 — Resume

When the user picks a row number, map it back to that row's **short id** and
re-run the scanner with `--pick <short-id>`. It prints exactly:

```
cd "<cwd>" && claude --resume <id>
```

**Always resume by id, never by row number.** Rows are ranked by mtime, and any
*other* Claude Code session the user has open in another terminal writes to its
file continuously — that session climbs to the top mid-conversation and shifts
every row beneath it. A row number captured before that shift then points at the
wrong session, and the wrong resume command comes back with no error to warn
you. An id is a stable handle and can't drift. (`--pick <N>` still accepts a
1–4 digit row number for direct CLI use, and carries exactly that risk.)

An id lookup is resolved against every session independently of the ranking, so
you do **not** need to re-pass the `--query`, `--offset`, `--limit`, or
`--min-size-kb` you listed with. It's also much faster than a listing — only the
matching session's file gets parsed. If a prefix matches more than one session
the scanner says so and lists the candidates rather than guessing.

This one call is where the session's `cwd` + full uuid are resolved — that's the
whole reason the list stays lean. `&&` chains cleanly in bash, PowerShell 7, and
cmd, and the stored `cwd` already carries the right path style for the platform
the session ran on. In the agent-native path there's no `--pick` — build the same
line from the `cwd` you already assembled.

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
  them. `--pick <id>` ignores this filter entirely — an id is exact, so the
  stub threshold can only get in its way.
- **Titles fall back in four tiers**: the AI's `aiTitle`, then an inline
  `lastPrompt`, then the last real user message, then the last slash command
  (`/fear:find-session`). The fallbacks exist because a `last-prompt` record has
  two shapes and the `leafUuid` pointer form carries no text — sessions using it
  have no `aiTitle` either, so without the fallbacks they'd list as
  `(untitled)` with an empty preview *and* be unmatchable by `--query`. Only a
  genuinely contentless stub is `(untitled)` now.
- Scanning is depth-1 only (`projects/<dir>/<id>.jsonl`); nested `subagents/`
  and `workflows/` transcript artifacts are never treated as sessions.
- **Relocated sessions** (you `/cd`'d): a `/cd` moves the session's file into the
  destination directory's folder and records `relocated`/`relocatedCwd`. The
  scanner reports the **destination** (last `relocatedCwd`, else last `cwd`) as
  the session's dir — not the origin it started in — so you see one live,
  resumable entry at "the path it was changed to," not a dead-end pointing at
  the old directory.
- Scale is small (tens–hundreds of sessions), so a full scan every invocation is
  fine — no caching needed.
