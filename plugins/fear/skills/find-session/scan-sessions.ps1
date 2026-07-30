# Scan every Claude Code session across all project directories and print a
# tab-separated table of session metadata. Primary runtime on Windows, where
# Windows PowerShell 5.1 is built in (no install needed). Written to run on BOTH
# Windows PowerShell 5.1 and PowerShell 7+. Behaviour mirrors scan-sessions.py:
# same args, and the same rows in the same order, so the two stay interchangeable.
#
#   scan-sessions.ps1                        # all sessions, newest-active first
#   scan-sessions.ps1 --query "netsuite"     # only sessions matching the query
#   scan-sessions.ps1 --query "bug" --limit 20
#   scan-sessions.ps1 --min-size-kb 0        # include empty/aborted stubs too
#   scan-sessions.ps1 --pick e5faf172        # resume command for that session id
#   scan-sessions.ps1 --pick e5faf172 --copy # ...and copy it to the clipboard
#   scan-sessions.ps1 --since 7d             # active in the last 7 days
#   scan-sessions.ps1 --since 2026-07-01 --before 2026-07-15
#   scan-sessions.ps1 --pick e5faf172 --tail 6  # read it without resuming
#
# NOTE: this file is saved as UTF-8 WITH a BOM, deliberately. Windows PowerShell
# 5.1 reads a BOM-less .ps1 as ANSI, which turns any UTF-8 multi-byte character
# into mojibake — and for an em-dash that mojibake includes U+201D RIGHT DOUBLE
# QUOTATION MARK, which PowerShell honours as a string delimiter. A single
# em-dash inside a double-quoted literal therefore terminated the string early
# and cascaded into parse errors across the whole file. The BOM fixes the class;
# keep double-quoted literals ASCII anyway as a second line of defence.
#
# Test hooks (both default to the real values, so they're no-ops in normal use):
#
#   FIND_SESSION_ROOT  scan root, instead of ~/.claude/projects
#   FIND_SESSION_HOME  path treated as home for the `~` dir label
#   FIND_SESSION_NOW   "now" in epoch ms, so relative --since/--before are fixed
#
# These exist so both scripts can be aimed at a fixture store and diffed against
# each other. $HOME is READ-ONLY in PowerShell (assigning to it fails with
# VariableNotWritable) and $env:HOME does not feed it on Windows, so without an
# env var there is no way to point this script anywhere but the real store.

$ErrorActionPreference = "Stop"
# Emit UTF-8 without a BOM so [Console]::Out.Write matches the Python script's
# text (the default 5.1 console codepage would mangle non-ASCII titles). Set
# before arg parsing so ArgError below can report through the same channel.
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)

# --- args --------------------------------------------------------------------
# Parse GNU-style flags manually so the invocation matches scan-sessions.py
# ("--query" / "--limit" / "--min-size-kb") rather than PowerShell's -Name form.

function ArgError($msg) {
    # Same `ERROR: …` convention as --pick failures, on stdout, exit 2. Callers
    # read stdout, and scan-sessions.py reports bad args identically.
    [Console]::Out.Write("ERROR: $msg`n")
    exit 2
}

# Parse with InvariantCulture, not the current culture: Python's int()/float()
# only ever accept a dot decimal separator, so a comma-decimal locale would
# otherwise make --min-size-kb 3.5 valid on one script and invalid on the other.
function ParseNonNegInt($flag, $v) {
    $out = 0
    $ok = [int]::TryParse($v, [System.Globalization.NumberStyles]::Integer,
                          [System.Globalization.CultureInfo]::InvariantCulture, [ref]$out)
    if (-not $ok) { ArgError "argument $flag`: expects an integer, got '$v'" }
    if ($out -lt 0) { ArgError "argument $flag`: must be >= 0, got $out" }
    return $out
}

