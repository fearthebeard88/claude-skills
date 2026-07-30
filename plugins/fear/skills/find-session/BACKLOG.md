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

The `dirlabel` root-case divergence drops from parity break to cosmetic.

**In hindsight the relaxation bought almost nothing.** It was requested for E1,
which turned out not to need it — E1 shipped by *specifying* the algorithm
(option 1), and the relaxation only ever freed output plumbing and two
display-only columns. It also didn't license the P1 regex fast path it was
supposed to, because that was rejected on correctness rather than on parity. Worth
remembering next time a contract looks like the obstacle: here the contract was
never the thing in the way.

`SKILL.md` has been rewritten to this contract (done).

**Done — all six sequencing steps (2026-07-30).** Shipped: B6, P0, numeric-arg
validation, B3, B4, E4, B1, E5, B2, E3 (branch half) and E2, E1. Rejected on
measured evidence: E3's relative times, and all of P1. Everything shipped is
covered by `test-parity.py` and mutation-tested. `SKILL.md` is rewritten to the
relaxed contract. **Only E6 and the smaller notes remain.**

Eight further divergences surfaced while testing and were fixed in the same
passes — see the entries under "Already fixed". Most would not have been found by
reading the code. Three were found only *after* the suite had gone green: nested
timestamps (caught by diffing against the real store), the UTF-16 truncation
mismatch (caught while scoping E1), and the deep score weight (caught by
mutation). The recurring lesson is that the suite tests what someone thought to
model, so a green run is evidence about the fixture, not about the code.

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
- **B2** — `gitBranch` is now last-wins, matching `cwd`. A session that started on
  `master` and moved to a feature branch reports the feature branch, which is both
  the more useful answer and the one consistent with how `cwd` already behaved.
  `$null`/`is not None` rather than a truthiness test, so moving *out* of a repo
  (recorded as an empty branch) genuinely clears it while the many records that
  simply omit the key don't.
- **Culture-sensitive case folding (found 2026-07-30 while scoping E1).** All
  three PowerShell `.ToLower()` calls in the search path followed the *current
  culture*, while Python's `str.lower()` is locale-independent. Under `tr-TR` or
  `az-AZ`, `"INVOICE".ToLower()` is `ınvoıce` with a dotless i, so `--query
  invoice` returned **1 row in Python and 0 in PowerShell** — measured end to end
  on both hosts before the fix. Now `ToLowerInvariant()`, and the home-dir
  comparison uses an explicit `OrdinalIgnoreCase` instead of `-eq` for the same
  reason. `String.Contains` was checked and is already ordinal. Pinned by a test
  that runs the scanner under en-US, tr-TR and az-AZ; reverting fails exactly the
  latter two.
- **Truncation counted UTF-16 code units, not code points (same session).**
  Python's `len()`/slicing count code points; .NET's `.Length`/`Substring()` count
  code units, so an astral character is 1 to Python and 2 to .NET. Measured: a
  50-emoji title truncated at 80 kept **50 emoji in Python and 40 in PowerShell**.
  That's a different title, therefore a different search score, therefore possibly
  a different **order** — a breach of the contract, not a cosmetic difference.
  `Substring` would also cut a surrogate pair in half and emit a lone half
  character. PowerShell's `Truncate` now walks code points (with a
  `.Length <= n` fast path, since code points never exceed code units). **7 files
  in the real store contain non-BMP characters**, and the parity suite had been
  green for days without a single astral fixture — the gap was found by reasoning
  about E1's scoring, not by the tests.
- **E2** — `--pick <id> --tail <N>` prints a session's last N exchanges in place,
  so "what did I decide about X?" no longer needs a terminal switch. Two decisions
  that came out of measuring rather than from the original write-up:
  - **N counts exchanges, not messages.** A first cut counted messages and was
    visibly wrong on real data: assistant records outnumber user records **3.3:1**,
    and **16 of 61 sessions contain a run of 4+ consecutive assistant messages**
    (longest 34), so `--tail 4` routinely returned four fragments of Claude's
    tool-call narration with the question that prompted it scrolled off screen. An
    exchange — one user message plus what followed — makes `--tail 3` mean "the
    last 3 things I asked".
  - **Only the final reply of an exchange prints.** Otherwise a single 34-message
    turn floods the output, and per-message truncation doesn't help because the
    problem is the message *count*. The earlier messages in a turn are mostly
    "let me check X" narration; the last carries the conclusion. Output is
    therefore at most 2N lines, which is predictable enough to drive from a skill.
    Cost: mid-turn reasoning isn't recoverable — documented, not hidden.
  - `--tail` deliberately does **not** inherit B3's directory check. A session
    whose folder was deleted is precisely the one that can't be resumed and most
    needs reading. Mutation-tested both ways.
  - Harness-injected notices (`[Request interrupted by user for tool use]`, 13 of
    them in the reference store) arrive as *user* records and read like
    conversation. They're filtered from transcripts via a separate pattern from
    `WRAPPER_RE`, because that one also gates the title fallback — an interrupted
    session should still get a title from its real prose.
