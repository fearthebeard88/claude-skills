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

so scope the rule to the plugin directory and let a trailing `*` absorb the
version segment **and** the args — otherwise the rule breaks on every update:

```jsonc
{
  "permissions": {
    "allow": [
      // Windows (PowerShell tool). Trailing * after the plugin dir survives version bumps.
      "PowerShell(& \"C:\\Users\\<you>\\.claude\\plugins\\cache\\fearthebeard88-skills\\fear\\*)"
    ]
  }
}
```

> The exact match string for a script invocation isn't documented, so treat the
> shape above as intended-but-unverified. Reliable path: on the prompt choose
> **"Yes, don't ask again,"** take what Claude Code writes to
> `.claude/settings.local.json`, then in *user* settings replace the `<version>`
> segment with `*` so it stays valid across updates. A wrong rule is **inert**
> (it can only fail to match, never over-permit), so iterating is safe.

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