function ParseNonNegNum($flag, $v) {
    $out = 0.0
    $ok = [double]::TryParse($v, [System.Globalization.NumberStyles]::Float,
                             [System.Globalization.CultureInfo]::InvariantCulture, [ref]$out)
    if (-not $ok) { ArgError "argument $flag`: expects a number, got '$v'" }
    if ($out -lt 0) { ArgError "argument $flag`: must be >= 0, got $out" }
    return $out
}

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
# Copy the resume command to the clipboard as well as printing it. Only
# meaningful with --pick; a no-op for a listing. NOTE: named $CopyCmd, not
# $Copy — PowerShell has a Copy-Item alias `copy`, and a bare $Copy reads badly
# next to it.
$CopyCmd = $false
# Time window, either end optional. Each takes a relative age ("7d", "12h", "2w")
# or an absolute local date ("2026-07-01"). The window is half-open: --since is
# inclusive, --before is exclusive, so `--since D --before D` is empty and
# adjacent windows don't double-count a session.
$Since = ""
$Before = ""
# Read mode: with --pick, print the last N conversation messages instead of a
# resume command. Answers "what did I decide about X in that session?" without
# leaving the current one. $null (not 0) when absent, so `--tail 0` is
# distinguishable from not passing it at all.
$Tail = $null
# Comparisons are case-SENSITIVE (-ceq / -ccontains) because argparse is: to
# Python, "--LIMIT" is an unknown argument, and the two must agree on that.
$ValueFlags = @("--query", "--limit", "--min-size-kb", "--pick", "--offset",
                "--since", "--before", "--tail")
$Unknown = [System.Collections.ArrayList]::new()
for ($i = 0; $i -lt $args.Count; $i++) {
    $a = [string]$args[$i]
    if ($a -ceq "--preview") {
        $ShowPreview = $true
    } elseif ($a -ceq "--copy") {
        $CopyCmd = $true
    } elseif ($ValueFlags -ccontains $a) {
        if ($i + 1 -ge $args.Count) { ArgError "argument $a`: expected one argument" }
        $v = [string]$args[++$i]
        if     ($a -ceq "--query")       { $Query     = $v }
        elseif ($a -ceq "--pick")        { $Pick      = $v }
        elseif ($a -ceq "--since")       { $Since     = $v }
        elseif ($a -ceq "--before")      { $Before    = $v }
        elseif ($a -ceq "--limit")       { $Limit     = ParseNonNegInt $a $v }
        elseif ($a -ceq "--offset")      { $Offset    = ParseNonNegInt $a $v }
        elseif ($a -ceq "--min-size-kb") { $MinSizeKB = ParseNonNegNum $a $v }
        elseif ($a -ceq "--tail")        { $Tail      = ParseNonNegInt $a $v }
    } else {
        # Previously ignored silently, so a typo'd flag failed loudly in Python
        # and quietly here — the listing just came back with default settings.
        # Collected rather than reported immediately, because argparse reports
        # every unrecognized token in one message ("--LIMIT 3", not "--LIMIT").
        [void]$Unknown.Add($a)
    }
}
# After the loop, so a bad value on a known flag is reported first — argparse
# type-checks during parsing and reports unrecognized args only afterwards.
if ($Unknown.Count -gt 0) { ArgError "unrecognized arguments: $($Unknown -join ' ')" }
if ($null -ne $Tail -and -not $Pick.Trim()) {
    ArgError "--tail needs --pick <id> to say which session to read"
}

