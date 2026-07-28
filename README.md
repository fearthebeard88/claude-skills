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
