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

**Last-active is the newest conversation record, not the file's mtime.** mtime
moves whenever anything rewrites the file without advancing the conversation —
sync clients, backup restores, a `cp -r` of `~/.claude`, or Claude Code appending
records that carry no timestamp of their own. In the reference store mtime ran up
to 168 h ahead of the last real activity and would have mis-ranked 34 of 61 rows.

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

Both scripts accept the same `<args>`, so the invocation is identical either way:

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
- `--deep` — also search **conversation text**, not just metadata. Requires
  `--query`. Without it, a query only sees the title, the last-prompt preview, the
  directory and the branch — so "find my session about X" fails whenever X came up
  mid-conversation and the title doesn't mention it. Costs about +0.5s on a 19 MB
  store (it reads every message), which is why it's opt-in. **A `--deep` search can
  only ever return *more* sessions than the same query without it, never a
  different order for the ones already found** — so retrying with `--deep` is
  always safe. Reach for it when a plain `--query` comes back empty or clearly
  misses, and say you did.
- `--since <when>` / `--before <when>` — restrict to a time window by **last
  activity**. Each takes either a relative age — `12h`, `7d`, `2w` (no month
  unit; `m` would be ambiguous) — or an absolute local date `YYYY-MM-DD`. The
  window is **half-open**: `--since` is inclusive, `--before` is exclusive, so
  `--since 7d` and `--before 7d` partition the sessions exactly. Absolute dates
  anchor at **local** midnight, matching the displayed column. Use these when the
  user gives a time reference ("last week", "back in June", "yesterday")
  instead of, or as well as, `--query`. `--pick <id>` ignores the window (an id
  is exact); `--pick <N>` respects it, because N indexes the filtered ranking.
- `--tail <N>` — **read mode.** With `--pick`, print the last N *exchanges* of
  that session instead of a resume command, so you can answer "what did I decide
  about X?" without the user leaving this session. See Step 4.
- `--copy` — also copy the resume command to the clipboard (`Set-Clipboard` /
  `pbcopy` / `wl-copy` / `xclip` / `clip`). Only meaningful with `--pick`; a
  no-op for a listing. Best-effort: the command is always printed too, and a
  failure adds a `NOTE:` line rather than replacing the command. **Pass it
  whenever you're handing over a resume command** — it turns a retype into a
  paste, which matters because the skill can't resume for the user itself.

**Bad arguments fail loudly and identically on both platforms**: a single
`ERROR: <what was wrong>` line on stdout and exit code 2. That covers unknown
flags (including a wrong-case or abbreviated one), a missing value, a
non-numeric value, and a negative `--limit`/`--offset`/`--min-size-kb`. If you
see an `ERROR:` line, fix the invocation — don't treat it as an empty result.

**List output is tab-separated, display-only**, one row per line:
`short-id⇥title⇥dir⇥last-active` (`+⇥preview` with `--preview`). `dir` is a short
label — `~` for home, else the folder basename — with `@branch` appended when the
session was on a named git branch, e.g. `claude-skills@master`. Most rows have no
suffix: only about 1 in 9 sessions is in a repo, and the placeholder git records
when there's no named branch (`HEAD`) is suppressed rather than printed. Already
filtered/ranked,
newest-active first when no query. The short id is the first 8 hex chars of the
session uuid — it's the handle you pass to `--pick`. Full uuids and full paths
stay out of the listing; they're only needed to resume, so they're deferred to
`--pick` (Step 3). If the user's request has no obvious search terms, run with
no `--query`.

