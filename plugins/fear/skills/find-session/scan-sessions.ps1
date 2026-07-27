# Scan every Claude Code session across all project directories and print a
# JSON array of session metadata. Primary runtime on Windows, where Windows
# PowerShell 5.1 is built in (no install needed). Written to run on BOTH
# Windows PowerShell 5.1 and PowerShell 7+. Behaviour mirrors scan-sessions.py
# exactly — same args, same JSON output shape — so the two stay interchangeable.
#
#   scan-sessions.ps1                        # all sessions, newest-active first
#   scan-sessions.ps1 --query "netsuite"     # only sessions matching the query
#   scan-sessions.ps1 --query "bug" --limit 20
#   scan-sessions.ps1 --min-size-kb 0        # include empty/aborted stubs too

# --- args --------------------------------------------------------------------
# Parse GNU-style flags manually so the invocation matches scan-sessions.py
# ("--query" / "--limit" / "--min-size-kb") rather than PowerShell's -Name form.
$Query = ""
$Limit = 15
# Hide aborted/empty stubs by default. Real sessions are >~20 KB; empty starts
# are <3 KB. Pass --min-size-kb 0 to show everything.
$MinSizeKB = 3.0
# Off by default to keep output lean; add the preview column only when titles
# collide and you need it to disambiguate. NOTE: named $ShowPreview, not
# $Preview — PowerShell variables are case-insensitive, so $Preview would
# collide with the per-row $preview value built in the scan loop below.
$ShowPreview = $false
# Resume mode: with --pick N, skip the listing and print just the ready-to-run
# resume command for row N (1-based) of the SAME ranking. Pass the identical
# --query / --min-size-kb you listed with so the row numbers line up. This is
# what keeps id + cwd (the expensive fields) out of the every-browse output.
$Pick = 0
# Pagination: skip the first N ranked rows, then show --limit of them. Row
# numbers stay global (offset+1, offset+2, …) so --pick lines up across pages.
$Offset = 0
for ($i = 0; $i -lt $args.Count; $i++) {
    switch ($args[$i]) {
        "--query"       { $Query     = [string]$args[++$i] }
        "--limit"       { $Limit     = [int]$args[++$i] }
        "--min-size-kb" { $MinSizeKB = [double]$args[++$i] }
        "--preview"     { $ShowPreview = $true }
        "--pick"        { $Pick      = [int]$args[++$i] }
        "--offset"      { $Offset    = [int]$args[++$i] }
    }
}

$ErrorActionPreference = "Stop"
# Emit UTF-8 without a BOM so [Console]::Out.Write matches the Python script's
# byte output (the default 5.1 console codepage would mangle non-ASCII titles).
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$root = Join-Path (Join-Path $HOME ".claude") "projects"
# The session we're running inside — exclude it; you're already attached to it.
# Absent when run outside Claude Code, so this is a no-op there.
$current = $env:CLAUDE_CODE_SESSION_ID

function Clean($s) {
    # Collapse whitespace so multi-line prompts render on one line.
    if ([string]::IsNullOrEmpty($s)) { return $null }
    return ($s -replace '\s+', ' ').Trim()
}