- **E3 (branch half)** — the branch is shown as an `@branch` suffix on the dir
  column, e.g. `claude-skills@master`. See the E3 entry below for why it isn't a
  separate column and why `HEAD` is suppressed. **Note this changed ranking as
  well as display**: normalising `HEAD` to empty removes it from the search
  haystack, so a query for `head` no longer matches ~90% of sessions at score 1.
  That's the intent, but it is a scoring change, not only a cosmetic one.

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

### ~~B2~~ / ~~E3~~ — done 2026-07-30, see "Already fixed"

Kept below for the reasoning, since E3's second half was **rejected** on the
data rather than implemented.

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

### P1 — REJECTED 2026-07-30. Regex extraction is unsafe; nothing safe is measurable.

**Closed with evidence rather than implemented.** Every avenue P1 proposed was
costed, and each one is either unsafe or produces no measurable end-to-end
improvement. Details first, then the original analysis for context.

**Where the time actually goes** (real 19 MB store, min of 5 runs):

| stage | Windows PowerShell 5.1 | PowerShell 7 |
|-------|------------------------|--------------|
| bare interpreter startup | 0.18 s | 0.29 s |
| + script load (empty store) | 0.34 s | 0.43 s |
| **full listing** | **1.84 s** | **2.78 s** |
| …of which `ConvertFrom-Json` | ~1.01 s (**55%**) | ~0.54 s (23%) |
| …of which `ReadLines` | 0.12 s | 0.07 s |
| …of which other per-line work | ~0.37 s | **~1.74 s** |

So parsing dominates on 5.1 but not on 7 — 7 is *slower overall* while parsing
*faster*, because its per-line script work (property access, regex, function
calls) costs ~1.7 s. Any parse optimisation therefore helps one host only.

**1. Regex field extraction: UNSAFE.** Measured exhaustively — for all 7,732
records, compare what a `"key"\s*:\s*"([^"]*)"` regex extracts against the parsed
top-level value:

| field | top-level on | regex **disagrees** on |
|-------|--------------|------------------------|
| `cwd` | 5,584 | **5,584 (100%)** |
| `relocatedCwd` | 58 | **58 (100%)** |
| `type` | 7,732 | **3,444 (45%)** |
| `timestamp` | 5,726 | **372** |
| `lastPrompt` | 450 | 12 |
| `gitBranch` | 5,584 | 0 |
| `aiTitle` | 446 | 0 |

`cwd` fails on every single record because JSON-escaped backslashes
(`"C:\\Users\\…"`) don't match the decoded value. `type` fails because nested
content blocks carry `"type":"text"` earlier in the line. `timestamp` disagrees on
**372 records** — worth noting that the ad-hoc check during B1 found only 9
*lines* and concluded nesting was harmless, which is exactly how that bug shipped
for an afternoon. Regex is safe only for `gitBranch` and `aiTitle`, which are the
two fields there is no reason to optimise.

**2. Skipping `try`/`catch`: NOT POSSIBLE.** It looked worth 267 ms on PS7. But
`ConvertFrom-Json` throws a *terminating* `ArgumentException` on malformed JSON on
both hosts, so `-ErrorAction SilentlyContinue` never gets a say. Dropping the
catch would cost the whole session instead of the bad line. Verified; a fixture
session now carries a malformed line mid-file.

**3. Size-based hybrid (parse small lines, regex big ones): WORSE.** Parse cost is
dominated by per-line overhead, not bytes — 6,582 small lines cost 755 ms while
1,070 big ones cost 305 ms. The hybrid measured 959 ms on 5.1 (vs 1,131 parsing
everything) and 1,012 ms on PS7 (vs 739) — a pessimisation on the host it was
supposed to help.

**4. Tail-reading: still rejected**, for the reasons in the original analysis
below (an `aiTitle` can sit 30 KB from EOF; losing it degrades titles silently).

**5. `-InputObject` instead of the pipeline: KEPT, but it is not a win.**
Identical semantics, and 846 → 536 ms of isolated parse time on PS7. But 7
interleaved runs per host put the end-to-end difference inside the noise (5.1
−0.10 s, PS7 0.00 s). Kept only because it cannot be slower.

