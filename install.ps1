# Install (copy) skills from this repo into ~/.claude/skills/.
# Copy + sync model: the repo is the source of truth; install only what you
# want on THIS machine (so nothing rides along just for being in the repo).
#
#   .\install.ps1                # list available skills (installs nothing)
#   .\install.ps1 find-session   # install one (or several, space-separated)
#   .\install.ps1 -All           # install every skill
[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Skills,
    [switch]$All,
    [switch]$List
)
$ErrorActionPreference = "Stop"

$src  = Join-Path $PSScriptRoot "skills"
$dest = Join-Path (Join-Path $HOME ".claude") "skills"
$available = Get-ChildItem -Path $src -Directory | Select-Object -ExpandProperty Name | Sort-Object

if ($List -or (-not $All -and -not $Skills)) {
    Write-Host "Available skills (repo is source of truth; nothing installed):"
    $available | ForEach-Object { Write-Host "  $_" }
    Write-Host ""
    Write-Host "Install:  .\install.ps1 <skill> [<skill>...]   or   .\install.ps1 -All"
    return
}

New-Item -ItemType Directory -Force -Path $dest | Out-Null

$targets = if ($All) { $available } else { $Skills }
foreach ($s in $targets) {
    $srcSkill = Join-Path $src $s
    if (-not (Test-Path $srcSkill)) {
        throw "no such skill: $s  (available: $($available -join ', '))"
    }
    $destSkill = Join-Path $dest $s
    if (Test-Path $destSkill) { Remove-Item -Recurse -Force $destSkill }
    Copy-Item -Recurse $srcSkill $destSkill
    Write-Host "installed: $s -> $destSkill"
}

Write-Host "Done. Run /reload-skills in Claude Code (or restart it)."
