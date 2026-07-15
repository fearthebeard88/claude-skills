#!/usr/bin/env bash
# Install (copy) skills from this repo into ~/.claude/skills/.
# Copy + sync model: the repo is the source of truth; install only what you
# want on THIS machine (so nothing rides along just for being in the repo).
#
#   ./install.sh                 # list available skills (installs nothing)
#   ./install.sh find-session    # install one (or several, space-separated)
#   ./install.sh --all           # install every skill
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
src="$repo_dir/skills"
dest="$HOME/.claude/skills"

list_skills() { find "$src" -mindepth 1 -maxdepth 1 -type d -exec basename {} \; | sort; }

if [ $# -eq 0 ] || [ "${1:-}" = "--list" ]; then
    echo "Available skills (repo is source of truth; nothing installed):"
    list_skills | sed 's/^/  /'
    echo
    echo "Install:  ./install.sh <skill> [<skill>...]   or   ./install.sh --all"
    exit 0
fi

mkdir -p "$dest"

if [ "$1" = "--all" ]; then
    targets="$(list_skills)"
else
    targets="$*"
fi

# Skill names are kebab-case with no spaces, so word-splitting is safe.
for s in $targets; do
    if [ ! -d "$src/$s" ]; then
        echo "! no such skill: $s" >&2
        echo "  available: $(list_skills | paste -sd', ' -)" >&2
        exit 1
    fi
    rm -rf "${dest:?}/$s"
    cp -r "$src/$s" "$dest/$s"
    echo "installed: $s -> $dest/$s"
done

echo "Done. Run /reload-skills in Claude Code (or restart it)."
