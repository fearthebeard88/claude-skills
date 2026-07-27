# claude-skills

A personal collection of [Claude Code](https://claude.com/claude-code) skills
and their supporting scripts. This repo is the **source of truth**; skills are
**copied** into `~/.claude/skills/` on each machine where you want them — so you
can keep a skill in the repo without installing it everywhere (e.g. skip
work-inappropriate ones on a work machine).

> These are Claude Code skills specifically — the `SKILL.md` format and the tools
> they drive are Anthropic's. They aren't portable to other agents (Cursor,
> Copilot, Gemini CLI, …) as-is.

## Skills

| Skill | What it does |
|-------|--------------|
| [`find-session`](skills/find-session) | Search and resume past Claude Code sessions across **all** project directories — the cross-directory counterpart to the built-in `/resume`. Runtime-portable (Python or PowerShell, with an agent-native fallback). |

## Installing a skill

Use the install helper — it copies a named skill (or several) into
`~/.claude/skills/`, so you install only what you want on a given machine:

```bash
# macOS / Linux
./install.sh                 # list available skills (installs nothing)
./install.sh find-session    # install one (or several, space-separated)
./install.sh --all           # install everything

# Windows (PowerShell)
.\install.ps1                # list
.\install.ps1 find-session   # install
.\install.ps1 -All           # install everything
```

Then run `/reload-skills` in Claude Code (or restart it), and invoke with
`/find-session`. Prefer a manual copy? `cp -r skills/find-session ~/.claude/skills/`
does the same thing.

## Removing the per-run permission prompt (optional)

A skill that runs a script triggers a permission prompt each time — Claude Code
gates the shell tool call. To pre-approve it, add a **narrow** allow rule
(scoped to the scanner script, *not* a blanket runtime allow) to your **user**
settings, `~/.claude/settings.json`. Merge into any existing `permissions.allow`
array; swap in your own home path:

```jsonc
{
  "permissions": {
    "allow": [
      // Windows — runs via the PowerShell tool. Confirmed format: the matcher
      // keys on the literal `& "..."` call operator; trailing * covers args.
      "PowerShell(& \"C:\\Users\\<you>\\.claude\\skills\\find-session\\scan-sessions.ps1\"*)",
      // macOS / Linux — runs via the Bash tool (best-guess; confirm as below):
      "Bash(python3 /home/<you>/.claude/skills/find-session/scan-sessions.py *)"
    ]
  }
}
```

Restart Claude Code afterward — permission rules load at startup.

> **The Windows form above is confirmed** — it's the shape Claude Code itself
> generated (via "Yes, don't ask again"), generalized with a trailing `*` so it
> covers `--pick`/`--query`/etc. The **Bash form is still a best-guess**, since
> the script-invocation match string isn't documented and we haven't captured it
> on Unix yet.
>
> **If a rule still prompts:** choose **"Yes, don't ask again"** on the prompt and
> take whatever rule Claude Code writes to `.claude/settings.local.json` — that's
> the guaranteed-correct form for your platform. Copy it into *user* settings
> (and add a trailing `*` to cover arguments) so it applies from every directory.
> A wrong rule is **inert** (it can only fail to match, never over-permit), so
> iterating is safe.

## Layout

```
skills/
  <skill-name>/
    SKILL.md          # the skill definition (name, description, instructions)
    *.py / *.ps1      # supporting scripts, if any
```

## Developing

Edit here in the repo (the source of truth), then re-copy the changed skill into
`~/.claude/skills/` to test it live. Keep any parallel script implementations
(e.g. a `.py` and a `.ps1`) behaviorally identical — see each skill's `SKILL.md`
for its own sync notes.