**If performance does become a real problem, don't micro-optimise the parse.**
At the current growth rate (~3 MB per heavy session) a year of history is a
~10× store and ~15 s on 5.1, which would be painful. The right answer then is to
stop doing a full scan at all: cache extracted metadata keyed on
`(path, size, mtime)` and re-read only files whose key changed, making a listing
O(changed files) instead of O(store). That is a real feature with its own
invalidation risks — note that B1 established mtime is untrustworthy as a
*recency* signal, though it remains fine as a *change* signal — but it is the only
approach with an order-of-magnitude payoff. Filed here as P1's successor rather
than as a variant of it.

---

### Original P1 analysis (kept for context)

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

### ~~E1~~ — done 2026-07-30 via option 1 (spec it tightly). Original entry below.

Shipped as `--deep`, requiring `--query`. The algorithm is written out as a
`SEARCH SPEC` block at the top of `scan-sessions.py` (summarised in the `.ps1`),
covering tokenisation, folding, match semantics, the three scoring tiers, what
counts as deep text, and the ordering guarantee. Decisions that made it tractable:

- **Reuse `transcript_text()`.** Deep text is *exactly* what `--tail` shows, so E1
  introduced no new extraction surface -- the hardest part of the parity problem
  was avoided rather than solved. It also gives a useful property: if `--deep`
  matched a session, `--tail` can show you the text that matched.
- **Deep is a third tier worth 1, reached only when title and metadata both miss.**
  So `--deep` can add sessions but cannot reorder those a shallow search already
  found; retrying with `--deep` is always safe. Verified on the real store:
  `--query claude` gives 21 rows, `--deep` gives 32, and the 21 keep their relative
  order.
- **Score during the scan, keep only which tokens hit.** Retaining every session's
  transcript to search afterwards would mean holding the whole store in memory.
- **Explicit `[ 	
]+` tokeniser**, because Python's `str.split()` and
  .NET's `\s` disagree about U+001C-U+001F.
- Cost: +0.04 s on Python, +0.53 s on 5.1, +0.56 s on pwsh. Opt-in, as designed.

Shallow search is byte-identical to the previous version across 10 queries, which
also confirms dropping the title from the tier-2 haystack was the no-op it looks
like (a token can never span the joining space, since tokens are split on
whitespace).

**Two mutations initially escaped the suite, and both are worth remembering:**
changing the deep score weight from 1 to 2 *in Python only* broke nothing, and
swapping the tokeniser for `str.split()` broke nothing. Both were invisible because
no fixture exercised them -- the weight needed two sessions that tie only when a
deep hit is worth exactly 1, and the tokeniser needed a query containing U+001C.
Both fixtures now exist and both mutations now fail. This is the third time here
that a green suite was hiding a real gap; the pattern is always the same, that the
suite tests what someone thought to model.

### E1 — search conversation content, not just metadata

**The biggest functional gap.** `--query` scores against `title + preview + cwd
+ branch` only, where `preview` is the *last* prompt. But "find my session about
X" usually means X came up mid-conversation, and the last prompt is very often
"yes do that" or "commit it". The skill's own description promises to find a
session by what it was about; today it can only find one by what it was titled.

Suggested shape: a `--deep` flag that also scores message text, so the cheap path
stays cheap and the expensive path is opt-in. Pairs with P1 — cheap default
listing, full read only under `--deep`.

**Two of E1's supposed risks turned out to be live bugs, now fixed.** Scoping this
item is what surfaced the culture-sensitive `ToLower()` and the UTF-16 truncation
divergence (both under "Already fixed"). Neither was an E1 risk — both affected
`--query` and the listing *today*. So the language-level primitives E1 needs are
now sound, and what remains below is genuinely a specification problem rather than
a pile of latent Unicode traps.

**Parity is still the hard part, and relaxing the contract barely helps here.**
The contract was relaxed to "same rows, same order" partly for this item (see
Decision above) — but scoring divergence changes *which sessions match* and
*what they score*, which changes the row set and the order. Those are exactly
what the new contract still requires to be identical. The relaxation frees output
plumbing, not scoring.

So E1 still needs both scripts to tokenize, match, and score the same way. The
surface: which record types and content blocks count as searchable text, how much
of each message is scored (any truncation must match — and truncation is now
code-point based on both sides, so that part is settled), dedup of repeated text,
and the per-term weighting.

One residual, untested divergence to keep in mind: **query tokenization.** Python's
`str.split()` with no arguments splits on Unicode whitespace per `str.isspace()`,
while PowerShell's `-split '\s+'` uses .NET's `\s`. These sets aren't identical —
`U+001C`–`U+001F` are whitespace to Python but not to .NET `\s`. Vanishingly
unlikely in a typed query, and not worth pre-emptively fixing, but if E1 widens
the tokenizer it's worth pinning with a fixture rather than assuming.

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

