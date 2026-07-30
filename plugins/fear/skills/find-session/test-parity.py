#!/usr/bin/env python3
"""Parity test for scan-sessions.py vs scan-sessions.ps1.

Builds a fixture session store, runs both scripts against it via
FIND_SESSION_ROOT / FIND_SESSION_HOME, and asserts they return the same rows in
the same order. Developer tool — not used by the skill at runtime.

    python3 test-parity.py            # test every available runtime
    python3 test-parity.py -v         # also print each command's output

The contract being checked is "same rows, same order" — NOT byte-identical
output. Line endings, encoding details and the trailing newline are allowed to
differ; the selected sessions, their order, and every field the user acts on
(short id, and the cwd + uuid in a resume command) are not.

HEADS UP: the --copy cases really do write to your clipboard, so running this
clobbers whatever was in it. Nothing else here touches state outside its temp
fixture directory.

Expect roughly a minute: every case runs once per available runtime, and a
PowerShell process start plus scan is ~2.5 s of that. If it needs to get faster,
cut runtimes rather than cases — the cross-runtime diff is the whole point, but
two runtimes still provide it.
"""
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = HERE / "scan-sessions.py"
PS1 = HERE / "scan-sessions.ps1"
VERBOSE = "-v" in sys.argv

# Session cwds must be REAL directories: --pick refuses to emit a resume command
# for a directory that no longer exists (BACKLOG.md B3), so a fixture of made-up
# paths would test only the error path. Set by build_fixture().
#
# FAKE_HOME is also what FIND_SESSION_HOME is set to, so the `~` dir label gets
# exercised — the branch that was previously untestable on the PowerShell side,
# because $HOME is read-only there.
FAKE_HOME = None
PROJ = None
MOVED = None
GONE = None  # deliberately never created

# Fixed "now" for --since/--before, passed via FIND_SESSION_NOW so relative
# windows resolve to the same instant in every runtime and every run. Chosen so
# the fixture's timestamps land on whole-day offsets from it: session 1 is 1 day
# old, session 2 is 2 days, and so on.
NOW_ISO = "2026-03-02T12:00:00.000Z"
NOW_MS = int(datetime.datetime.strptime(NOW_ISO, "%Y-%m-%dT%H:%M:%S.%fZ")
             .replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)


# Claude Code writes compact JSON (no space after ':'), so the fixture must too —
# otherwise it isn't testing the input shape the scripts actually meet. Session 16
# below deliberately uses the padded form to keep the PowerShell side's raw-text
# regex honest about legal JSON whitespace.
COMPACT = (",", ":")


def rec(_sep=COMPACT, **kw):
    return json.dumps(kw, ensure_ascii=False, separators=_sep)


def pad(cwd, n, ts=None, sep=COMPACT):
    """Filler to push a fixture file past the 3 KB stub threshold.

    Assistant records carry cwd (which the scanner tracks) but no title, prompt
    or user text, so they add bytes without changing any extracted field. `ts`
    stamps them so a session's timestamps aren't all on the final record.
    """
    out = []
    for _ in range(n):
        r = {"type": "assistant", "cwd": cwd,
             "message": {"role": "assistant",
                         "content": [{"type": "text", "text": "filler " * 20}]}}
        if ts:
            r["timestamp"] = ts
        out.append(json.dumps(r, ensure_ascii=False, separators=sep))
    return out


