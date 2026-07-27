# HANDOFF — claude-skills / `fear` plugin

_Last updated: 2026-07-27. Maintainer-facing working note (not part of the
installed plugin). Where we left off + what's open._

## ✅ Done and working

- Repo is the **`fearthebeard88-skills`** marketplace hosting the **`fear`**
  plugin; pushed to GitHub (`master`), `autoUpdate: true` — pushes propagate on
  next startup.
- **`/fear:find-session`** works: cross-directory session finder; dispatch by OS
  (PowerShell on Windows, Python on Unix, agent-native fallback); lean TSV
  output; 15-per-page with `--offset` pagination; `--pick N` resume; current
  session auto-excluded via `CLAUDE_CODE_SESSION_ID`.
- Installed via user settings (`extraKnownMarketplaces` + `enabledPlugins`).
- Old manually-copied `~/.claude/skills/find-session/` removed.

## ⏳ OPEN #1 — Windows permission rule for the versioned plugin path

**Goal:** one rule that is narrow (only `scan-sessions.ps1`, not the whole
plugin), version-tolerant (survives `1.0.0` → next), and covers args
(`--pick`/`--query`/`--offset`).

**Confirmed facts**
- The matcher compares against **doubled backslashes** — a rule's path needs
  `\\` between segments (`\\\\` in the JSON file). Single-backslash rules
  silently never match. (This was the bug behind several failed attempts.)
- Plugin path:
  `C:\Users\tim.ferris\.claude\plugins\cache\fearthebeard88-skills\fear\<version>\skills\find-session\scan-sessions.ps1`

**Current user-settings rule (in `~/.claude/settings.json`) — UNVERIFIED:**
```
PowerShell(& "C:\\Users\\tim.ferris\\.claude\\plugins\\cache\\fearthebeard88-skills\\fear\\*\\skills\\find-session\\scan-sessions.ps1"*)
```

**Captured working examples** (verbatim, from "Yes, don't ask again" — these are
the *exact* format the matcher accepts):
```
no-args:  PowerShell(& "C:\\Users\\tim.ferris\\.claude\\plugins\\cache\\fearthebeard88-skills\\fear\\1.0.0\\skills\\find-session\\scan-sessions.ps1")
--pick 6: PowerShell(& "C:\\Users\\tim.ferris\\.claude\\plugins\\cache\\fearthebeard88-skills\\fear\\1.0.0\\skills\\find-session\\scan-sessions.ps1" --pick 6)
```
Note: doubled backslashes; version pinned (`1.0.0`); args captured **literally
after the closing quote with a leading space and NO wildcard**.

**Observed this session (after a real restart, from inside the repo dir):**
- no-args `/fear:find-session` → **silent**
- `--pick 6` → **prompted** (accepted with "don't ask again")
- ⚠️ But the repo held project-local rules that could have produced the no-args
  silence — so we do **not** yet know whether the user-settings rule matches at
  all. That ambiguity is why the cleanup below matters.

**Hypothesis to test next:** my rule used a trailing `*` touching the quote
(`…scan-sessions.ps1"*`). The captured args form shows a **space then args**
after the quote. The general args form may need `…scan-sessions.ps1" *)`
(space before `*`) — or the version wildcard (`fear\\*`) may not be matching at
all. A clean test will tell.

**Next steps when resuming:**
1. Confirm no project-local `.claude/settings.local.json` rules exist (removed
   this session) so results aren't false positives.
2. Restart, then run `/fear:find-session` **from a non-repo directory** → does
   no-args match the *user-settings* rule?
3. Run a `--pick` → does the args form match?
4. If either prompts: "don't ask again", read the captured rule, and reconcile
   the user-settings rule — keep it narrow (anchored on the
   `…\skills\find-session\scan-sessions.ps1` tail) and version-tolerant
   (`fear\\*`), fixing the args coverage (likely space-before-`*`).
5. Update the README permission template + doubled-backslash note to the
   confirmed general form.

## ⏳ OPEN #2 — Linux/Bash permission rule

Not captured yet. On Linux: run `/fear:find-session`, choose "don't ask again",
record the `Bash(python3 …)` rule, put a wildcard-generalized version in user
settings, and finalize the `.py` template in the README.

## Cleanup done this session
- Removed the project-local `.claude/settings.local.json` (it held the two
  captured examples above — preserved here) to eliminate false positives on the
  next test.

## Repo pointers
- Latest commit at handoff: `9783f82`.
- Scripts: `plugins/fear/skills/find-session/scan-sessions.{py,ps1}` — keep their
  output byte-identical (see the skill's `SKILL.md` sync notes).
