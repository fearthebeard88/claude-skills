# claude-skills

A personal [Claude Code](https://claude.com/claude-code) **plugin marketplace** —
`fearthebeard88-skills` — hosting the **`fear`** plugin: a collection of skills
and their supporting scripts. Installed as a plugin, it **auto-updates** — push
here and each machine pulls the change shortly after its next start.

> Claude Code skills specifically — the `SKILL.md` format and the tools they
> drive are Anthropic's. Not portable to other agents (Cursor, Copilot, Gemini
> CLI, …) as-is.

## Skills

| Skill | Invoke | What it does |
|-------|--------|--------------|
| [`find-session`](plugins/fear/skills/find-session) | `/fear:find-session` | Search and resume past Claude Code sessions across **all** project directories — the cross-directory counterpart to the built-in `/resume`. Runtime-portable (PowerShell on Windows, Python on Unix, agent-native fallback). |

Skills are namespaced by the plugin, so you invoke them as `/fear:<skill>`.

## Install

Add the marketplace and enable the plugin — via the CLI:

```
/plugin marketplace add fearthebeard88/claude-skills
/plugin install fear@fearthebeard88-skills
```

…or declaratively in your **user** settings (`~/.claude/settings.json`), which
also switches on auto-update:

```json
{
  "extraKnownMarketplaces": {
    "fearthebeard88-skills": {
      "source": { "source": "github", "repo": "fearthebeard88/claude-skills" },
      "autoUpdate": true
    }
  },
  "enabledPlugins": {
    "fear@fearthebeard88-skills": true
  }
}
```

Restart Claude Code (or `/reload-plugins`). With `autoUpdate: true`, later pushes
are pulled automatically shortly after startup, with a prompt to `/reload-plugins`.

## Removing the per-run permission prompt (optional)

A skill that runs a script prompts for permission each time. Pre-approve it with
a **narrow** allow rule in your **user** settings. As a plugin, the scanner runs
from a *versioned* cache path:

```
~/.claude/plugins/cache/fearthebeard88-skills/fear/<version>/skills/find-session/scan-sessions.ps1
```

Wildcard **only the version segment** and keep the rest of the path literal, so
the rule stays scoped to *this one script* yet survives version bumps (swap in
your own home path):

```jsonc
{
  "permissions": {
    "allow": [
      // Windows (PowerShell tool). The * covers the <version> dir; the literal
      // tail keeps this scoped to find-session's scanner, not the whole plugin.
      // Backslashes are DOUBLED (\\\\ in the file) — see the gotcha below.
      "PowerShell(& \"C:\\\\Users\\\\<you>\\\\.claude\\\\plugins\\\\cache\\\\fearthebeard88-skills\\\\fear\\\\*\\\\skills\\\\find-session\\\\scan-sessions.ps1\"*)"
    ]
  }
}
```

Two things that make or break this rule:
- **Doubled backslashes.** The matcher compares against the command with
  *escaped* backslashes, so the rule's path needs `\\` between segments — which
  is `\\\\` in the JSON file. A single-backslash rule silently never matches.
- **Narrowing.** A `*` matches any characters *including* `\`, so anchoring on
  the literal `…\skills\find-session\scan-sessions.ps1` tail is what keeps this
  scoped to this one script rather than blanket-approving the whole plugin.

Not sure of the exact string? Let the prompt write it: choose **"Yes, don't ask
again"** once, copy the rule Claude Code drops into `.claude/settings.local.json`
(it'll have the correct doubled backslashes), then in *user* settings swap the
`<version>` segment for `*` and add a trailing `*` for args.

> A wrong rule is **inert** — it can only fail to match (you keep getting the
> prompt), never over-permit — so iterating on the exact form is safe.

## Layout

```
.claude-plugin/
  marketplace.json                 # marketplace catalog (lists plugins)
plugins/
  fear/
    .claude-plugin/plugin.json     # plugin manifest
    skills/
      find-session/
        SKILL.md                   # skill definition
        scan-sessions.py / .ps1    # supporting scripts (kept behaviorally identical)
```

## Developing

Edit here and push. Machines with `autoUpdate: true` pull it automatically on
their next start; the current session keeps the version it launched with until
`/reload-plugins`. Keep parallel script implementations (`.py` / `.ps1`)
byte-for-byte equivalent in output — see each skill's `SKILL.md` for its own
sync notes and language traps.
