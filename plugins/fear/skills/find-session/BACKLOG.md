# find-session — backlog

Open bugs and enhancement candidates for the `find-session` skill. Nothing here
is committed to; it's a review queue.

Findings came out of a read of `SKILL.md`, `scan-sessions.py`, and
`scan-sessions.ps1` against a real session store (71 non-agent `.jsonl` files,
25 MB, 8 project directories, ~60 non-stub sessions). Measurements below are
from that store on Windows (Python 3, PowerShell 7.6.4), and are recorded so
they can be re-checked rather than re-guessed.

**Item ids are stable handles, not a priority order.** Several items
cross-reference each other (E3→B2, E5→B1, P1→E1), so ids never get renumbered.
Read "Sequencing" at the bottom for the order actually worth working in.

---

## Decision — the parity contract is relaxed (2026-07-30)

**Decided:** the two scripts no longer have to produce **byte-identical** output.
The contract is now **same rows, same order**.

Restated precisely, because the old contract's value was that it was
unambiguous:

- **Must still agree exactly** — which sessions are selected (all filters), and
  the order they're ranked in. Plus anything the user *acts* on: the short id,
  and the `cwd` + full uuid in the resume command. A wrong `cwd` sends someone to
  a dead directory; a divergent id breaks `--pick`.
- **Now free to differ** — line endings, encoding and BOM handling, the trailing
  newline, and display-only rendering that nothing else depends on: the `dir`
  label and the `lastActive` timestamp format.

**Note the cascade, because it makes this narrower than it looks.** `title`,
`preview`, `cwd`, and `branch` are the search haystack, and `title` carries a 3×
score weight. If any of them differs between the scripts, scores differ, so the
*order* differs — which the new contract still forbids. So "same order"
transitively pins all four fields to exact agreement anyway. What the relaxation
genuinely frees is the output plumbing (`sys.stdout.buffer` / `[Console]::Out`
lock-step) and the two display-only columns.

Consequences already reflected below: P1 can take a PowerShell-only regex fast
path without mirroring it in Python, so long as the values it extracts are
identical. The `dirlabel` root-case divergence drops from parity break to
cosmetic. **E1 is the item this was relaxed for, and it's the one it helps
least** — see the note under E1.

Follow-up: `SKILL.md` states the byte-identical contract in several places
(the sync callout, the Step 1 output description, the `--pick` notes). It has to
be rewritten to the new contract, or the docs and the code disagree.

**Done — sequencing steps 1, 2 and 3 (2026-07-30):** B6, P0, numeric-arg
validation, B3, B4, E4, B1 and E5, all covered by `test-parity.py` and
mutation-tested. `SKILL.md` is rewritten to the relaxed contract. Details in
"Already fixed" below; the items are struck from the lists. Six extra divergences
surfaced while testing and were fixed in the same passes — see the entries under
"Already fixed". Four of them would not have been found by reading the code, and
one (nested timestamps) was found only by diffing against the **real** store
after the fixture had already gone green.

**Already fixed, not in this backlog:**

- **B6** — `$ErrorActionPreference = "Stop"` plus an unguarded `ReadLines` meant
  one unreadable session file aborted the whole PowerShell scan and printed zero
  rows. Both scripts now skip unreadable files and unlistable directories.
  Verified by mutation: reinstating the rethrow drops the listing from 6 rows to
  0, so the test genuinely covers it.
- **P0** — `FIND_SESSION_ROOT` / `FIND_SESSION_HOME` let both scripts run against
  a fixture store, working around PowerShell's read-only `$HOME`. `test-parity.py`
  uses them to diff 23 invocations across `python`, `pwsh` and `powershell` 5.1,
  and to cover the `~` label, relocation, mtime ties and unreadable files.
- **Numeric-arg validation** — negative `--offset` silently returned the *last*
  rows in Python (slice wrap) and threw in PowerShell; unknown flags errored in
  Python and were ignored in PowerShell. Both now emit one `ERROR:` line and
  exit 2 for unknown/abbreviated/wrong-case flags, missing values, non-numeric
  values, and negatives.
