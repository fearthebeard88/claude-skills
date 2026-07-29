# Scan every Claude Code session across all project directories and print a
# tab-separated table of session metadata. Primary runtime on Windows, where
# Windows PowerShell 5.1 is built in (no install needed). Written to run on BOTH
# Windows PowerShell 5.1 and PowerShell 7+. Behaviour mirrors scan-sessions.py
# exactly — same args, byte-identical output — so the two stay interchangeable.
#
#   scan-sessions.ps1                        # all sessions, newest-active first
#   scan-sessions.ps1 --query "netsuite"     # only sessions matching the query
#   scan-sessions.ps1 --query "bug" --limit 20
#   scan-sessions.ps1 --min-size-kb 0        # include empty/aborted stubs too
#   scan-sessions.ps1 --pick e5faf172        # resume command for that session id

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
# Resume mode: print just the ready-to-run resume command for one session.
# Takes the session's short id (the first column of the listing) — an id is a
# STABLE handle, so it stays correct even if another live session's mtime
# reorders the ranking between the list and the pick. A bare 1-4 digit number
# is still accepted as a row index into the same ranking, but that form drifts
# under concurrent sessions; prefer the id.
$Pick = ""
# Pagination: skip the first N ranked rows, then show --limit of them. Row
# numbers stay global (offset+1, offset+2, …) for display purposes.
$Offset = 0
for ($i = 0; $i -lt $args.Count; $i++) {
    switch ($args[$i]) {
        "--query"       { $Query     = [string]$args[++$i] }
        "--limit"       { $Limit     = [int]$args[++$i] }
        "--min-size-kb" { $MinSizeKB = [double]$args[++$i] }
        "--preview"     { $ShowPreview = $true }
        "--pick"        { $Pick      = [string]$args[++$i] }
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

# How many leading hex chars of the session uuid form the short id / pick handle.
$IdLen = 8

$Pick = $Pick.Trim()
# A 1-4 digit token is a row index; anything else is an id prefix. Short ids are
# $IdLen chars, so the two forms can't collide.
$PickByRow = [bool]$Pick -and $Pick -match '^\d{1,4}$'
$PickById  = [bool]$Pick -and -not $PickByRow
# An id lookup is exact, so the stub filter can only get in its way — the id
# came from a listing, and forcing 0 means the caller never has to re-pass the
# --min-size-kb they listed with.
if ($PickById) { $MinSizeKB = 0.0 }

# Wrapper text that a user record can carry instead of a real prompt: slash
# commands, local-command plumbing, injected reminders. Never a useful title.
$WrapperRe = '^<(local-command-caveat|local-command-stdout|local-command-stderr|' +
             'command-name|command-message|command-args|system-reminder)\b' +
             '|^Caveat: The messages below'
# A slash-command invocation inside one of those wrappers. Weakest fallback, but
# "/fear:find-session" still identifies a session that holds no prose at all.
$CommandRe = '<command-name>\s*(/?[^<\s]+)\s*</command-name>'

function Clean($s) {
    # Collapse whitespace so multi-line prompts render on one line.
    if ([string]::IsNullOrEmpty($s)) { return $null }
    return ($s -replace '\s+', ' ').Trim()
}

function Truncate($s, $n) {
    if ($s.Length -gt $n) { return $s.Substring(0, $n) + "..." }
    return $s
}

function UserText($rec) {
    # Text a user record carries, or $null for tool results and injected
    # context. Fallback source for sessions that carry no aiTitle and no inline
    # lastPrompt — see the `last-prompt` note in the scan loop. Returns wrapper
    # text as-is; the caller classifies it.
    if ($rec.isSidechain -or $rec.isMeta) { return $null }
    $msg = $rec.message
    if (-not $msg) { return $null }
    $content = $msg.content
    $parts = @()
    if ($content -is [string]) {
        $parts = @($content)
    } elseif ($content) {
        # Text blocks only — a tool_result block is machine output, not a prompt.
        foreach ($b in $content) { if ($b.type -eq 'text' -and $b.text) { $parts += $b.text } }
    } else {
        return $null
    }
    if (-not $parts) { return $null }
    return (Clean ($parts -join ' '))
}

$sessions = [System.Collections.ArrayList]::new()
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
            # Picking by id needs exactly one file's contents, so don't parse
            # the rest — this is what makes --pick <id> near-instant.
            if ($PickById -and -not $id.StartsWith($Pick, [StringComparison]::OrdinalIgnoreCase)) { continue }
            # Skip aborted/empty stubs unless asked to keep them.
            if (($file.Length / 1KB) -lt $MinSizeKB) { continue }

            $title = $null; $prompt = $null; $cwd = $null; $branch = $null
            $relocated = $null; $fallback = $null; $fallbackCmd = $null
            foreach ($line in [System.IO.File]::ReadLines($file.FullName)) {
                if ([string]::IsNullOrWhiteSpace($line)) { continue }
                try { $rec = $line | ConvertFrom-Json } catch { continue }
                $t = $rec.type
                # Titles/prompts are re-emitted as the session grows — keep last.
                if ($t -eq "ai-title" -and $rec.aiTitle) { $title = $rec.aiTitle }
                # A last-prompt record comes in two shapes: one carries the text
                # inline as `lastPrompt`, the other is just a `leafUuid` pointer
                # with no text at all. The pointer form leaves us nothing to show,
                # so track the last real user message as a fallback — otherwise
                # those sessions list as "(untitled)" with an empty preview and
                # can never match a query.
                if ($t -eq "last-prompt" -and $rec.lastPrompt) { $prompt = $rec.lastPrompt }
                if ($t -eq "user") {
                    $ut = UserText $rec
                    if ($ut) {
                        $m = [regex]::Match($ut, $CommandRe, 'IgnoreCase')
                        if ($m.Success) { $fallbackCmd = $m.Groups[1].Value }
                        elseif ($ut -notmatch $WrapperRe) { $fallback = $ut }
                    }
                }
                # Take the LAST cwd, not the first: a /cd relocates the session,
                # and we want the directory it ended up in (the resumable one),
                # not the dead-end origin it started in.
                if ($rec.cwd) { $cwd = $rec.cwd }
                # A relocation records its destination explicitly — trust it over
                # any transient trailing cwd.
                if ($t -eq "relocated" -and $rec.relocatedCwd) { $relocated = $rec.relocatedCwd }
                if (-not $branch -and $null -ne $rec.gitBranch) { $branch = $rec.gitBranch }
            }

            if ($relocated) { $cwd = $relocated }
            if (-not $cwd) {
                # Lossy fallback: decode the folder name (only for the rare
                # session that somehow lacks a cwd record).
                $cwd = $projDir.Name -replace '^([A-Za-z])--', '$1:\' -replace '-', '\'
            }
            # Fallback chain, strongest first: the AI's own title, the inline
            # last prompt, the last real user prose, the last slash command.
            $prompt = Clean $prompt
            if (-not $fallback) { $fallback = $fallbackCmd }
            $title = Clean $title
            if (-not $title) { $title = $prompt }
            if (-not $title -and $fallback) { $title = Truncate $fallback 80 }
            if (-not $title) { $title = "(untitled)" }
            $previewFull = $prompt; if (-not $previewFull) { $previewFull = $fallback }
            if (-not $previewFull) { $previewFull = "" }
            $preview = Truncate $previewFull 140
            $br = Clean $branch; if (-not $br) { $br = "" }

            [void]$sessions.Add([pscustomobject]@{
                id         = $id
                shortId    = $id.Substring(0, $IdLen)
                # Deterministic tie-break for equal mtimes. Dashes stripped so
                # the key is pure lowercase hex — PowerShell's culture-aware
                # string sort and Python's ordinal sort agree on that alphabet,
                # but not on where '-' lands.
                # Parenthesised: inside a hash literal, the comma in -replace's
                # argument list would otherwise end the entry.
                sortKey    = ($id -replace '-', '')
                title      = $title
                preview    = $preview
                cwd        = $cwd
                branch     = $br
                lastActive = $file.LastWriteTime.ToString("yyyy-MM-dd HH:mm")
                mtime      = $file.LastWriteTimeUtc.Ticks
                sizeKB     = [math]::Round($file.Length / 1KB, 1)
            })
        }
    }
}