### ~~E2~~ — done 2026-07-30, see "Already fixed". Original entry kept below.

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

- ~~Show the git branch~~ — **done, but not as written.** The raw value is
  unusable as a column: **55 of 62 sessions record `HEAD`**, not a branch name,
  and 52 of those 55 were simply not in a git repo. Printing it would have put a
  meaningless `HEAD` on ~90% of rows — a worse version of the very `~` problem
  this item was raised to fix. Shipped instead as an `@branch` suffix on the
  existing dir column, shown only for a real branch (6 rows of 61 here), with
  `HEAD` normalised away at the source so it stays out of the search haystack
  too.
- ~~Consider relative times ("2h ago", "3d ago")~~ — **rejected on the data.**
  Sessions cluster heavily on single days: **19 share 2026-07-06** and 8 share
  2026-07-29. At day granularity those 19 rows all read "24d ago" and become
  indistinguishable, while the absolute time-of-day (17:35 vs 08:16) is exactly
  what separates them. Relative times would also make output non-deterministic
  between runs. `--since`/`--before` (E5) already covers the "last week" style of
  request, which was the underlying need. Not worth revisiting unless the store's
  shape changes a lot.

### ~~E6~~ — REJECTED 2026-07-30 on measurement. Original entry below.

E6 asked to check "whether this is annoying in practice or just cosmetic" before
building. Measured on the real store (61 non-stub sessions), it is neither quite —
it's rarer than described, and **both proposed remedies make things worse.**

- **4 titles are shared, covering 8 rows (13%).** But **6 of those 8 are already
  separated by the dir or time columns**, which every row carries. Two sessions
  merely sharing a title are usually different conversations days apart — the two
  titled `Clarify AI agent definition and Claude Code classification` are two weeks
  apart in different directories, one about "Build Lesson 6 with the /handoff
  exercise" and the other a glossary discussion.
- **Only 2 rows of 61 (3%) are genuinely indistinguishable**, and they are
  indistinguishable because the sessions are *identical*: same title, dir, time,
  preview, and content (`"Reply with exactly: ok"` -> `"ok"`). Throwaway test
  sessions that differ only by id. No column can separate them because there is
  nothing to separate.
- **Zero collisions occur within the default first page of 15.** A collision needs
  title AND dir AND time-to-the-minute to match, which effectively requires
  near-simultaneous duplicate sessions. Rare by construction, and it does not get
  worse as the store grows, because what matters is collisions *within a page*.

Why each proposed fix is counterproductive:

- **Auto-enable `--preview` when titles collide within a page.** Measured:
  `--preview` would separate **2** of the 8 colliding rows and **fail on 6** — while
  6 were already separated by dir/time. So it adds a column for the rows that don't
  need it and fails the ones that do. Strictly worse than doing nothing.
- **Collapse duplicates into one row with a count.** Would have collapsed the two
  `Clarify AI agent…` sessions, which are genuinely different conversations. That
  hides a session the user might be looking for — an actively harmful failure mode,
  and a silent one.

Note the columns improved under E3 and B1 after this item was written: real
activity timestamps replaced mtime (so same-titled sessions now show their true,
usually distinct, times) and the dir column gained `@branch`. Part of E6's premise
was dissolved by work done elsewhere.

**Shipped instead:** one sentence of `SKILL.md` guidance for the only case the data
supports — when two rows are identical *and* `--preview` doesn't separate them,
say they're duplicates of the same conversation rather than presenting them as a
meaningful choice, and don't silently drop either. No code change.

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
4. ~~**B2** (branch semantics) → **E3** (show branch; relative times rejected).~~
   **Done 2026-07-30.**
5. ~~**E2** (`--tail`).~~ **Done 2026-07-30.**
6. ~~**P1** (PowerShell regex extraction) — rejected on measured evidence.
   **E1** (deep content search) — shipped via option 1.~~ **Done 2026-07-30.**

**The sequenced work is complete, and E6 is closed too** (rejected on measurement
— see E6). What remains is only the smaller notes, plus P1's successor if listing
performance ever becomes a real problem.

Final tally: **twelve items shipped, three rejected on measured evidence** (E3's
relative times, all of P1, all of E6). Every rejection carries the measurements
that drove it, so none of them gets re-opened on intuition.

Only **E1**, **P1**, **E6** and the smaller notes remain. Note that E2 has taken
some pressure off E1: "find the session where we discussed X" is often really
"remind me what we decided", and `--tail` answers that once the session is
identified — so E1's value now rests on the cases where the *title* genuinely
doesn't identify the session at all.

**B5** and the remaining smaller notes are cosmetic; fold them into whichever
pass already touches the relevant lines.