def local_of(ts):
    """The local-time string the scripts should render for a UTC timestamp.

    Computed independently here, but the cross-runtime diff is what makes it
    meaningful — PowerShell derives its own value from the same input.
    """
    dt = datetime.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ")
    return dt.replace(tzinfo=datetime.timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")


def write_session(root, proj, sid, lines, mtime):
    d = root / proj
    d.mkdir(parents=True, exist_ok=True)
    p = d / (sid + ".jsonl")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.utime(p, (mtime, mtime))
    return p


def build_fixture(root):
    """A store covering every branch the two scripts can disagree on."""
    global FAKE_HOME, PROJ, MOVED, GONE
    cwds = root.parent / "cwds"
    for name in ("home", "proj", "moved"):
        (cwds / name).mkdir(parents=True, exist_ok=True)
    FAKE_HOME = str(cwds / "home")
    PROJ = str(cwds / "proj")
    MOVED = str(cwds / "moved")
    GONE = str(cwds / "deleted-project")  # never created, on purpose

    enc = "C--fixture-proj"

    # Ordering comes from record TIMESTAMPS, not file mtime (BACKLOG.md B1). To
    # prove that, every session's mtime is set in the OPPOSITE order to its
    # timestamps: MTIME_INVERTED counts UP as the timestamps count DOWN, so a
    # regression to mtime-based sorting reverses the whole listing.
    #
    # mtime epochs here are in 2023 (~1.70e12 ms) while the timestamps are in
    # 2026 (~1.77e12 ms), so the two mtime-fallback sessions at the end sort
    # below every timestamped one — a fixed, checkable position.
    mt = iter(range(1_700_000_010, 1_700_000_600, 50))

    # 1. Ordinary session: aiTitle wins over everything below it. Newest
    #    timestamp, OLDEST mtime -> must rank first.
    write_session(root, enc, "aaaaaaaa-0000-0000-0000-000000000001", [
        rec(type="user", cwd=PROJ, gitBranch="master",
            timestamp="2026-03-01T11:00:00.000Z",
            message={"role": "user", "content": "first prompt about netsuite"}),
        *pad(PROJ, 20, "2026-03-01T11:30:00.000Z"),
        rec(type="user", cwd=PROJ, gitBranch="feature/x",
            timestamp="2026-03-01T12:00:00.000Z",
            message={"role": "user", "content": "commit it"}),
        # A record with NO top-level timestamp but a nested one, dated far in the
        # future. Real sessions contain exactly this shape, and it is what broke
        # an earlier PowerShell implementation that regex-scanned the raw line:
        # the nested value outranked the true maximum, so lastActive jumped
        # forward on PowerShell only. Both scripts must read the record's OWN
        # timestamp field and ignore this entirely.
        rec(type="file-history-snapshot", messageId="m1",
            snapshot={"timestamp": "2026-12-01T00:00:00.000Z",
                      "trackedFileBackups": {}}),
        rec(type="ai-title", aiTitle="Ordinary titled session"),
        rec(type="last-prompt", lastPrompt="commit it"),
    ], next(mt))

    # 2. leafUuid-only last-prompt and no aiTitle -> title falls back to the
    #    last real user prose, and must be truncated at 80 chars.
    long_prose = "deliberately long user prose " * 6
    write_session(root, enc, "bbbbbbbb-0000-0000-0000-000000000002", [
        *pad(PROJ, 20, "2026-02-28T11:00:00.000Z"),
        rec(type="user", cwd=PROJ, timestamp="2026-02-28T12:00:00.000Z",
            message={"role": "user", "content": long_prose}),
        rec(type="last-prompt", leafUuid="dead-beef"),
    ], next(mt))

    # 3. cwd == home -> the `~` dir label. gitBranch "HEAD" is the placeholder
    #    recorded when there is no named branch, so it must NOT appear in the dir
    #    column (it would otherwise show on ~90% of real rows) and must not be
    #    searchable.
    write_session(root, "C--fixture-home", "cccccccc-0000-0000-0000-000000000003", [
        *pad(FAKE_HOME, 20, "2026-02-27T12:00:00.000Z"),
        rec(type="user", cwd=FAKE_HOME, gitBranch="HEAD",
            timestamp="2026-02-27T12:00:00.000Z",
            message={"role": "user", "content": "no repo here"}),
        rec(type="ai-title", aiTitle="Session in the home directory"),
        rec(type="last-prompt", lastPrompt="what did we decide"),
    ], next(mt))

    # 4. Two things at once: the relocation destination must win over the
    #    trailing cwd (and its record sits far from EOF on purpose — see P1), AND
    #    the timestamps are OUT OF ORDER with the newest in the middle. 57 of 73
    #    sessions in the real store look like this, so "last timestamp" would
    #    report this session ~2 months stale. The max is 2026-02-26T12:00Z; the
    #    final record is 2026-01-01T00:00Z.
    write_session(root, enc, "dddddddd-0000-0000-0000-000000000004", [
        rec(type="user", cwd=PROJ, timestamp="2026-02-26T09:00:00.000Z",
            message={"role": "user", "content": "start here"}),
        rec(type="relocated", relocatedCwd=MOVED),
        *pad(PROJ, 20, "2026-02-26T12:00:00.000Z"),
        *pad(PROJ, 20, "2026-01-01T00:00:00.000Z"),
        rec(type="ai-title", aiTitle="Relocated session"),
    ], next(mt))

    # 5+6. Identical max timestamp -> the uuid tie-break decides, and both
    #      scripts must pick the same winner. Written in reverse id order so a
    #      script that falls back to directory/filename order gets it wrong.
    for sid in ("ffffffff-0000-0000-0000-000000000006",
                "eeeeeeee-0000-0000-0000-000000000005"):
        write_session(root, enc, sid, [
            *pad(PROJ, 20, "2026-02-25T12:00:00.000Z"),
            rec(type="ai-title", aiTitle="Tie-break " + sid[:8]),
        ], next(mt))

    # 7. Contentless stub, under the 3 KB threshold: hidden by default, shown
    #    with --min-size-kb 0, and always visible to --pick.
    write_session(root, enc, "99999999-0000-0000-0000-000000000007", [
        rec(type="user", cwd=PROJ, timestamp="2026-02-24T12:00:00.000Z",
            message={"role": "user", "content": "hi"}),
    ], next(mt))

    # 8. Subagent sidechain: must be excluded everywhere. Given the NEWEST
    #    timestamp of all, so a leak would show up at the top of every listing.
    write_session(root, enc, "agent-12345678-0000-0000-0000-000000000008", [
        *pad(PROJ, 20, "2026-12-31T23:59:59.999Z"),
        rec(type="ai-title", aiTitle="SHOULD NEVER APPEAR"),
    ], next(mt))

    # 9. Slash-command-only session: title falls back to the command name.
    write_session(root, enc, "77777777-0000-0000-0000-000000000009", [
        *pad(PROJ, 20, "2026-02-23T12:00:00.000Z"),
        rec(type="user", cwd=PROJ, timestamp="2026-02-23T12:00:00.000Z",
            message={"role": "user",
                     "content": "<command-name>/fear:find-session</command-name>"}),
    ], next(mt))

    # 10. cwd that no longer exists: must still LIST (the session is real and
    #     its content is intact) but --pick must refuse to emit a resume command
    #     that would `cd` into nothing.
    write_session(root, enc, "88888888-0000-0000-0000-000000000010", [
        *pad(GONE, 20, "2026-02-22T12:00:00.000Z"),
        rec(type="ai-title", aiTitle="Session in a deleted directory"),
    ], next(mt))

    # 11+12. Shared id prefix, so `--pick ab` is ambiguous and must be refused
    #        rather than resolved to whichever happened to sort first. ("ab" is
    #        not 1-4 digits, so it's read as a prefix, not a row number.)
    for n, ts in (("1", "2026-02-21T12:00:00.000Z"), ("2", "2026-02-20T12:00:00.000Z")):
        write_session(root, enc, "ab%s11111-0000-0000-0000-00000000001%s" % (n, n), [
            *pad(PROJ, 20, ts),
            rec(type="ai-title", aiTitle="Ambiguous prefix " + n),
        ], next(mt))

    # 13. No timestamps at ALL -> must fall back to file mtime. 1 of 73 sessions
    #     in the real store is like this.
    write_session(root, enc, "11111111-0000-0000-0000-000000000013", [
        *pad(PROJ, 20),
        rec(type="ai-title", aiTitle="No timestamps anywhere"),
    ], 1_700_000_002)

    # 14. Timestamps that match the expected SHAPE but aren't real dates (month
    #     13, day 45). Must be rejected and fall back to mtime, not crash and not
    #     sort as though the date were valid.
    write_session(root, enc, "22222222-0000-0000-0000-000000000014", [
        *pad(PROJ, 20, "2026-13-45T99:00:00.000Z"),
        rec(type="ai-title", aiTitle="Impossible timestamp"),
    ], 1_700_000_001)

    # 15. A valid ISO-8601 timestamp in a NON-canonical form (+00:00 offset
    #     instead of Z). Nothing in the real store looks like this — all 5341
    #     timestamps end in Z — but this pins the behaviour if Claude Code's
    #     format ever changes: both scripts must treat it the same way, i.e. fall
    #     back to mtime rather than one accepting it and the other not.
    #
    #     This is the case that would break a $rec.timestamp-based read on
    #     PowerShell 7, where ConvertFrom-Json coerces it to a [datetime] that
    #     round-trips into canonical form and gets silently accepted, while
    #     Python and PowerShell 5.1 reject the raw string.
    write_session(root, enc, "33333333-0000-0000-0000-000000000015", [
        *pad(PROJ, 20, "2026-02-19T12:00:00.000+00:00"),
        rec(type="ai-title", aiTitle="Non-canonical timestamp form"),
    ], 1_700_000_000)

    # 16. Same records, but written as PADDED JSON ("timestamp": "…" with a space
    #     after the colon, which JSON permits). Claude Code writes compact JSON,
    #     so this never occurs in practice — it's here because the PowerShell side
    #     reads timestamps out of the raw line text rather than the parsed record,
    #     and a regex that assumed no whitespace passed against a real store while
    #     silently failing on anything formatted differently. Must rank by its
    #     timestamp, exactly like the compact sessions do.
    padded = (", ", ": ")
    write_session(root, enc, "44444444-0000-0000-0000-000000000016", [
        *pad(PROJ, 20, "2026-02-18T12:00:00.000Z", sep=padded),
        rec(_sep=padded, type="ai-title", aiTitle="Padded JSON formatting"),
    ], 1_699_999_999)


def works(exe, probe):
    """Whether `exe` is real and runnable.

    shutil.which() is not enough on Windows: the Store's `python3` App Execution
    Alias resolves on PATH but exits 9009 when run. SKILL.md avoids it by never
    calling python on Windows; this test has to detect it instead.
    """
    if not shutil.which(exe):
        return False
    try:
        return subprocess.run([exe] + probe, capture_output=True,
                              timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def runtimes():
    out = []
    # `python` first: on Windows a real install is usually `python`, while
    # `python3` is often the alias stub above.
    for exe in ("python", "python3"):
        if works(exe, ["--version"]):
            out.append(("py:" + exe, [exe, str(PY)]))
            break
    for exe in ("pwsh", "powershell"):
        if works(exe, ["-NoProfile", "-Command", "exit 0"]):
            out.append(("ps:" + exe, [exe, "-NoProfile", "-File", str(PS1)]))
    return out


def run(cmd, args, root):
    env = dict(os.environ)
    env["FIND_SESSION_ROOT"] = str(root)
    env["FIND_SESSION_HOME"] = FAKE_HOME
    env["FIND_SESSION_NOW"] = str(NOW_MS)
    # Any real session id would be excluded as "the current session"; the
    # fixture has none, but clear it so a real value can't leak in.
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    p = subprocess.run(cmd + args, capture_output=True, env=env)
    text = p.stdout.decode("utf-8", errors="replace")
    # Normalise only what the relaxed contract permits to differ.
    rows = [ln.rstrip("\r") for ln in text.split("\n")]
    while rows and not rows[-1]:
        rows.pop()
    return rows, p.returncode, p.stderr.decode("utf-8", errors="replace")


# (label, args, mode). Modes:
#
#   "strict"   — exit code and every row must match exactly.
#   "rejected" — both must refuse the input: same exit code, same number of
#                rows, all of them ERROR lines. Wording is NOT compared —
#                that's cosmetic under the relaxed contract, and argparse's
#                phrasing isn't worth reimplementing verbatim in PowerShell.
#                Whether an input is *accepted at all* is not cosmetic, which is
#                what caught `--lim 3`, so that part stays strict.
#   "copy"     — as strict, but NOTE: lines are dropped first. Whether a
#                clipboard tool exists is a property of the machine, not of the
#                script, and the two runtimes reach the clipboard differently
#                (Set-Clipboard vs clip/pbcopy/xclip). The command itself must
#                still match exactly, and must be unaffected by --copy.
CASES = [
    ("default listing", [], "strict"),
    ("include stubs", ["--min-size-kb", "0"], "strict"),
    ("with preview", ["--preview"], "strict"),
    ("query hit", ["--query", "session"], "strict"),
    ("query multi-term", ["--query", "relocated session"], "strict"),
    ("query miss", ["--query", "zzzznomatch"], "strict"),
    ("paged", ["--limit", "2", "--offset", "2"], "strict"),
    ("limit zero", ["--limit", "0"], "strict"),
    ("fractional min-size", ["--min-size-kb", "3.5"], "strict"),
    ("pick by id", ["--pick", "dddddddd"], "strict"),
    ("pick hidden stub by id", ["--pick", "99999999"], "strict"),
    ("pick no match", ["--pick", "notanid"], "strict"),
    ("pick by row", ["--pick", "1"], "strict"),
    ("pick row out of range", ["--pick", "999"], "strict"),
    ("pick with missing cwd", ["--pick", "88888888"], "strict"),
    ("pick ambiguous prefix", ["--pick", "ab"], "strict"),
    ("pick with copy", ["--pick", "dddddddd", "--copy"], "copy"),
    ("copy without pick", ["--copy", "--limit", "3"], "copy"),
    # Time window. Relative bounds are pure epoch arithmetic against
    # FIND_SESSION_NOW, so these are timezone-independent; the absolute-date
    # cases resolve to LOCAL midnight, so their row sets depend on the machine's
    # zone — the cross-runtime diff still pins them, which is the point.
    ("since relative", ["--since", "3d"], "strict"),
    ("since on the boundary", ["--since", "5d"], "strict"),
    ("since hours", ["--since", "36h"], "strict"),
    ("since weeks", ["--since", "2w"], "strict"),
    ("before relative", ["--before", "7d"], "strict"),
    ("since and before", ["--since", "9d", "--before", "4d"], "strict"),
    ("empty window", ["--since", "1d", "--before", "9d"], "strict"),
    ("since absolute", ["--since", "2026-02-25"], "strict"),
    ("before absolute", ["--before", "2026-02-25"], "strict"),
    ("absolute range", ["--since", "2026-02-20", "--before", "2026-03-01"], "strict"),
    ("window plus query", ["--since", "5d", "--query", "session"], "strict"),
    ("window ignored by pick id", ["--since", "1h", "--pick", "dddddddd"], "strict"),
    ("window applies to pick row", ["--since", "3d", "--pick", "3"], "strict"),
    ("bad since unit", ["--since", "7m"], "rejected"),
    ("bad since format", ["--since", "yesterday"], "rejected"),
    ("impossible since date", ["--since", "2026-13-45"], "rejected"),
    ("bad before format", ["--before", "7"], "rejected"),
    # Argument validation: both must reject these, and accept nothing here.
    ("bad flag", ["--nope"], "rejected"),
    ("negative offset", ["--offset", "-5"], "rejected"),
    ("negative limit", ["--limit", "-1"], "rejected"),
    ("non-numeric limit", ["--limit", "abc"], "rejected"),
    ("fractional limit", ["--limit", "1.5"], "rejected"),
    ("negative min-size", ["--min-size-kb", "-1"], "rejected"),
    ("missing value", ["--limit"], "rejected"),
    ("wrong-case flag", ["--LIMIT", "3"], "rejected"),
    ("abbreviated flag", ["--lim", "3"], "rejected"),
]


def shape(rows, code, mode):
    """What must match between runtimes for this case."""
    if mode == "rejected":
        return (code, len(rows), all(r.startswith("ERROR: ") for r in rows))
    if mode == "copy":
        return (code, [r for r in rows if not r.startswith("NOTE: ")])
    return (code, rows)


class Unreadable:
    """Make one session file unreadable for the duration of the block.

    Windows: open it with dwShareMode=0, so any other open fails with "being
    used by another process" — the case that used to abort the whole PowerShell
    scan (BACKLOG.md B6). Unix: chmod 000, which is a no-op for root, so the
    check self-skips there rather than passing vacuously.
    """

    def __init__(self, path):
        self.path = path
        self.handle = None
        self.mode = None
        self.active = False

    def __enter__(self):
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            CreateFileW = ctypes.windll.kernel32.CreateFileW
            CreateFileW.restype = wintypes.HANDLE
            CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                                    wintypes.DWORD, ctypes.c_void_p,
                                    wintypes.DWORD, wintypes.DWORD,
                                    wintypes.HANDLE]
            h = CreateFileW(str(self.path), 0x80000000, 0, None, 3, 0, None)
            if h and h != ctypes.c_void_p(-1).value:
                self.handle = h
                self.active = True
        elif os.getuid() != 0:
            self.mode = self.path.stat().st_mode
            os.chmod(self.path, 0)
            self.active = True
        return self

    def __exit__(self, *exc):
        if self.handle is not None:
            import ctypes
            ctypes.windll.kernel32.CloseHandle(self.handle)
        if self.mode is not None:
            os.chmod(self.path, self.mode)
        return False


def unreadable_check(root, rts):
    """B6: one unreadable file must cost one row, not the whole listing."""
    target = root / "C--fixture-proj" / "aaaaaaaa-0000-0000-0000-000000000001.jsonl"
    before, _, _ = run(rts[0][1], [], root)
    results = []
    with Unreadable(target) as lock:
        if not lock.active:
            print("skip  unreadable file tolerated (cannot revoke read access here)")
            return 0
        for name, cmd in rts:
            rows, code, err = run(cmd, [], root)
            results.append((name, rows, code, err))

    failures = 0
    for name, rows, code, err in results:
        locked_gone = all(not r.startswith("aaaaaaaa") for r in rows)
        rest_kept = len(rows) == len(before) - 1
        clean = code == 0 and "Exception" not in err
        passed = locked_gone and rest_kept and clean
        print(("ok    " if passed else "FAIL  ")
              + "unreadable file tolerated [%s] (%d/%d rows, rc=%d)"
              % (name, len(rows), len(before) - 1, code))
        if not passed:
            failures += 1
            if not locked_gone:
                print("      locked session still listed")
            if not rest_kept:
                print("      lost other rows too — the scan aborted")
            if not clean:
                print("      " + (err.strip().splitlines() or ["nonzero exit"])[0])
    # Same rows from every runtime, not just the right count.
    if len({tuple(r[1]) for r in results}) > 1:
        print("FAIL  unreadable file: runtimes disagree on the surviving rows")
        failures += 1
    return failures


def main():
    rts = runtimes()
    names = ", ".join(n for n, _ in rts)
    print("runtimes: " + (names or "NONE"))
    if len(rts) < 2:
        print("SKIP: need at least two runtimes to compare")
        return 0

    tmp = Path(tempfile.mkdtemp(prefix="find-session-fixture-"))
    root = tmp / "projects"
    try:
        build_fixture(root)
        failures = 0
        for label, cargs, mode in CASES:
            results = {}
            for name, cmd in rts:
                rows, code, err = run(cmd, cargs, root)
                results[name] = (rows, code)
                if VERBOSE:
                    print("  [%s %s] rc=%d" % (name, label, code))
                    for r in rows:
                        print("    " + r)
                    if err.strip():
                        print("    stderr: " + err.strip().splitlines()[0])
            base_name, (base_rows, base_code) = list(results.items())[0]
            ok = True
            for name, (rows, code) in list(results.items())[1:]:
                if shape(rows, code, mode) != shape(base_rows, base_code, mode):
                    ok = False
                    failures += 1
                    print("FAIL  %-24s %s vs %s" % (label, base_name, name))
                    print("      rc %d vs %d" % (base_code, code))
                    for i in range(max(len(base_rows), len(rows))):
                        a = base_rows[i] if i < len(base_rows) else "<missing>"
                        b = rows[i] if i < len(rows) else "<missing>"
                        if a != b:
                            print("      row %d:" % (i + 1))
                            print("        %s: %r" % (base_name, a))
                            print("        %s: %r" % (name, b))
                elif rows != base_rows:
                    # Not a failure — the difference is outside the contract for
                    # this mode — but worth seeing, so drift can't hide forever.
                    extra = [r for r in rows if r not in base_rows]
                    missing = [r for r in base_rows if r not in rows]
                    print("note  %-24s %s differs: +%r -%r"
                          % (label, name, extra[:1], missing[:1]))
            if ok:
                kind = "rejected" if mode == "rejected" else "%d rows" % len(base_rows)
                print("ok    %-24s (%s)" % (label, kind))

        # Fixture-specific assertions the two-runtime diff can't catch: both
        # agreeing on the WRONG answer would still pass above.
        rows, _, _ = run(rts[0][1], [], root)
        checks = [
            ("sidechain excluded", all("SHOULD NEVER APPEAR" not in r for r in rows)),
            ("stub hidden by default", all(not r.startswith("99999999") for r in rows)),
            ("home labelled ~", any(r.split("\t")[2] == "~" for r in rows)),
            ("relocation destination shown",
             any(r.startswith("dddddddd") and r.split("\t")[2] == "moved" for r in rows)),
            ("uuid tie-break: e before f",
             [i for i, r in enumerate(rows) if r.startswith("eeeeeeee")] <
             [i for i, r in enumerate(rows) if r.startswith("ffffffff")]),
            ("prose title truncated at 80",
             any(r.split("\t")[1].endswith("...") and len(r.split("\t")[1]) == 83
                 for r in rows)),
            ("slash-command title fallback",
             any(r.split("\t")[1] == "/fear:find-session" for r in rows)),
            ("session with missing cwd still listed",
             any(r.startswith("88888888") for r in rows)),
        ]

        # B2 + E3: the branch is the LAST one seen (matching cwd), shown appended
        # to the dir column, and the "HEAD" placeholder is suppressed everywhere.
        def dircol(prefix):
            for r in rows:
                if r.startswith(prefix):
                    return r.split("\t")[2]
            return None

        checks += [
            # Session 1 starts on master and moves to feature/x. first-wins would
            # print proj@master here.
            ("branch is last-wins, not first-wins", dircol("aaaaaaaa") == "proj@feature/x"),
            ("HEAD placeholder not shown as a branch", dircol("cccccccc") == "~"),
            ("no branch means no @ suffix", dircol("bbbbbbbb") == "proj"),
            ("relocated session keeps its dir label", dircol("dddddddd") == "moved"),
            # HEAD must not be searchable either, or the query "head" matches
            # nearly every real session at score 1 and buries the real hits.
            ("HEAD is not searchable",
             run(rts[0][1], ["--query", "HEAD", "--limit", "1000"], root)[0] == []),
            # A real branch name still is.
            ("real branch is searchable",
             [r.split("\t")[0] for r in
              run(rts[0][1], ["--query", "feature/x", "--limit", "1000"], root)[0]]
             == ["aaaaaaaa"]),
        ]

        # B1: ordering and the lastActive column come from record timestamps, not
        # file mtime. Every mtime is set inversely to its timestamps, so a
        # regression to mtime reverses the listing.
        def col(prefix, i):
            for r in rows:
                if r.startswith(prefix):
                    return r.split("\t")[i]
            return None

        order = [r.split("\t")[0] for r in rows]
        checks += [
            ("timestamps beat mtime for ordering", order[0] == "aaaaaaaa"),
            ("full order follows timestamps",
             order == ["aaaaaaaa", "bbbbbbbb", "cccccccc", "dddddddd", "eeeeeeee",
                       "ffffffff", "77777777", "88888888", "ab111111", "ab211111",
                       "44444444", "11111111", "22222222", "33333333"]),
            # 44444444 uses padded JSON; it must sort by its timestamp
            # (2026-02-18) among the timestamped sessions, not fall to the mtime
            # group at the end.
            ("padded JSON timestamps still parsed",
             col("44444444", 3) == local_of("2026-02-18T12:00:00.000Z")),
            # Also pins the nested-timestamp trap: session 1 carries a nested
            # 2026-12-01 inside a file-history-snapshot record. If either script
            # reads timestamps out of the raw line instead of the record's own
            # field, this shows 2026-12-01 instead of 2026-03-01.
            ("lastActive is the timestamp, in local time",
             col("aaaaaaaa", 3) == local_of("2026-03-01T12:00:00.000Z")),
            ("nested timestamps ignored",
             col("aaaaaaaa", 3) != local_of("2026-12-01T00:00:00.000Z")),
            # The whole reason "max" beats "last": this session's final record is
            # ~2 months older than its newest one.
            ("out-of-order timestamps use the max, not the last",
             col("dddddddd", 3) == local_of("2026-02-26T12:00:00.000Z")),
            ("no-timestamp session falls back to mtime",
             col("11111111", 3) == datetime.datetime.fromtimestamp(
                 1_700_000_002).strftime("%Y-%m-%d %H:%M")),
            ("impossible timestamp falls back to mtime",
             col("22222222", 3) == datetime.datetime.fromtimestamp(
                 1_700_000_001).strftime("%Y-%m-%d %H:%M")),
            # Pins the PowerShell-7 ConvertFrom-Json date-coercion trap: if this
            # regresses, 7 accepts the +00:00 form and 5.1/Python don't.
            ("non-canonical timestamp form falls back to mtime",
             col("33333333", 3) == datetime.datetime.fromtimestamp(
                 1_700_000_000).strftime("%Y-%m-%d %H:%M")),
        ]

        # B4: the resume command must be two lines with no shell separator, so
        # it pastes cleanly into PowerShell 5.1 (no `&&`) and cmd (no `;`).
        pick, pick_rc, _ = run(rts[0][1], ["--pick", "dddddddd"], root)
        checks += [
            ("resume command is two lines", len(pick) == 2),
            ("resume line 1 is a bare cd",
             len(pick) == 2 and pick[0] == 'cd "%s"' % MOVED),
            ("resume line 2 is a bare claude --resume",
             len(pick) == 2
             and pick[1] == "claude --resume dddddddd-0000-0000-0000-000000000004"),
            ("no shell separator in resume command",
             not any(sep in r for r in pick for sep in ("&&", ";", " & "))),
            ("resume exits 0", pick_rc == 0),
        ]

        # B3: a session whose directory is gone must be refused, not handed over
        # as a command that would `cd` into nothing.
        gone, _, _ = run(rts[0][1], ["--pick", "88888888"], root)
        checks += [
            ("missing cwd refused", len(gone) == 1 and gone[0].startswith("ERROR: ")),
            ("missing cwd error names the directory",
             len(gone) == 1 and GONE in gone[0]),
            ("missing cwd emits no runnable command",
             not any(r.startswith("cd ") or r.startswith("claude ") for r in gone)),
        ]

        # E5: exact row sets for relative windows. FIND_SESSION_NOW is
        # 2026-03-02T12:00Z and the fixture's timestamps sit on whole-day offsets
        # from it, so these are exact and timezone-independent. --since is
        # inclusive and --before exclusive, which the 5d/7d pair pins directly:
        # sessions e+f sit exactly 5 days out and must be IN for `--since 5d`,
        # while 77777777 sits exactly 7 days out and must be OUT for `--before 7d`.
        def ids(args_):
            got, _, _ = run(rts[0][1], args_, root)
            return [r.split("\t")[0] for r in got]

        checks += [
            ("--since 3d", ids(["--since", "3d"]) ==
             ["aaaaaaaa", "bbbbbbbb", "cccccccc"]),
            ("--since 5d includes the boundary", ids(["--since", "5d"]) ==
             ["aaaaaaaa", "bbbbbbbb", "cccccccc", "dddddddd", "eeeeeeee", "ffffffff"]),
            ("--since 36h", ids(["--since", "36h"]) == ["aaaaaaaa"]),
            ("--since 2w keeps every timestamped session",
             ids(["--since", "2w"]) ==
             ["aaaaaaaa", "bbbbbbbb", "cccccccc", "dddddddd", "eeeeeeee", "ffffffff",
              "77777777", "88888888", "ab111111", "ab211111", "44444444"]),
            ("--before 7d excludes the boundary", ids(["--before", "7d"]) ==
             ["88888888", "ab111111", "ab211111", "44444444",
              "11111111", "22222222", "33333333"]),
            ("--since with --before", ids(["--since", "9d", "--before", "4d"]) ==
             ["eeeeeeee", "ffffffff", "77777777", "88888888", "ab111111"]),
            ("inverted window returns nothing",
             ids(["--since", "1d", "--before", "9d"]) == []),
            ("window still hides stubs",
             "99999999" not in ids(["--since", "2w"])),
            ("window plus query intersects both",
             ids(["--since", "5d", "--query", "home"]) == ["cccccccc"]),
            # An id lookup is exact, so a window it wasn't listed with must not
            # suppress it — same rule as --min-size-kb.
            ("--pick <id> ignores the window",
             ids(["--since", "1h", "--pick", "dddddddd"])[0].startswith('cd "')),
            # The half-open interval's defining property: at the same bound,
            # --since and --before partition the set exactly — no session in both,
            # none in neither. Catches an inclusivity flip on either side even if
            # the boundary happens to miss every fixture row.
            ("same bound partitions the set",
             sorted(ids(["--since", "6d", "--limit", "1000"])
                    + ids(["--before", "6d", "--limit", "1000"]))
             == sorted(ids(["--limit", "1000"]))),
        ]

        # E4: --copy must not alter the command it copies.
        copied, _, _ = run(rts[0][1], ["--pick", "dddddddd", "--copy"], root)
        checks.append(("--copy leaves the command unchanged",
                       [r for r in copied if not r.startswith("NOTE: ")] == pick))
        for name, passed in checks:
            print(("ok    " if passed else "FAIL  ") + name)
            if not passed:
                failures += 1

        failures += unreadable_check(root, rts)

        print("\n%s (%d failure%s)" % ("PASS" if not failures else "FAIL",
                                       failures, "" if failures == 1 else "s"))
        return 1 if failures else 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