$root = $env:FIND_SESSION_ROOT
if (-not $root) { $root = Join-Path (Join-Path $HOME ".claude") "projects" }
# Path treated as home for the `~` dir label — see the FIND_SESSION_HOME note in
# the header for why this can't just read $HOME.
$HomeDir = $env:FIND_SESSION_HOME
if (-not $HomeDir) { $HomeDir = $HOME }
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
# Notices the harness injects as if they were user messages - "[Request
# interrupted by user for tool use]" and friends (13 of them in the reference
# store). They read as conversation but nobody typed them, so --tail drops them.
# Not folded into $WrapperRe: that one also gates the *title* fallback, where an
# interrupted session should still fall through to real prose rather than being
# treated as contentless.
$NoticeRe = '^\[Request interrupted by user[^\]]*\]$'
# Per-message cap for --tail. Generous, because asking for N messages is opting
# in to the cost, but bounded so one long answer can't dominate the output.
$TailMaxChars = 1200
# Record timestamps look like 2026-07-29T19:38:58.753Z - fixed width, UTC, always
# exactly three fractional digits (all 5341 in the reference store matched). That
# fixed width plus the trailing Z is what makes an ORDINAL STRING comparison
# chronologically correct, which lets the scan track the maximum without parsing
# every record. Anything not matching exactly is ignored, so a format change
# degrades to file mtime rather than sorting wrongly - variable fractional widths
# would break string ordering ("...58.7Z" sorts below "...58Z").
#
# Applied to the record's OWN timestamp field. An earlier version regex-scanned
# the raw line instead, to dodge the ConvertFrom-Json inconsistency described at
# the extraction site - that was wrong: some records carry a nested "timestamp"
# inside a sub-object while having no top-level one of their own, and a raw scan
# picks those up. It made one real session report 12:30 instead of 12:29 on
# PowerShell only, because a nested 16:30:00.219Z outranked the true top-level
# maximum of 16:29:32.732Z.
$TsRe = '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$'
# 'T' and 'Z' are quoted so they're literals, not format specifiers.
$TsFmt = "yyyy-MM-dd'T'HH:mm:ss.fff'Z'"
$TsStyles = [System.Globalization.DateTimeStyles]::AssumeUniversal -bor `
            [System.Globalization.DateTimeStyles]::AdjustToUniversal
$Invariant = [System.Globalization.CultureInfo]::InvariantCulture
# Display format is applied with InvariantCulture on purpose: in a .NET custom
# format string ':' means "the culture's time separator", so a locale that uses
# '.' would render 09.57 where Python's strftime always renders 09:57.
$OutFmt = "yyyy-MM-dd HH:mm"

# One "now" for the whole run, so two relative bounds can't be anchored a few
# milliseconds apart. FIND_SESSION_NOW (epoch ms) overrides it so tests of a
# relative window are deterministic; see the test hooks in the header.
if ($env:FIND_SESSION_NOW) {
    $parsedNow = [long]0
    if (-not [long]::TryParse($env:FIND_SESSION_NOW, [System.Globalization.NumberStyles]::Integer,
                              $Invariant, [ref]$parsedNow)) {
        ArgError "FIND_SESSION_NOW must be epoch milliseconds, got '$($env:FIND_SESSION_NOW)'"
    }
    $NowMs = $parsedNow
} else {
    $NowMs = [datetimeoffset]::UtcNow.ToUnixTimeMilliseconds()
}

# No month unit: "m" would be ambiguous between minutes and months, and a
# calendar month isn't a fixed number of milliseconds anyway.
$RelRe = '^(\d+)([hdw])$'
$DateRe = '^\d{4}-\d{2}-\d{2}$'
$UnitMs = @{ h = 3600000L; d = 86400000L; w = 604800000L }

function TimeBound($flag, $value) {
    # Resolve a --since/--before value to epoch milliseconds, or $null.
    if ([string]::IsNullOrEmpty($value)) { return $null }
    $m = [regex]::Match($value, $RelRe)
    if ($m.Success) {
        return $NowMs - ([long]$m.Groups[1].Value * $UnitMs[$m.Groups[2].Value])
    }
    if ($value -match $DateRe) {
        try {
            # Kind stays Unspecified, so the [datetimeoffset] cast reads it as
            # LOCAL midnight - matching the lastActive column, also rendered in
            # local time. A user asking for "since 2026-07-01" means their own
            # July 1st. Same as Python's naive datetime.timestamp().
            $d = [datetime]::ParseExact($value, "yyyy-MM-dd", $Invariant)
        } catch {
            ArgError "argument $flag`: '$value' is not a real date"
        }
        return ([datetimeoffset]$d).ToUnixTimeMilliseconds()
    }
    ArgError "argument $flag`: expects a relative age (7d, 12h, 2w) or a date (YYYY-MM-DD), got '$value'"
}

$SinceMs = TimeBound "--since" $Since
$BeforeMs = TimeBound "--before" $Before

function Clean($s) {
    # Collapse whitespace so multi-line prompts render on one line.
    if ([string]::IsNullOrEmpty($s)) { return $null }
    return ($s -replace '\s+', ' ').Trim()
}

function Truncate($s, $n) {
    if ($s.Length -gt $n) { return $s.Substring(0, $n) + "..." }
    return $s
}

function NormBranch($branch) {
    # A real branch name, or "" when the session wasn't on one.
    #
    # "HEAD" is what gets recorded when there's no named branch - 52 of the 55
    # HEAD values in the reference store were simply sessions outside a git repo
    # (the other 3 were detached, which is indistinguishable from here).
    # Normalised away once, at the source, so the same value feeds both the dir
    # column and the search haystack:
    #
    #   - as a column it would otherwise print a meaningless "HEAD" on ~90% of
    #     rows, which is exactly the low-signal problem showing the branch was
    #     meant to fix
    #   - in the haystack it made the query "head" match almost every session at
    #     score 1, burying real hits
    $b = Clean $branch
    if (-not $b -or $b -ceq "HEAD") { return "" }
    return $b
}