- Regression-checked against the real 25 MB store: output is byte-identical to
  the previous version for six invocation shapes on both runtimes.
- **B4** — the resume command is now **two lines** (`cd "…"` then
  `claude --resume …`) instead of `cd "…" && claude …`. Verified across all four
  shells: `&&` is a parse error in Windows PowerShell 5.1 (*"The token '&&' is not
  a valid statement separator in this version"*) and works in bash/PS7/cmd; `;`
  works in bash/PS5.1/PS7 and breaks in cmd; two lines work in all four, with the
  cwd verifiably changed. Residual gap, documented rather than solved: cmd needs
  `cd /d` to change drive, which can't be expressed portably.
- **B3** — `--pick` now `Test-Path`/`isdir`-checks the recorded cwd and refuses to
  emit a command that would `cd` into nothing, reporting
  `ERROR: session <id> cannot be resumed - its recorded directory no longer
  exists: <cwd>`. The session still appears in listings; only the resume command
  is withheld. This is also what makes B4's separator-less form safe — there's no
  short-circuit any more, so the failure it guarded against is pre-empted instead.
- **E4** — `--copy` copies the resume command to the clipboard
  (`Set-Clipboard` / `clip` / `pbcopy` / `wl-copy` / `xclip`). Best-effort and
  never fatal: the command is always printed, and a failure adds a `NOTE:` line.
  `SKILL.md` tells the agent to pass it whenever handing over a resume command.
- **argparse prefix abbreviation** — `--lim 3` was accepted by Python and rejected
  by PowerShell. `allow_abbrev=False`. Found by the parity test, not by reading.
- **Locale-dependent number parsing** — PowerShell's `[double]::TryParse` follows
  the current culture, so `--min-size-kb 3.5` would be rejected in a
  comma-decimal locale while Python always accepts a dot. Now `InvariantCulture`.
- **PowerShell 5.1 source-encoding trap** — `scan-sessions.ps1` is now saved as
  UTF-8 **with a BOM**. Without one, 5.1 reads the file as ANSI, so a UTF-8
  em-dash becomes `â€”`, whose trailing `”` is U+201D RIGHT DOUBLE QUOTATION MARK
  — which PowerShell honours as a string delimiter. A single em-dash in a
  double-quoted literal terminated the string early and cascaded parse errors
  through the whole file, breaking the script completely on the primary Windows
  runtime. Latent for as long as em-dashes stayed in comments (harmless there);
  it fired the moment one landed in a message string. Double-quoted literals are
  kept ASCII as a second line of defence, and both scripts' user-facing messages
  are ASCII so they read identically.
- **Clipboard line endings** — `Set-Clipboard` stored LF while `clip.exe`
  converted to CRLF, so the two scripts put different bytes on the clipboard.
  Both now normalise to CRLF on Windows for the clipboard only; stdout stays LF.
  Verified byte-identical between the two runtimes.
- **B1** — `lastActive` and the ranking now come from the **greatest** record
  `timestamp` (UTC, rendered in local time), with file mtime kept only as the
  fallback for a session that has none. Effect on the reference store: **34 of 61
  rows changed rank**, and displayed times moved by up to 168 h. Several sessions
  had been claiming "active today" while their last real activity was days
  earlier. Three sub-findings, none of which were in B1's original write-up:
  - **The maximum, not the last.** 57 of 73 sessions carry out-of-order
    timestamps and in 8 of them the final record is not the newest, so B1's
    "capture the last `timestamp`" — and `SKILL.md`'s agent-native instruction to
    the same effect — would have under-reported those sessions. Both are fixed.
  - **Only the record's own top-level `timestamp` counts.** Some records
    (`file-history-snapshot`) carry no timestamp of their own but hold a *nested*
    one that isn't session activity. An implementation that regex-scanned the raw
    line picked those up and reported one real session as 12:30 instead of 12:29.
    Caught by diffing against the real store, not by the fixture — a fixture case
    has since been added and mutation-verified.
  - **`ConvertFrom-Json` is host-dependent.** Windows PowerShell 5.1 leaves an
    ISO-8601 string as `[string]`; PowerShell 7 coerces it to `[datetime]` and
    discards the original text. A plain `-is [string]` test passed on 5.1 and
    failed on 7, silently sending *every* session to the mtime fallback on 7 only.
    Where 7 coerces, the canonical text is re-derived and checked against the
    line, so a non-canonical source form (e.g. a `+00:00` offset) is rejected
    identically on all three runtimes. Pinned by a fixture case.
- **Culture-sensitive time formatting** — `.ToString("yyyy-MM-dd HH:mm")` uses the
  culture's *time separator* for `:`, so a locale that uses `.` would render
  `09.57` where Python's `strftime` always renders `09:57`. Now formatted with
  `InvariantCulture`. This was latent in the pre-existing mtime formatting too.
- **Culture-sensitive string comparison** — timestamp maxima are compared with
  `[string]::CompareOrdinal`, because PowerShell's `-gt` is culture-aware while
  Python's `>` is ordinal. Same family as the existing `sortKey` dash-stripping
  trap.
- **E5** — `--since` / `--before`, each taking a relative age (`12h`, `7d`, `2w`)
  or an absolute local date (`YYYY-MM-DD`). Decisions worth knowing:
  - **Half-open window**: `--since` inclusive, `--before` exclusive. So the two at
    the same bound partition the set exactly — verified on the real store, where
    `--since 7d` (17 rows) + `--before 7d` (44) = 61 = the whole store. That
    invariant is now a test assertion, because it catches an inclusivity flip even
    when no fixture row sits exactly on the boundary.
  - **No month unit.** `m` is ambiguous between minutes and months, and a calendar
    month isn't a fixed number of milliseconds. `h`/`d`/`w` only.
  - **Absolute dates anchor at LOCAL midnight**, matching the `lastActive` column,
    which B1 also renders in local time. "Since 2026-07-01" means the user's own
    July 1st. Both scripts get there differently but identically — Python via a
    naive `datetime.timestamp()`, PowerShell via a `Kind=Unspecified` `ParseExact`
    cast to `[datetimeoffset]`.
  - **One "now" per run**, captured at startup, so two relative bounds can't be
    anchored milliseconds apart. `FIND_SESSION_NOW` overrides it, which is what
    makes relative-window tests deterministic rather than time-of-day dependent.
  - `--pick <id>` ignores the window (an id is exact — same rule as
    `--min-size-kb`); `--pick <N>` respects it, since N indexes the filtered
    ranking.

- `--pick` row-number drift — sessions now resume by stable short id.
- `(untitled)` and unsearchable sessions caused by the `leafUuid`-only
  `last-prompt` record shape — now covered by a four-tier title fallback.
- Equal-mtime rows ordered by filename in Python and by directory order in
  PowerShell, so the two listings diverged. Both now tie-break on the uuid.
- `dirlabel()` compared the home path case-sensitively in Python and
  case-insensitively in PowerShell, so the same session could label `~` on one
  and `Users` on the other. Both are case-insensitive now. (This store really
  does contain both `C--Users-tim-ferris` and `c--Users-tim-ferris`.)
- PowerShell built `$sessions` with `+=` in a loop (O(n²) copying); now an
  `ArrayList`.

---

## Bugs

Listed most severe first. ~~B6~~, ~~B4~~, ~~B3~~ and ~~B1~~ are fixed (see
"Already fixed" above). B2 is now the only substantive open bug.

### B2 — `gitBranch` is taken first-wins while `cwd` is taken last-wins

`scan-sessions.py:186` / `scan-sessions.ps1:159` keep the *first* branch seen;
`cwd` deliberately keeps the *last*. A session that started on `master` and
moved to a feature branch reports `master`. The field is currently only used for
search scoring, so the blast radius is small — but it becomes visible the moment
E3 (show the branch) lands.

Precisely, the guard is `if not branch` / `if (-not $branch)`, so it's
**first non-empty wins**: a session that starts outside a git repo (empty
branch) keeps looking and picks up the first real branch it sees later. Whatever
semantics you choose, that asymmetry with `cwd` should be deliberate rather than
incidental.

Cost: trivial, but decide the semantics deliberately: last-wins matches `cwd`,
first-wins answers "where did this work start".

### B5 — title truncation is inconsistent across fallback tiers

Of the four title tiers, tier 1 (`aiTitle`) and tier 2 (`lastPrompt`) are
**both** untruncated; only tiers 3 and 4 truncate, at 80 chars
(`scan-sessions.py:198`, `scan-sessions.ps1:174`). So the rule is arbitrary in
two directions, not one. One session in this store lists with a 93-character
title. Cosmetic and pre-existing, but there's no reason a title's length limit
should depend on which tier produced it.

---

## Testing

**~~P0~~ is done** — see "Already fixed" above. `test-parity.py` is the harness
every remaining item should extend:

- Add a fixture session for any new record shape or field an item introduces
  (B1 wants a `timestamp` record; B2 wants a branch that changes mid-session).
- Add invocations to `CASES` for any new flag (E5's `--since`, E2's `--tail`).
- Add a fixture-specific assertion for anything a two-runtime diff can't catch —
  both scripts agreeing on the *wrong* answer still passes the diff. That's what
  the `checks` list at the bottom of `main()` is for.
- Mutation-test anything load-bearing: break the fix on purpose, confirm the test
  goes red, put it back. A green test that was never seen red proves nothing.
- **Also diff against the real store, not just the fixture.** The nested-timestamp
  bug in B1 went green on the fixture and was caught only by diffing the three
  runtimes over `~/.claude/projects`. A fixture encodes what you thought to model;
  the real store contains what you didn't. The loop that found it:
  `python scan-sessions.py <args> > a; powershell -NoProfile -File
  scan-sessions.ps1 <args> > b; diff a b` over several `<args>` shapes.
- The suite takes ~1 minute (every case × every runtime, and a PowerShell start
  plus scan is ~2.5 s). Don't run it twice in one shell command — that exceeds a
  120 s tool timeout.

---

## Performance

### P1 — PowerShell's per-line `ConvertFrom-Json` is the whole problem; Python is already fast

**Corrected.** An earlier version of this entry quoted a single cost of 1.72 s
and framed a tail read as the primary fix, with regex extraction as a
PowerShell-side "secondary win". Re-measured, that's backwards — 1.72 s was a
PowerShell-only number. Three runs each on this store:

| runtime               | full listing (`--limit 1000`) | `--pick <id>` |
|-----------------------|-------------------------------|---------------|
| Python 3              | **0.27 s**                    | 0.17 s        |
| Windows PowerShell 5.1| **2.37 s**                    | —             |
| PowerShell 7.6.4      | **3.01 s**                    | 0.39–0.45 s   |

Re-measured 2026-07-30 with every runtime spawned as a child process, so the
three numbers are directly comparable (the earlier 1.5–2.4 s figure for pwsh was
measured in-process and understated it). **Windows PowerShell 5.1 is the faster
of the two PowerShell hosts here — 2.37 s vs 3.01 s** — which is worth knowing
before optimising, since 5.1 is also the always-present one this skill targets.

B1 added no measurable cost: 2.37 s → 2.37 s on 5.1, 3.01 → 3.16 s on 7 (noise),
0.27 → 0.31 s on Python. Reading the timestamp is a field access on a record
that was already parsed.

Python needs no optimization at all: 0.27 s over 25 MB, and it isn't even
streaming — `scan-sessions.py:145` slurps the whole file into a string and then
`splitlines()` it (peak memory ~2× file size on the 4.7 MB session). PowerShell
already streams via `ReadLines` and is still ~7× slower, so the cost is
essentially all `ConvertFrom-Json` per line. Sharpest illustration: PowerShell
spends 0.39 s parsing the **one** 4.7 MB file under `--pick`, which Python does
in 0.17 s *including interpreter startup*.

Implication for what to build: **extract the handful of fields by regex instead
of decoding whole records, on the PowerShell side.** That's the high-value,
low-risk change. A tail read is a much larger change that would complicate
Python for zero measurable gain.

The relaxed contract (see Decision above) licenses this cleanly: the regex path
can be PowerShell-only and need not be mirrored in Python, **provided the values
it extracts are identical to what the JSON decode produced**. That proviso is the
whole risk, and it's not trivial — a regex over raw JSON sees escaped text
(`\n`, `\"`, `\uXXXX`) that `ConvertFrom-Json` would have decoded, so anything
extracted by regex needs unescaping before it lands in `title`/`preview`, which
feed scoring and therefore order. Verify with P0's fixture, diffing the ranking
rather than eyeballing the top rows.

**If a tail read is still pursued, two caveats — the second is the dangerous
one:**

1. A `relocated` record can sit outside a small tail window. Confirmed real, not
   hypothetical: `aa2504d4`'s last `relocated` record is **18,041 bytes** from
   EOF (others in this store: 1708, 332, 313). Either substring-scan the whole
   file for that one marker without parsing it (cheap — no JSON decode), or rely
   on the trailing `cwd`, which already reflects the destination.

2. **`aiTitle` can sit far from EOF, and losing it degrades silently.** The
   earlier version of this entry tabulated distances from one file only and
   understated the spread ~20×. Byte offset of the last occurrence, six largest
   sessions:

   | session  | `aiTitle` | `lastPrompt` | `cwd` |
   |----------|-----------|--------------|-------|
   | ffcb9133 |       446 |          770 |   946 |
   | 82cc949c |       189 |          334 |   511 |
   | addbecda | **18426** |        18763 |   182 |
   | 9d3d6ee6 | **29974** |          158 |   512 |
   | 67354236 | **18872** |          320 |   464 |
   | 9d42fca1 | **20101** |          168 |   312 |

   A 64 KB window still covers this store, but the margin is ~2×, not the 40×
   the old table implied. And the failure mode is silent: a session titled before
   a large tool-output burst falls off the window and quietly drops to the
   `lastPrompt` tier — a worse title with no error. Unlike the `relocated` case
   there's no backstop. Any window has to be justified against the observed
   spread, not against the best-case file.

Scaling still matters for PowerShell: linear in bytes, so a year of history at
this rate turns ~1.9 s into ~20 s. For Python it turns 0.27 s into ~3 s.

Note this interacts with E1 (content search) — the fast path wants to read as
little as possible, the deep path has to read everything. They should be
designed together.

---

## Enhancements, roughly by value

### E1 — search conversation content, not just metadata

**The biggest functional gap.** `--query` scores against `title + preview + cwd
+ branch` only, where `preview` is the *last* prompt. But "find my session about
X" usually means X came up mid-conversation, and the last prompt is very often
"yes do that" or "commit it". The skill's own description promises to find a
session by what it was about; today it can only find one by what it was titled.

Suggested shape: a `--deep` flag that also scores message text, so the cheap path
stays cheap and the expensive path is opt-in. Pairs with P1 — cheap default
listing, full read only under `--deep`.

**Parity is still the hard part, and relaxing the contract barely helps here.**
The contract was relaxed to "same rows, same order" partly for this item (see
Decision above) — but scoring divergence changes *which sessions match* and
*what they score*, which changes the row set and the order. Those are exactly
what the new contract still requires to be identical. The relaxation frees output
plumbing, not scoring.

So E1 still needs both scripts to tokenize, match, and score the same way. The
surface: which record types and content blocks count as searchable text, how much
of each message is scored (any truncation must match), dedup of repeated text,
and the per-term weighting. Locale is the smaller risk — Python's `str.lower()`
and .NET's `ToLower()` agree on ASCII, and .NET's `String.Contains(string)` is
ordinal — so non-ASCII content is where they'd drift.

Two honest ways forward, worth picking deliberately before writing code:

1. **Hold the line.** Specify the scoring algorithm precisely enough to
   reimplement twice, and use P0's fixture to diff the two rankings on every
   change. More work, contract stays meaningful.
2. **Relax further, explicitly.** Accept that `--deep` rankings may differ
   between platforms, and document `--deep` as best-effort ranking rather than a
   stable one. Cheap, but the skill then gives platform-dependent answers to
   "find my session about X" — the skill's headline use case.

Do P0 first either way; without a fixture there's no way to tell which one you
actually shipped.

### E2 — read a past session without leaving the current one

The skill's sharpest limitation is that it *can't* resume for the user — it hands
over a command and says "open a fresh terminal" (launching `claude --resume` from
a tool call spawns a broken nested instance, and the `!` prefix has the same
problem).

But a large share of the actual need isn't "resume", it's "what did I decide
about X in that session?" A `--pick <id> --tail <N>` that prints the last N
exchanges would answer that in place, with no terminal switch at all. Arguably
more valuable than the resume path, cheaper than E1, and small given the file is
already being read — it also dissolves most of the "can't resume for you"
framing. Worth doing before E1.

### E3 — make the columns carry more signal

In this store, **9 of the top 15 rows show `~`** as their directory — the column
tells you nothing for most sessions, because most sessions start in the home
directory. Meanwhile `branch` is collected, fed into search scoring, and then
discarded before output.

- Show the git branch (see B2 first — the value is currently first-non-empty).
- Consider relative times ("2h ago", "3d ago") instead of absolute timestamps.
  For a recency-ordered list that's easier to scan at the same token cost.

### E6 — group or flag near-duplicate sessions

This store has several sessions sharing a title (`Build markdown browser
extension for Chrome` ×2, plus five near-identical `Acknowledge…` /
`Confirm session acknowledgment` variants). They're indistinguishable in a
listing without `--preview`. Could collapse them, or auto-enable the preview
column when titles collide within a page.

Lower confidence than the rest — worth checking whether this is annoying in
practice or just cosmetic.

---

## Smaller notes

- **Untested: is the `cd` needed at all?** The resume command exists as two lines
  only because `claude --resume <id>` is assumed to require the session's own
  directory. `claude --help` confirms there's no `--cwd`-style flag, but whether
  `--resume <full-uuid>` resolves globally was **not** verified — the only way to
  check is to actually resume a session, which appends a turn to real user data.
  If it does resolve globally, the whole command collapses to
  `claude --resume <id>`, and B3/B4 become moot. Worth one deliberate test on a
  throwaway session; until then the `cd` stays, because keeping it is correct
  either way and dropping it wrongly would break every resume.

- `sizeKB` is computed in both scripts and never read. Dead field. (Left in
  place for now — it's the obvious input if the `--min-size-kb` rethink below
  ever happens, and removing it is a one-liner either way.)
- `dirlabel` diverges when `cwd` is a filesystem root: Python falls back to the
  raw `cwd` when the basename comes out empty (`scan-sessions.py:281`),
  PowerShell has no such fallback and emits an empty column
  (`scan-sessions.ps1:265`). Only reachable for `cwd == "/"`. Under the relaxed
  contract `dir` is display-only, so this is now cosmetic rather than a parity
  break — but an empty column is still worse output than a path.
- `--min-size-kb 3` hides stubs by size. A short-but-real Q&A session could fall
  under the threshold; counting user records would be a truer "is this session
  empty" test than byte size.

---

## Sequencing

Dependency-ordered, not value-ordered. Each group is one coherent review.

1. ~~**P0** (fixture root) + **B6** (abort on unreadable file) + numeric-arg
   validation + the `SKILL.md` contract rewrite.~~ **Done 2026-07-30.**
2. ~~**Resume-command pass: B3 + B4 + E4.**~~ **Done 2026-07-30.**
3. ~~**B1** (authoritative timestamp) → **E5** (time filters).~~ **Done 2026-07-30.**
4. **B2** (branch semantics) → then **E3** (show branch, relative times). ← next
5. **E2** (`--tail`). Self-contained, high value per unit of work.
6. **P1** (PowerShell regex extraction, corrected diagnosis) → then **E1** (deep
   content search), after choosing E1's option 1 or 2.

**B5** and the remaining smaller notes are cosmetic; fold them into whichever
pass already touches the relevant lines.