# All output goes through this so PowerShell emits LF-only (its default string
# output is CRLF) — byte-identical to the Python script, and no stray \r to
# break a pasted resume command.
function Emit($text) { [Console]::Out.Write($text + "`n") }

# --- resume mode: by id ------------------------------------------------------
# An id is resolved against every session, independent of --query/--offset/
# --limit ranking, so it can never point at the wrong row.
if ($PickById) {
    $matched = @($sessions | Sort-Object sortKey)
    if ($matched.Count -eq 1) {
        Emit ('cd "' + $matched[0].cwd + '" && claude --resume ' + $matched[0].id)
    } elseif ($matched.Count -eq 0) {
        Emit "ERROR: --pick $Pick matched no session"
    } else {
        $ids = ($matched | ForEach-Object { $_.shortId }) -join ", "
        Emit "ERROR: --pick $Pick is ambiguous ($($matched.Count) matches: $ids)"
    }
    return
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
    # sortKey ascending breaks mtime ties identically in both scripts; without
    # it, equal-mtime rows order by directory listing and the two drift apart.
    $ranked = $scored | Sort-Object @{e={$_.score};Descending=$true}, @{e={$_.mtime};Descending=$true}, @{e={$_.sortKey};Descending=$false}
} else {
    $ranked = $sessions | Sort-Object @{e={$_.mtime};Descending=$true}, @{e={$_.sortKey};Descending=$false}
}
$rankedArr = @($ranked)

# --- resume mode: by row -----------------------------------------------------
# Legacy/interactive form. Drifts if another live session's mtime reorders the
# ranking between the listing and the pick — --pick <id> is the safe handle.
if ($PickByRow) {
    $n = [int]$Pick
    if ($n -ge 1 -and $n -le $rankedArr.Count) {
        $s = $rankedArr[$n - 1]
        Emit ('cd "' + $s.cwd + '" && claude --resume ' + $s.id)
    } else {
        Emit "ERROR: --pick $n out of range (1-$($rankedArr.Count))"
    }
    return
}

# --- list mode ---------------------------------------------------------------
# Tab-separated, display-only columns plus the short id. No full uuids, no full
# paths, no JSON keys. title has whitespace collapsed already, so it can't
# contain a tab/newline.
$lines = foreach ($s in @($rankedArr | Select-Object -Skip $Offset -First $Limit)) {
    $label = if ($s.cwd -eq $HOME) { "~" } else { (($s.cwd -replace '\\', '/').TrimEnd('/') -split '/')[-1] }
    $cols = @($s.shortId, $s.title, $label, $s.lastActive)
    if ($ShowPreview) { $cols += $s.preview }
    $cols -join "`t"
}
Emit ($lines -join "`n")