function UserText($rec) {
    # Conversation text a user/assistant record carries, or $null for tool
    # results, injected context and subagent sidechains. Returns wrapper text
    # as-is; callers decide whether to keep it. The title fallback needs the
    # wrapper text through so it can pull a slash-command name out of it.
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

function TranscriptText($rec) {
    # Text for --tail, or $null if this record isn't part of the conversation.
    # Stricter than UserText: the injected wrappers and harness notices are
    # dropped rather than passed through, because a transcript should show what
    # was actually said, not the plumbing around it.
    $text = UserText $rec
    if (-not $text) { return $null }
    if ($text -match $WrapperRe -or $text -match $NoticeRe) { return $null }
    return $text
}

$sessions = [System.Collections.ArrayList]::new()
if (Test-Path $root) {
    # Resumable sessions live exactly one level down: projects\<dir>\<id>.jsonl.
    # Do NOT recurse deeper — nested subagents\ and workflows\ folders hold
    # transcript artifacts (e.g. journal.jsonl) that aren't resumable sessions.
    # $ErrorActionPreference is Stop, so an unlistable directory would otherwise
    # abort the whole scan and print nothing. Skip what can't be read.
    try { $projDirs = @(Get-ChildItem -Path $root -Directory) } catch { $projDirs = @() }
    foreach ($projDir in $projDirs) {
        try { $projFiles = @(Get-ChildItem -Path $projDir.FullName -Filter *.jsonl -File) }
        catch { continue }
        foreach ($file in $projFiles) {
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
            $tsMax = $null
            # Only collected when --tail asked for it, which implies --pick, so
            # exactly one file reaches here - no reason to hold transcripts for
            # every session in the store.
            $msgs = $null
            if ($null -ne $Tail) { $msgs = [System.Collections.ArrayList]::new() }
            # ReadLines throws on a file held without sharing by another process,
            # a permission-denied file, or an unhydrated OneDrive placeholder.
            # With $ErrorActionPreference = Stop that terminated the entire scan
            # — and because rows are emitted once at the very end, the user got
            # zero rows plus a .NET stack trace instead of a listing. Skip the
            # file instead, matching scan-sessions.py's `except OSError: continue`.
            # Flagged rather than `continue`-ing from the catch, so the control
            # flow is explicit about which loop it leaves.
            $readFailed = $false
            try {
                foreach ($line in [System.IO.File]::ReadLines($file.FullName)) {
                    if ([string]::IsNullOrWhiteSpace($line)) { continue }
                    try { $rec = $line | ConvertFrom-Json } catch { continue }
                    $t = $rec.type
                    # Titles/prompts are re-emitted as the session grows — keep last.
                    if ($t -eq "ai-title" -and $rec.aiTitle) { $title = $rec.aiTitle }
                    # A last-prompt record comes in two shapes: one carries the
                    # text inline as `lastPrompt`, the other is just a `leafUuid`
                    # pointer with no text at all. The pointer form leaves us
                    # nothing to show, so track the last real user message as a
                    # fallback — otherwise those sessions list as "(untitled)"
                    # with an empty preview and can never match a query.
                    if ($t -eq "last-prompt" -and $rec.lastPrompt) { $prompt = $rec.lastPrompt }
                    if ($null -ne $msgs -and ($t -eq "user" -or $t -eq "assistant")) {
                        $tt = TranscriptText $rec
                        if ($tt) { [void]$msgs.Add(@($t, $tt)) }
                    }
                    if ($t -eq "user") {
                        $ut = UserText $rec
                        if ($ut) {
                            $m = [regex]::Match($ut, $CommandRe, 'IgnoreCase')
                            if ($m.Success) { $fallbackCmd = $m.Groups[1].Value }
                            elseif ($ut -notmatch $WrapperRe) { $fallback = $ut }
                        }
                    }
                    # Take the LAST cwd, not the first: a /cd relocates the
                    # session, and we want the directory it ended up in (the
                    # resumable one), not the dead-end origin it started in.
                    if ($rec.cwd) { $cwd = $rec.cwd }
                    # A relocation records its destination explicitly — trust it
                    # over any transient trailing cwd.
                    if ($t -eq "relocated" -and $rec.relocatedCwd) { $relocated = $rec.relocatedCwd }
                    # LAST branch, not the first, to match how cwd is taken: a
                    # session that started on master and moved to a feature
                    # branch reports the branch it ended on. Previously
                    # first-non-empty-wins, which answered "where did this work
                    # start" while cwd answered "where did it end up" - the two
                    # are now consistent. A $null test rather than truthiness, so
                    # moving out of a repo (which records an empty branch) does
                    # clear it, while the many records that omit the key don't.
                    if ($null -ne $rec.gitBranch) { $branch = $rec.gitBranch }
                    # MAXIMUM timestamp, not the last one seen: 57 of 73 sessions
                    # in the reference store carry out-of-order timestamps, and in
                    # 8 of them the final record is not the latest. Taking the
                    # last would report those sessions as older than they are.
                    #
                    # ConvertFrom-Json is NOT consistent between hosts: Windows
                    # PowerShell 5.1 leaves an ISO-8601 string as [string], while
                    # PowerShell 7 coerces it to [datetime] (Kind=Utc) and throws
                    # the original text away. A bare `-is [string]` test therefore
                    # passed on 5.1 and failed on 7, silently sending every
                    # session down the mtime fallback on 7 only.
                    #
                    # Where 7 coerced, re-derive the canonical text and require
                    # that the line actually contained it. That keeps the shape
                    # gate meaning the same thing on both hosts: a non-canonical
                    # source form (say a +00:00 offset) round-trips into canonical
                    # text and would otherwise be accepted here while
                    # scan-sessions.py's strict string test rejects it. Looking
                    # for this record's own canonical value - not any timestamp on
                    # the line - is what keeps nested timestamps out of it.
                    $ts = $rec.timestamp
                    if ($ts -is [datetime]) {
                        $cand = $ts.ToUniversalTime().ToString($TsFmt, $Invariant)
                        if ($line.Contains('"' + $cand + '"')) { $ts = $cand } else { $ts = $null }
                    }
                    # CompareOrdinal, not -gt: PowerShell's string operators are
                    # culture-aware, and Python's `>` is ordinal. Same trap as the
                    # sortKey dash-stripping in SKILL.md.
                    if ($ts -is [string] -and $ts -match $TsRe) {
                        if ($null -eq $tsMax -or [string]::CompareOrdinal($ts, $tsMax) -gt 0) {
                            $tsMax = $ts
                        }
                    }
                }
            } catch { $readFailed = $true }
            if ($readFailed) { continue }

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
            $br = NormBranch $branch

            # "Last active" is the newest conversation record, not the file's
            # mtime. mtime is reset by anything that rewrites the file without
            # advancing the conversation - sync clients, backup restores, a
            # `cp -r` of ~/.claude - and it also moves when Claude Code appends
            # records that carry no timestamp of their own (ai-title, last-prompt,
            # mode, file-history-snapshot). In the reference store mtime ran up to
            # 168 h ahead of the last real activity, which scrambles a
            # recency-ordered listing. mtime stays as the fallback for the rare
            # session with no usable timestamp (1 of 73 there).
            $activeMs = $null
            $lastActive = $null
            if ($tsMax) {
                try {
                    $dt = [datetime]::ParseExact($tsMax, $TsFmt, $Invariant, $TsStyles)
                    $activeMs = ([datetimeoffset]$dt).ToUnixTimeMilliseconds()
                    # Local time, because the mtime it replaces was local - the
                    # column would otherwise silently shift by the UTC offset.
                    $lastActive = $dt.ToLocalTime().ToString($OutFmt, $Invariant)
                } catch {
                    # Matched the shape but isn't a real date; fall through.
                    $activeMs = $null
                }
            }
            if ($null -eq $activeMs) {
                $activeMs = ([datetimeoffset]$file.LastWriteTimeUtc).ToUnixTimeMilliseconds()
                $lastActive = $file.LastWriteTime.ToString($OutFmt, $Invariant)
            }

            [void]$sessions.Add([pscustomobject]@{
                id         = $id
                shortId    = $id.Substring(0, $IdLen)
                # Deterministic tie-break for equal activeMs. Dashes stripped so
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
                lastActive = $lastActive
                # Sort key: epoch milliseconds of the newest conversation record
                # (or of mtime when there is none). Named for what it is - it is
                # no longer the file's mtime.
                activeMs   = $activeMs
                msgs       = $msgs
                sizeKB     = [math]::Round($file.Length / 1KB, 1)
            })
        }
    }
}

# All output goes through this so PowerShell emits LF-only (its default string
# output is CRLF) — byte-identical to the Python script, and no stray \r to
# break a pasted resume command.
function Emit($text) { [Console]::Out.Write($text + "`n") }

function ResumeCommand($s) {
    # TWO lines, not `cd … && claude …`. A one-liner needs a separator, and no
    # separator works in every shell a user might paste into: `&&` is a parse
    # error in Windows PowerShell 5.1 — the shell this skill deliberately targets
    # because it's always present — and `;` isn't a separator in cmd, where it
    # becomes a literal argument to `cd`. Two lines run sequentially in bash,
    # PowerShell 5.1, PowerShell 7 and cmd alike.
    #
    # Losing `&&` means losing its short-circuit, so `claude` would run even if
    # the `cd` failed. EmitResume covers that by refusing to print a command
    # whose directory doesn't exist — the only common reason the `cd` fails.
    #
    # (cmd still needs `cd /d` to change drive as well as directory. That can't
    # be expressed portably — `/d` is a bad argument to PowerShell's Set-Location
    # alias — so a cmd user crossing drives has to add it. PowerShell, where `cd`
    # switches drive on its own, is the realistic paste target on Windows.)
    return 'cd "' + $s.cwd + '"' + "`n" + 'claude --resume ' + $s.id
}

function CopyToClipboard($text) {
    # Best-effort: returns $null on success, else a short reason. Never fatal —
    # failing to copy shouldn't cost the user the command, which is printed too.
    # Set-Clipboard ships with Windows PowerShell 5.1 but does not exist on
    # PowerShell 7 for Linux, where the CommandNotFoundException lands here.
    # stdout stays LF for parity with scan-sessions.py, but the CLIPBOARD gets
    # CRLF: that text is going into a terminal paste, and Windows expects CRLF.
    # Set-Clipboard stores whatever it's given, while clip.exe (the Python path)
    # converts on its own, so without this the two scripts disagreed on the
    # clipboard's bytes. Set-Clipboard is Windows-only, so CRLF is always right
    # by the time we get here.
    try {
        Set-Clipboard -Value ($text -replace "`n", "`r`n") -ErrorAction Stop
        return $null
    } catch {
        return $_.Exception.Message
    }
}

function EmitTail($s) {
    # Print the last N exchanges, oldest first.
    #
    # Counts EXCHANGES, not messages. Assistant records outnumber user records
    # 3.3:1 in the reference store, and 16 of 61 sessions contain a run of 4+
    # consecutive assistant messages (longest: 34), so "last N messages"
    # routinely returned nothing but Claude's own tool-call narration with the
    # question that prompted it scrolled off. An exchange is one user message
    # plus everything that followed it, so `--tail 3` means "the last 3 things I
    # asked".
    #
    # Each exchange prints the prompt and the FINAL reply to it - at most 2N
    # lines for N exchanges, so output stays predictable. The assistant messages
    # before the last one in an exchange are overwhelmingly "let me check X"
    # narration around tool calls; the last one carries the conclusion.
    #
    # Deliberately does NOT check that the session's directory still exists,
    # unlike EmitResume: a session whose project folder was deleted is exactly
    # the one you can't resume and most need to read. Reading is always safe.
    #
    # One message per line as `role<TAB>text`, matching the tab-separated,
    # one-record-per-line shape of the listing. Whitespace is collapsed, so
    # embedded code blocks lose their line breaks - this is for recovering what
    # was decided, not for reconstructing a file.
    #
    # Assistant messages before the first user message belong to no exchange and
    # are dropped; sessions open with a user turn.
    $exchanges = [System.Collections.ArrayList]::new()
    foreach ($m in @($s.msgs)) {
        if ($m[0] -eq "user") {
            [void]$exchanges.Add(@($m[1], $null))
        } elseif ($exchanges.Count -gt 0) {
            $exchanges[$exchanges.Count - 1][1] = $m[1]  # keep only the latest reply
        }
    }
    if ($exchanges.Count -eq 0) {
        Emit "ERROR: session $($s.shortId) ($($s.id)) has no readable exchanges"
        return
    }
    if ($Tail -le 0) { return }
    $start = [Math]::Max(0, $exchanges.Count - $Tail)
    for ($k = $start; $k -lt $exchanges.Count; $k++) {
        Emit ("user`t" + (Truncate $exchanges[$k][0] $TailMaxChars))
        if ($exchanges[$k][1]) {
            Emit ("assistant`t" + (Truncate $exchanges[$k][1] $TailMaxChars))
        }
    }
}

function EmitResume($s) {
    # Checking the directory first is what makes the two-line command safe: the
    # `cd` can't silently fail and leave `claude` to start a fresh session in the
    # wrong place. A recorded cwd goes missing when the project folder is
    # renamed, deleted, or lived on a share that isn't mounted.
    if (-not (Test-Path -LiteralPath $s.cwd -PathType Container)) {
        # ASCII only inside double-quoted literals: see the encoding trap in
        # SKILL.md. A UTF-8 em-dash here parsed as a string terminator under
        # Windows PowerShell 5.1 and took the whole script down.
        Emit "ERROR: session $($s.shortId) ($($s.id)) cannot be resumed - its recorded directory no longer exists: $($s.cwd)"
        return
    }
    $cmd = ResumeCommand $s
    Emit $cmd
    if ($CopyCmd) {
        $why = CopyToClipboard $cmd
        if ($why) { Emit "NOTE: could not copy to clipboard ($why)" }
    }
}

# --- resume mode: by id ------------------------------------------------------
# An id is resolved against every session, independent of --query/--offset/
# --limit ranking, so it can never point at the wrong row.
if ($PickById) {
    $matched = @($sessions | Sort-Object sortKey)
    if ($matched.Count -eq 1) {
        if ($null -ne $Tail) { EmitTail $matched[0] } else { EmitResume $matched[0] }
    } elseif ($matched.Count -eq 0) {
        Emit "ERROR: --pick $Pick matched no session"
    } else {
        $ids = ($matched | ForEach-Object { $_.shortId }) -join ", "
        Emit "ERROR: --pick $Pick is ambiguous ($($matched.Count) matches: $ids)"
    }
    return
}

# --- time window -------------------------------------------------------------
# Applied after the --pick <id> block above, deliberately: an id lookup is exact
# and shouldn't be second-guessed by a window the caller listed with, exactly as
# it already ignores --min-size-kb. --pick <row> ranks first, so it does see this.
# Plain arrays from here on: $sessions is only ever enumerated below, and the
# ArrayList was only for O(1) appends during the scan.
if ($null -ne $SinceMs) {
    $sessions = @($sessions | Where-Object { $_.activeMs -ge $SinceMs })
}
if ($null -ne $BeforeMs) {
    $sessions = @($sessions | Where-Object { $_.activeMs -lt $BeforeMs })
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
    # sortKey ascending breaks activeMs ties identically in both scripts; without
    # it, tied rows order by directory listing and the two drift apart.
    $ranked = $scored | Sort-Object @{e={$_.score};Descending=$true}, @{e={$_.activeMs};Descending=$true}, @{e={$_.sortKey};Descending=$false}
} else {
    $ranked = $sessions | Sort-Object @{e={$_.activeMs};Descending=$true}, @{e={$_.sortKey};Descending=$false}
}
$rankedArr = @($ranked)

# --- resume mode: by row -----------------------------------------------------
# Legacy/interactive form. Drifts if another live session's mtime reorders the
# ranking between the listing and the pick — --pick <id> is the safe handle.
if ($PickByRow) {
    $n = [int]$Pick
    if ($n -ge 1 -and $n -le $rankedArr.Count) {
        if ($null -ne $Tail) { EmitTail $rankedArr[$n - 1] } else { EmitResume $rankedArr[$n - 1] }
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
    $label = if ($s.cwd -eq $HomeDir) { "~" } else { (($s.cwd -replace '\\', '/').TrimEnd('/') -split '/')[-1] }
    # `@branch` folded into the existing dir column rather than added as a new
    # one: only ~11% of sessions have a real branch, so a separate column would be
    # empty on nearly every row while costing width on all of them.
    if ($s.branch) { $label = $label + "@" + $s.branch }
    $cols = @($s.shortId, $s.title, $label, $s.lastActive)
    if ($ShowPreview) { $cols += $s.preview }
    $cols -join "`t"
}
Emit ($lines -join "`n")