The **current session is auto-excluded** — the scripts read
`CLAUDE_CODE_SESSION_ID` from the environment and skip it (no point resuming what
you're already in). Harmless no-op when run outside Claude Code.

> **Keep the two scripts in sync.** `scan-sessions.py` and `scan-sessions.ps1`
> are parallel implementations of the same logic. The contract is **identical
> args, and the same rows in the same order** — plus exact agreement on anything
> the user acts on (the short id, and the `cwd` + uuid in a resume command).
> Output *formatting* may differ (line endings, encoding details, the trailing
> newline), as may error-message wording. Any behavioural change to one must be
> mirrored in the other, or the dispatch results drift.
>
> Note the cascade: `title`, `preview`, `cwd` and `branch` are the search
> haystack and `title` carries a 3× score weight, so if any of them differs
> between the scripts the *ranking* differs — which the contract forbids. "Same
> order" therefore pins those four fields to exact agreement anyway. Only the
> `dir` label and the `lastActive` format are genuinely free.
>
> **The search algorithm is specified in full** in a `SEARCH SPEC` comment block
> at the top of `scan-sessions.py`, with a summary in `scan-sessions.ps1`. That
> spec is the contract: tokenisation, case folding, match semantics, the three
> scoring tiers and their weights, what counts as deep text, and the guarantee
> that `--deep` never reorders what a shallow search found. Change it in both
> scripts and in the spec, or not at all.
>
> **Run `python3 test-parity.py` after touching either script.** It builds a
> fixture store, runs both against it via the env vars below, and diffs the rows.
> It also covers the branches a real store can't reproduce on demand: the `~`
> label, a relocation, an activity-time tie, out-of-order and nested timestamps,
> and an unreadable file.
>
> Language traps that already bit us: (1) PowerShell variables are
> case-insensitive, so the preview flag is `$ShowPreview`, not `$Preview` —
> `$Preview` collides with the per-row `$preview` value. (2) Both scripts force
> LF+UTF-8 explicitly (Python via `sys.stdout.buffer`, PowerShell via
> `[Console]::Out.Write` + a no-BOM `UTF8Encoding`) because their defaults
> disagree on newlines (Python text mode → CRLF on Windows; PowerShell → CRLF).
> Formatting is outside the contract now, but a stray `\r` still corrupts a
> pasted resume command, so keep it. (3) Sorts must break ties on `sortKey` (the
> uuid with dashes stripped). Equal activity times happen, and without an
> explicit tie-break Python falls back to filename order while PowerShell falls
> back to directory order — the two listings silently diverge. Dashes are stripped
> because PowerShell's culture-aware string sort and Python's ordinal sort
> disagree on where `-` lands; they agree on plain lowercase hex. (4) Inside a
> PowerShell hash literal, `-replace '-', ''` needs parentheses — the comma
> otherwise ends the entry and the parse error cascades to the end of the file.
> (5) `$ErrorActionPreference = "Stop"` makes any .NET exception terminate the
> whole script, so every file read and directory listing needs a `try`/`catch`
> that skips and continues — Python's `except OSError: continue` equivalent.
> Without it one locked or permission-denied session file produced **zero rows
> and a stack trace** instead of a listing. (6) argparse accepts unambiguous
> prefixes by default, so `--lim 3` worked in Python and was an error in
> PowerShell; the Python parser sets `allow_abbrev=False`. Argument matching is
> case-sensitive on both sides (`-ceq`/`-ccontains` in PowerShell). (7) Parse
> numbers with `InvariantCulture` in PowerShell — `[double]::TryParse` otherwise
> follows the current locale, so `--min-size-kb 3.5` would be rejected in a
> comma-decimal locale while Python always accepts a dot. **Case-fold with
> `ToLowerInvariant()`, never `ToLower()`** — .NET's `ToLower()` follows the
> current culture, so under `tr-TR`/`az-AZ` `"INVOICE".ToLower()` is `ınvoıce`
> with a dotless i and `--query invoice` matched in Python while returning nothing
> in PowerShell. Same reason the home-dir comparison uses an explicit
> `OrdinalIgnoreCase` rather than `-eq`. (`String.Contains` is already ordinal, so
> it's safe.) (8) **Count and cut text by CODE POINTS, not `.Length`.** .NET
> counts UTF-16 code units, so an emoji is 2 there and 1 to Python — a
> `.Length`-based truncate cut a 50-emoji title to 40 emoji where Python kept 50,
> producing a different title, a different search score, and potentially a
> different *order*. `Substring` will also split a surrogate pair in half. 7 files
> in the reference store contain non-BMP characters. (9) **`scan-sessions.ps1`
> is saved as UTF-8 *with* a BOM, on purpose.** Windows PowerShell 5.1 reads a
> BOM-less `.ps1` as ANSI, so a UTF-8 em-dash becomes `â€”` — and that trailing
> `”` is U+201D RIGHT DOUBLE QUOTATION MARK, which PowerShell honours as a string
> delimiter. One em-dash inside a double-quoted literal terminated the string
> early and cascaded parse errors through the entire file. Don't strip the BOM,
> and keep double-quoted literals ASCII anyway. (Em-dashes in *comments* are
> harmless, which is why this went unnoticed for so long.) Message text is
> deliberately identical in both scripts, so the Python side avoids non-ASCII in
> those strings too.
>
> Because `[Console]::Out.Write` writes to the console handle directly, it
> **bypasses the PowerShell pipeline** — `& .\scan-sessions.ps1 | …` receives
> nothing. To capture output for a diff, redirect a child process's stdout
> (`powershell.exe -NoProfile -File scan-sessions.ps1 … > out.txt`).
>
> **Test hooks** (all default to the real values, so they're no-ops in normal
> use): `FIND_SESSION_ROOT` overrides the scan root, `FIND_SESSION_HOME`
> overrides the path treated as home for the `~` label, and `FIND_SESSION_NOW`
> overrides "now" in epoch ms so a relative `--since`/`--before` resolves to a
> fixed instant. The first two exist because
> PowerShell's `$HOME` is **read-only** — without them there is no way to aim
> `scan-sessions.ps1` at anything but the real store, and so no way to test the
> two implementations against identical input.

### Step 1b — Agent-native fallback (no runtime available)

Do this only when neither Python nor PowerShell exists. It uses Claude Code's
own always-present tools, so it works anywhere:

1. **Glob** `*/*.jsonl` under `~/.claude/projects`. It returns newest-first by
   **mtime**, which is only a rough starting order — mtime is not the recency
   order (see step 3); re-sort once you have the timestamps. Ignore any
   `agent-*.jsonl` basenames.
2. **Grep** those files (output_mode `content`, with line/file info) for
   `"(aiTitle|lastPrompt|cwd|gitBranch|timestamp|relocatedCwd)":` in one call.
3. Assemble per file: `title` = last `aiTitle`; `preview` = last `lastPrompt`
   (truncate ~140 chars); `lastActive` = the **greatest** `timestamp`, converted
   to local time. Not the last one in the file: most sessions carry them
   out of order, and in some the final record isn't the newest. Only count a
   record's **own** top-level `timestamp` — some records (e.g.
   `file-history-snapshot`) have no timestamp of their own but hold a nested one
   that is not session activity. Timestamps are UTC (`…Z`); fall back to the
   file's mtime only when a session has none at all. For `cwd`, use the
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
is what Step 3 resumes by. Pass `dir` through as the scanner gives it, including
any `@branch` suffix — that suffix is often the only thing distinguishing two
sessions in the same repo. Offer the full path on request.

A listing is **one page of 15 by default** (the script's `--limit` default) —
don't pass `--limit` unless the user asks for a different count. When more rows
exist beyond the page, say so and mention they can ask for the next page
(`--offset 15`) or a larger count (`--limit`).

If several titles look alike and you can't tell them apart, re-run with
`--preview` and use the prompt text to disambiguate. **If two rows are identical
in every column *and* `--preview` doesn't separate them either, they are duplicate
sessions holding the same conversation** — usually an accidental double-start. Say
that plainly instead of offering them as a meaningful choice: "rows 12 and 13 are
duplicates of the same conversation, so either will do." Don't invent a
distinction, and don't silently drop one — two sessions that merely *share a title*
are very often different conversations days apart, which the dir and time columns
already tell apart. **If a query matched nothing
or looks like it missed, retry with `--deep` before concluding the session isn't
there** — a plain query only sees titles and metadata, and the thing the user
remembers was usually said mid-conversation. Only after `--deep` also comes back
empty should you say there's no match, and offer to list recent sessions instead.
If you passed `--since`/`--before`, say which window you used: an empty result is
far more often a too-narrow window than a genuinely absent session.

## Step 3 — Resume

When the user picks a row number, map it back to that row's **short id** and
re-run the scanner with `--pick <short-id> --copy`. It prints exactly two lines:

```
cd "<cwd>"
claude --resume <id>
```

**Two lines, not `cd … && claude …`, and don't "helpfully" rejoin them.** A
one-liner needs a shell separator and none works everywhere: `&&` is a parse
error in Windows PowerShell 5.1 (verified: *"The token '&&' is not a valid
statement separator in this version"*) — the very shell this skill targets
because it's always present — and `;` isn't a separator in cmd, where it becomes
a literal argument to `cd`. Two lines run sequentially in bash, PowerShell 5.1,
PowerShell 7 and cmd alike. Present both lines in one code block.

(One residual gap: in cmd, `cd` alone doesn't change *drive*, so a cmd user
resuming a session on another drive needs `cd /d`. That can't be expressed
portably — `/d` is a bad argument to PowerShell's `Set-Location` alias — and
PowerShell, where `cd` switches drive by itself, is the realistic paste target
on Windows. Mention `cd /d` only if the user says they're in cmd.)

If the session's recorded directory no longer exists, `--pick` refuses to hand
over a command that can't work and says so instead:

```
ERROR: session <short-id> (<id>) cannot be resumed - its recorded directory no longer exists: <cwd>
```

Relay that plainly. The session's *content* is still intact on disk, so if the
user wants what's in it rather than to continue it, offer to recreate the
directory — don't present a resume command that would `cd` into nothing.

**Always resume by id, never by row number.** Rows are ranked by last activity,
and any *other* Claude Code session the user has open in another terminal keeps
recording activity — that session climbs to the top mid-conversation and shifts
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
whole reason the list stays lean. The stored `cwd` already carries the right path
style for the platform the session ran on. In the agent-native path there's no
`--pick`, so build the same two lines from the `cwd` you already assembled — and
check that directory exists first, since nothing else will.

**Before handing over a resume command, consider whether they wanted Step 4
instead** — if the question is about what happened in that session rather than
about continuing it, `--tail` answers it here with no terminal switch.

**You cannot resume it for them from inside this session** — launching
`claude --resume` via a tool call spawns a broken nested instance, not a
terminal handoff. The same is true of the `!` prefix: it runs the command
*inside* the current session, so it spawns the same broken nested instance (it
fails with "No deferred tool marker found in the resumed session"). Do NOT
recommend `!` for resuming. Tell the user to run the command in a **separate,
fresh terminal** (or to exit this session first, then run it). Present the
command in a copy-ready code block.

## Step 4 — Read it here instead (often what they actually wanted)

**"Resume" is frequently not the goal.** A large share of the real need is *"what
did I decide about X in that session?"* — and that doesn't require a terminal
switch at all. `--pick <id> --tail <N>` prints the last N exchanges right here:

```
& "<skill-dir>\scan-sessions.ps1" --pick e5faf172 --tail 4
```

Output is one message per line, `role⇥text`, oldest first:

```
user⇥what should we do about the retry logic
assistant⇥Use exponential backoff capped at 30s, and don't retry 4xx.
```

**When the user asks a question about a past session rather than asking to
continue it, reach for this first** and only offer the resume command if they
need to actually keep working there. It's also the *only* option when the
session's directory is gone — `--tail` reads fine where `--pick` alone refuses.

Details worth knowing:

- `N` counts **exchanges**, not messages: one thing the user asked plus the final
  reply to it. So `--tail 3` is "the last 3 things I asked". Each exchange prints
  at most two lines, so output stays predictable.
- Only the **last** reply in an exchange is shown. Claude's earlier messages in a
  turn are mostly tool-call narration; the last one carries the conclusion. If the
  answer looks truncated mid-reasoning, ask for more exchanges, not more depth —
  depth isn't available.
- Whitespace is collapsed, so **code blocks lose their line breaks**. This
  recovers decisions and prose, not files. If the user needs code back verbatim,
  they'll have to resume.
- Long messages are cut at ~1200 characters with an ellipsis.
- Injected plumbing (`[Request interrupted by user…]`, system reminders, slash
  command wrappers) and tool results are excluded — this shows what was actually
  said.
- A session with no user turn at all reports `no readable exchanges`.

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
- **The branch is the one the session ended on**, matching `cwd` (which is also
  the last one seen). A session that started on `master` and moved to a feature
  branch reports the feature branch. It's searchable via `--query`, but `HEAD` is
  normalised away first, so querying `head` won't match every non-repo session.
- **Relocated sessions** (you `/cd`'d): a `/cd` moves the session's file into the
  destination directory's folder and records `relocated`/`relocatedCwd`. The
  scanner reports the **destination** (last `relocatedCwd`, else last `cwd`) as
  the session's dir — not the origin it started in — so you see one live,
  resumable entry at "the path it was changed to," not a dead-end pointing at
  the old directory.
- Scale is small (tens–hundreds of sessions), so a full scan every invocation is
  fine — no caching needed.