$sessions = @()
if (Test-Path $root) {
    # Resumable sessions live exactly one level down: projects\<dir>\<id>.jsonl.
    # Do NOT recurse deeper — nested subagents\ and workflows\ folders hold
    # transcript artifacts (e.g. journal.jsonl) that aren't resumable sessions.
    foreach ($projDir in Get-ChildItem -Path $root -Directory) {
        foreach ($file in Get-ChildItem -Path $projDir.FullName -Filter *.jsonl -File) {
            $id = $file.BaseName
            # Skip subagent sidechain transcripts — not resumable sessions.
            if ($id -like "agent-*") { continue }
            # Skip the current session — no point resuming what you're in.
            if ($current -and $id -eq $current) { continue }
            # Skip aborted/empty stubs unless asked to keep them.
            if (($file.Length / 1KB) -lt $MinSizeKB) { continue }

            $title = $null; $prompt = $null; $cwd = $null; $branch = $null
            foreach ($line in [System.IO.File]::ReadLines($file.FullName)) {
                if ([string]::IsNullOrWhiteSpace($line)) { continue }
                try { $rec = $line | ConvertFrom-Json } catch { continue }
                $t = $rec.type
                # Titles/prompts are re-emitted as the session grows — keep last.
                if ($t -eq "ai-title"    -and $rec.aiTitle)    { $title  = $rec.aiTitle }
                if ($t -eq "last-prompt" -and $rec.lastPrompt) { $prompt = $rec.lastPrompt }
                # cwd / branch are stable — take the first non-empty we see.
                if (-not $cwd -and $rec.cwd) { $cwd = $rec.cwd }
                if (-not $branch -and $null -ne $rec.gitBranch) { $branch = $rec.gitBranch }
            }

            if (-not $cwd) {
                # Lossy fallback: decode the folder name (only for the rare
                # session that somehow lacks a cwd record).
                $cwd = $projDir.Name -replace '^([A-Za-z])--', '$1:\' -replace '-', '\'
            }
            $title = Clean $title; if (-not $title) { $title = Clean $prompt }; if (-not $title) { $title = "(untitled)" }
            $previewFull = Clean $prompt; if (-not $previewFull) { $previewFull = "" }
            $preview = if ($previewFull.Length -gt 140) { $previewFull.Substring(0, 140) + "..." } else { $previewFull }
            $br = Clean $branch; if (-not $br) { $br = "" }

            $sessions += [pscustomobject]@{
                id         = $id
                title      = $title
                preview    = $preview
                cwd        = $cwd
                branch     = $br
                lastActive = $file.LastWriteTime.ToString("yyyy-MM-dd HH:mm")
                mtime      = $file.LastWriteTimeUtc.Ticks
                sizeKB     = [math]::Round($file.Length / 1KB, 1)
            }
        }
    }
}

# --- filter + rank -----------------------------------------------------------
$q = $Query.Trim().ToLower()
if ($q) {
    $terms = $q -split '\s+' | Where-Object { $_ }
    $scored = foreach ($s in $sessions) {
        $hay = "$($s.title) $($s.preview) $($s.cwd) $($s.branch)".ToLower()
        $titleLc = $s.title.ToLower()
        $score = 0
        foreach ($term in $terms) {
            if ($titleLc.Contains($term)) { $score += 3 }
            elseif ($hay.Contains($term)) { $score += 1 }
        }
        if ($score -gt 0) { $s | Add-Member -NotePropertyName score -NotePropertyValue $score -PassThru }
    }
    $ranked = $scored | Sort-Object @{e={$_.score};Descending=$true}, @{e={$_.mtime};Descending=$true}
} else {
    $ranked = $sessions | Sort-Object @{e={$_.mtime};Descending=$true}
}

# All output goes through this so PowerShell emits LF-only (its default string
# output is CRLF) — byte-identical to the Python script, and no stray \r to
# break a pasted resume command.
function Emit($text) { [Console]::Out.Write($text + "`n") }

# --- resume mode -------------------------------------------------------------
# Print only the chosen row's resume command; id + cwd cost is paid once, here.
$rankedArr = @($ranked)
if ($Pick -gt 0) {
    if ($Pick -le $rankedArr.Count) {
        $s = $rankedArr[$Pick - 1]
        Emit ('cd "' + $s.cwd + '" && claude --resume ' + $s.id)
    } else {
        Emit "ERROR: --pick $Pick out of range (1-$($rankedArr.Count))"
    }
    return
}

# --- list mode ---------------------------------------------------------------
# Tab-separated, display-only columns — no UUIDs, no full paths, no JSON keys.
# title has whitespace collapsed already, so it can't contain a tab/newline.
$lines = foreach ($s in @($rankedArr | Select-Object -Skip $Offset -First $Limit)) {
    $label = if ($s.cwd -eq $HOME) { "~" } else { (($s.cwd -replace '\\', '/').TrimEnd('/') -split '/')[-1] }
    $cols = @($s.title, $label, $s.lastActive)
    if ($ShowPreview) { $cols += $s.preview }
    $cols -join "`t"
}
Emit ($lines -join "`n")
