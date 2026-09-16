#!/usr/bin/env python3
"""
MyWorkDay - a native Windows desktop app (Python + pywebview) that shows your real
development activity: tickets touched, estimated worked time, and a recent-activity
timeline, all scanned live from your local git repos. No server, no account, no
artifact - everything here is computed fresh, locally, each time you refresh.

WHY PYWEBVIEW, NOT PLAIN TKINTER
    You asked for a modern, card-based look (rounded cards, colored badges, a donut
    chart, a timeline). Plain Tkinter can't render that well - no border-radius, no
    shadows, no easy charts. pywebview renders real HTML/CSS/JS in a native window
    (using Windows' built-in WebView2 control, the same engine as Edge), so the UI
    below is an actual local web page, just running in its own window instead of a
    browser tab or server.

WHAT'S REAL VS. DECORATIVE
    Every number on screen is computed from `git log` just now (see scan_and_group()
    below, identical algorithm to the web dashboard, ticket-time estimate included).
    The "Quick Actions" only include things this app can really do (rescan, open
    Jira, show a weekly rollup) - nothing here fakes a timer or a manual log-time
    form that isn't backed by real state.

INSTALL (one extra package beyond the standard library)
    pip install pywebview

RUN
    python myworkday_app.py

CONFIGURE (all optional - same env vars as the other Session Log tools)
    set REPOS_ROOT=F:\\MLProjects
    set GIT_AUTHOR_NAME=Your Full Name
    set TICKET_PREFIX=CAD
    set LOOKBACK_HOURS=168
    set DAILY_TARGET_HOURS=8
    set JIRA_BASE_URL=https://yourcompany.atlassian.net   (optional, for real ticket status + the Open Jira action)
    set JIRA_EMAIL=you@yourcompany.com
    set JIRA_API_TOKEN=....
    set BREAK_REMINDER_MINS=120   (continuous active time before a "take a break" toast; 0 disables it)

BUILD A STANDALONE .EXE (optional)
    pip install pyinstaller
    pyinstaller --onefile --windowed --name "MyWorkDay" myworkday_app.py
"""
import ctypes
import json
import os
import re
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from datetime import datetime, timedelta, timezone

import webview

try:
    from zoneinfo import ZoneInfo
    EASTERN = ZoneInfo("America/New_York")
except Exception:
    EASTERN = timezone(timedelta(hours=-4))

TICKET_PREFIX = os.environ.get("TICKET_PREFIX", "CAD")
TICKET_RE = re.compile(r"\b" + re.escape(TICKET_PREFIX) + r"-(\d+)\b")
IDLE_GAP_CAP_MINS = 90
WORKDAY_CUTOFF_HOUR = 22
DEFAULT_LOOKBACK_HOURS = int(os.environ.get("LOOKBACK_HOURS", "168"))
# Backstop for Api.refresh() - generous enough to never fire on a normal (even slow) load,
# but guarantees the page always gets an answer instead of hanging on a stuck DNS lookup.
REFRESH_TIMEOUT_SECS = 60
# Same idea for Api.refresh_commented_tickets() - its JQL search plus one comment-fetch per
# candidate issue has no bound on candidate count, and pywebview dispatches bridge calls on the
# UI thread, so without this a slow/busy Jira tenant freezes the whole window for however long
# that real work takes (confirmed live: sometimes ~10s, sometimes 100+ seconds, entirely
# dependent on candidate count and Jira response time that day). This data is a pure enhancement
# (see that method's docstring), so on timeout it's simply skipped for this refresh instead of
# blocking the window.
COMMENTED_TICKETS_TIMEOUT_SECS = 20
DAILY_TARGET_MINS = int(float(os.environ.get("DAILY_TARGET_HOURS", "8")) * 60)
BREAK_REMINDER_MINS = int(os.environ.get("BREAK_REMINDER_MINS", "120"))
IDLE_RESET_MINS = 5      # a gap this long in real mouse/keyboard input counts as "took a break"
BREAK_REMIND_REPEAT_MINS = 30  # re-nag this often if they're still going past the threshold


# ---------------------------------------------------------------- break reminder (real mouse/
# keyboard activity, system-wide - not scoped to this window, not derived from git at all)

class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def get_idle_seconds():
    """Seconds since the last system-wide mouse or keyboard input (Windows' own idle-time
    API - the same one the OS uses for screen-saver/lock timing). Returns 0.0 if unavailable."""
    try:
        info = _LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(_LASTINPUTINFO)
        ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info))
        millis = ctypes.windll.kernel32.GetTickCount() - info.dwTime
        return max(0.0, millis / 1000.0)
    except Exception:
        return 0.0


def show_windows_toast(title, message):
    """A real Windows Action Center toast, via PowerShell - no extra pip dependency.

    CreateToastNotifier(appId) never throws for an arbitrary, unregistered appId like
    "MyWorkDay" - it silently accepts the call and files the toast into notification history,
    but Windows never actually pops the banner on screen, because that appId isn't tied to any
    real registered application (a Start Menu shortcut with a matching AppUserModelID, or an
    MSIX package identity). That's exactly why break reminders never visibly showed. The
    standard workaround for a plain, non-packaged script (the same one the popular BurntToast
    PowerShell module uses) is to borrow the AUMID Windows already auto-registers for
    PowerShell's own default Start Menu shortcut - it's guaranteed to exist, at the cost of the
    toast showing PowerShell's name/icon instead of this app's."""
    aumid = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"

    def xml_escape(s):
        return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    def ps_single_quote_escape(s):
        # Inside a PowerShell single-quoted string, a literal ' must be doubled to '' - an
        # unescaped apostrophe (e.g. "You've been working...") otherwise terminates the string
        # early and corrupts the rest of the script. This was a real, confirmed bug: break
        # reminders never showed because of exactly this, logged as a PowerShell parse error
        # ("missing terminator") on every attempt.
        return s.replace("'", "''")

    safe_title = ps_single_quote_escape(xml_escape(title))
    safe_message = ps_single_quote_escape(xml_escape(message))
    ps_script = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
        "ContentType=WindowsRuntime] | Out-Null; "
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, "
        "ContentType=WindowsRuntime] | Out-Null; "
        "$t = '<toast><visual><binding template=\"ToastGeneric\">"
        f"<text>{safe_title}</text><text>{safe_message}</text>"
        "</binding></visual></toast>'; "
        "$xml = New-Object Windows.Data.Xml.Dom.XmlDocument; "
        "$xml.LoadXml($t); "
        "$toast = New-Object Windows.UI.Notifications.ToastNotification $xml; "
        f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{aumid}').Show($toast)"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            log_error(f"show_windows_toast: powershell exited {result.returncode}: {result.stderr[:500]}")
    except Exception:
        log_error("show_windows_toast failed", exc=True)


def break_reminder_loop(api):
    """Runs forever in a daemon thread. Tracks one continuous "active" stretch using real
    system-wide input (GetLastInputInfo) - a gap of IDLE_RESET_MINS with no mouse/keyboard
    input resets the stretch (that's a real break, not an assumption). Crossing
    api.break_reminder_mins of continuous activity fires a toast, repeated every
    BREAK_REMIND_REPEAT_MINS until an actual break happens. Reads api.break_reminder_mins
    fresh every iteration (rather than a fixed constant) so a change saved from the Settings
    panel - including turning it off with 0 - takes effect within a minute, no restart.

    Writes to api.break_status instead of pushing into the page via window.evaluate_js() -
    WebView2 only tolerates being touched from its own UI thread, and calling evaluate_js()
    from this background thread was a real, reproducible cause of the whole window freezing
    (Windows reports it as "not responding"). The page polls get_break_status() instead, which
    is a plain, safe attribute read - the only direction that's actually safe here is JS
    pulling from Python, never Python pushing into JS from a non-UI thread."""
    work_start = time.monotonic()
    last_notified_at = None
    while True:
        time.sleep(60)
        now = time.monotonic()
        reminder_mins = api.break_reminder_mins
        idle_secs = get_idle_seconds()
        if idle_secs >= IDLE_RESET_MINS * 60:
            work_start = now - idle_secs  # the streak actually ended when input last happened
            last_notified_at = None
            continuous_mins = 0.0
        else:
            continuous_mins = (now - work_start) / 60.0
            if reminder_mins > 0 and continuous_mins >= reminder_mins and (
                last_notified_at is None or (now - last_notified_at) >= BREAK_REMIND_REPEAT_MINS * 60
            ):
                show_windows_toast(
                    "Time for a break",
                    f"You've been working for {int(continuous_mins)} minutes straight. "
                    "Stand up, stretch, look away from the screen for a bit.",
                )
                last_notified_at = now

        api.break_status = {"continuous_mins": round(continuous_mins, 1), "reminder_mins": reminder_mins}

# Settings entered in the app's own Settings panel (Jira base URL / email / API token, Google
# OAuth client/secret/refresh token) are persisted here. This holds real secrets tied to this
# machine, so it belongs under %LOCALAPPDATA% like any other local app config - not roamed,
# and not sitting in the install/script folder where it'd be easy to accidentally ship or sync.
_CONFIG_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "MyWorkDay")
os.makedirs(_CONFIG_DIR, exist_ok=True)
CONFIG_PATH = os.path.join(_CONFIG_DIR, "config.json")

# One-time migration from the old next-to-the-script/exe location, so existing Jira/Google
# settings aren't lost when this ships.
_OLD_APP_DIR = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
_OLD_CONFIG_PATH = os.path.join(_OLD_APP_DIR, ".myworkday_config.json")
if not os.path.exists(CONFIG_PATH) and os.path.exists(_OLD_CONFIG_PATH):
    try:
        import shutil
        shutil.copy2(_OLD_CONFIG_PATH, CONFIG_PATH)
    except Exception:
        pass


ERROR_LOG_PATH = os.path.join(_CONFIG_DIR, "error.log")
_ERROR_LOG_MAX_BYTES = 2 * 1024 * 1024  # rotate before this becomes unbounded on a bad day


def log_error(context, exc=None):
    """Appends a timestamped entry to error.log - this is the one place both Python-side
    exceptions and JS-side failures (via Api.log_client_error) end up, so a hard-to-reproduce
    failure leaves a real record instead of only a silently-swallowed retry."""
    try:
        if os.path.exists(ERROR_LOG_PATH) and os.path.getsize(ERROR_LOG_PATH) > _ERROR_LOG_MAX_BYTES:
            os.replace(ERROR_LOG_PATH, ERROR_LOG_PATH + ".old")
        with open(ERROR_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"\n[{datetime.now().isoformat(timespec='seconds')}] {context}\n")
            if exc is not None:
                traceback.print_exc(file=f)
    except Exception:
        pass  # logging must never itself break the app


def load_config():
    try:
        # utf-8-sig tolerates a leading UTF-8 BOM (and behaves identically to utf-8 without
        # one) - Notepad, and PowerShell's own `Set-Content -Encoding utf8`, both write a BOM
        # by default, which plain "utf-8" doesn't skip. json.load() then fails to parse at all,
        # and this silently falls back to {} - every saved setting (Jira/Google/team/break
        # reminder) reads back as unset, with no visible error anywhere. Confirmed happening for
        # real via one such external edit.
        with open(CONFIG_PATH, encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg):
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, CONFIG_PATH)


def apply_config_to_env(cfg):
    # A value saved via the Settings panel wins over whatever's in the environment.
    for key in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN",
                "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN"):
        if cfg.get(key):
            os.environ[key] = cfg[key]


apply_config_to_env(load_config())


# ---------------------------------------------------------------- git + parsing (same
# algorithm as session_log_desktop.py / session_worklog_sync.py - see those files' own
# comments for why each step is done this way, in particular the instant-based sort).

_MERGE_INTO_RE = re.compile(r"^merged? .+ into .+$")


def classify(subject):
    s = subject.lower()
    if s.startswith('revert "'):
        return "revert"
    if s.startswith("merged in "):
        return "pr-merge"
    # git's own auto-generated merge subject is always "Merge(d) <branch/ref> into <branch>" -
    # matching that shape (rather than a fixed list of branch names) catches every repo's own
    # naming convention ("Merge remote-tracking branch 'origin/x' into y", "Merge WashCentral-0.2
    # into x", "Merge master into CAD-7405", ...) without misclassifying a real authored commit
    # that merely mentions "merge" as a verb (no "into <branch>" clause).
    if _MERGE_INTO_RE.match(s):
        return "base-merge"
    return "commit"


def discover_repos(root):
    repos = []
    for dirpath, dirnames, _ in os.walk(root):
        depth = dirpath[len(root):].count(os.sep)
        if ".git" in dirnames:
            repos.append(dirpath)
            dirnames[:] = []
            continue
        if depth >= 2:
            dirnames[:] = []
    return repos


def git_log(repo, author, since_iso):
    # stdin=DEVNULL matters more than it looks: a --windowed PyInstaller build launched the
    # normal way (double-click, Start Menu, desktop shortcut - no console at all) gives every
    # child process an invalid/closed stdin handle, not a clean "nothing to read" one. If any of
    # these git calls ever hits something that tries to read from stdin (an expired cached
    # credential re-prompting, a credential helper, an SSH host-key check on a repo whose remote
    # changed), it can stall for the full 30s timeout below instead of failing instantly - and
    # since scan() fans this out across every discovered repo, a handful of stalls compounds into
    # exactly the "sometimes fast, sometimes slow to load" symptom this was actually causing.
    # Launching from a real terminal (as during dev/test) inherits a valid stdin, which is why
    # this never showed up in a terminal-launched test - only in a real installed launch.
    try:
        out = subprocess.run(
            ["git", "-C", repo, "log", "--all", "--author", author,
             "--since", since_iso, "--date=iso-strict",
             "--pretty=format:%H|%aI|%s"],
            capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        return []
    if out.returncode != 0:
        return []
    rows = []
    for line in out.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("|", 2)
        if len(parts) == 3:
            rows.append(parts)
    return rows


def parse_ts(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def work_date_key(ts_iso):
    dt = parse_ts(ts_iso).astimezone(EASTERN)
    if dt.hour >= WORKDAY_CUTOFF_HOUR:
        dt = dt + timedelta(days=1)
    return dt.strftime("%Y-%m-%d")


def scan(repos_root, author, lookback_hours):
    import concurrent.futures

    since_dt = datetime.now(EASTERN) - timedelta(hours=lookback_hours)
    since_iso = since_dt.strftime("%Y-%m-%dT%H:%M:%S%z")

    repos = discover_repos(repos_root)
    actions = []
    repo_labels = set()
    # Collapses the same real commit seen more than once - git_log uses --all (needed to catch
    # not-yet-merged feature-branch work), and this org's workflow regularly cherry-picks a
    # commit onto a QA/release branch afterwards. That cherry-pick is a genuinely new SHA, but
    # keeps the exact same author date and message as the original, so (ts, subject) is what
    # actually identifies "the same real commit" here - without this, every cherry-picked
    # commit double-counts toward commit_count/worked_mins for whatever ticket it's on.
    seen_commits = set()
    # Each git_log() call is its own subprocess spawn (~1-1.5s on Windows) - running them
    # one repo at a time made scan() the actual bottleneck on load (dozens of repos x that
    # overhead), so fan them out instead of looping.
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
        futures = {executor.submit(git_log, repo, author, since_iso): repo for repo in repos}
        for future in concurrent.futures.as_completed(futures):
            repo = futures[future]
            rows = future.result()
            if not rows:
                continue
            label = os.path.basename(repo.rstrip(os.sep))
            for sha, ts, subject in rows:
                keys = sorted(set(TICKET_PREFIX + "-" + m for m in TICKET_RE.findall(subject)))
                if not keys:
                    continue
                dedup_key = (ts, subject)
                if dedup_key in seen_commits:
                    continue
                seen_commits.add(dedup_key)
                actions.append({"sha": sha[:12], "ts": ts, "repo": label,
                                 "type": classify(subject), "message": subject, "tickets": keys})
                repo_labels.add(label)

    return group_and_credit(actions), len(repos), repo_labels


def group_and_credit(actions):
    by_sha = {}
    for a in actions:
        if a["sha"] not in by_sha:
            by_sha[a["sha"]] = {"ts": a["ts"], "tickets": set(), "repo": a["repo"],
                                 "type": a["type"], "message": a["message"]}
        by_sha[a["sha"]]["tickets"].update(a["tickets"])

    shas = sorted(by_sha.keys(), key=lambda s: parse_ts(by_sha[s]["ts"]))
    credit = {}
    for i, sha in enumerate(shas):
        mins = 0.0
        if i + 1 < len(shas):
            gap = (parse_ts(by_sha[shas[i + 1]]["ts"]) - parse_ts(by_sha[sha]["ts"])).total_seconds() / 60.0
            mins = max(0.0, min(IDLE_GAP_CAP_MINS, gap))
        tickets = by_sha[sha]["tickets"]
        share = mins / len(tickets) if tickets else 0
        for k in tickets:
            credit[(sha, k)] = share

    by_date = {}
    for sha in shas:
        rec = by_sha[sha]
        dk = work_date_key(rec["ts"])
        for key in rec["tickets"]:
            bucket = by_date.setdefault(dk, {}).setdefault(key, {"actions": [], "mins": 0.0, "repos": set()})
            share = credit.get((sha, key), 0.0)
            bucket["actions"].append({"ts": rec["ts"], "type": rec["type"], "message": rec["message"],
                                       "sha": sha, "mins": share})
            bucket["repos"].add(rec["repo"])
            bucket["mins"] += share

    result = {}
    for dk, tickets in by_date.items():
        rows = []
        for key, b in tickets.items():
            acts = sorted(b["actions"], key=lambda a: parse_ts(a["ts"]))
            rows.append({
                "key": key, "repos": sorted(b["repos"]), "actions": acts,
                "worked_mins": round(b["mins"]),
                "commit_count": sum(1 for a in acts if a["type"] == "commit"),
                "merged": any(a["type"] == "pr-merge" for a in acts),
                "reverted": any(a["type"] == "revert" for a in acts),
                "first": acts[0]["ts"], "last": acts[-1]["ts"],
            })
        rows.sort(key=lambda r: parse_ts(r["last"]), reverse=True)
        result[dk] = rows
    return result


def fetch_jira_statuses(ticket_keys):
    """Returns {key: {"status": name_or_None, "status_category": "new"|"indeterminate"|"done"|None,
    "assignee_email": email_or_None}}. status_category is Jira's own statusCategory.key - the
    same 3-way split ("To Do"/"In Progress"/"Done" color families) Jira's own UI uses to color
    any status pill, whatever the workflow's real status names are."""
    import base64
    import urllib.request
    base = os.environ.get("JIRA_BASE_URL")
    email = os.environ.get("JIRA_EMAIL")
    token = os.environ.get("JIRA_API_TOKEN")
    if not (base and email and token) or not ticket_keys:
        return {}
    # Atlassian retired GET /rest/api/2|3/search (410 Gone) - the current equivalent is this
    # POST endpoint. It silently returned {} for every caller here until this was caught,
    # which is why status never actually showed up in the UI.
    jql = "key in (" + ",".join(sorted(ticket_keys)) + ")"
    url = base.rstrip("/") + "/rest/api/3/search/jql"
    body = json.dumps({"jql": jql, "fields": ["status", "assignee"], "maxResults": 200}).encode()
    auth = base64.b64encode(f"{email}:{token}".encode()).decode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": f"Basic {auth}", "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.load(resp)
        out = {}
        for i in data.get("issues", []):
            f = i["fields"]
            assignee = f.get("assignee") or {}
            status_field = f.get("status") or {}
            category = status_field.get("statusCategory") or {}
            out[i["key"]] = {
                "status": status_field.get("name"),
                "status_category": category.get("key"),
                "assignee_email": assignee.get("emailAddress"),
            }
        return out
    except Exception:
        return {}


def _fetch_one_ticket_worklogs(base, headers, email, key):
    import urllib.request
    by_date = {}
    start_at = 0
    try:
        while True:
            url = (base.rstrip("/") + f"/rest/api/3/issue/{key}/worklog"
                   f"?startAt={start_at}&maxResults=100")
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.load(resp)
            for w in data.get("worklogs", []):
                author_email = (w.get("author") or {}).get("emailAddress", "")
                if author_email.lower() != email.lower():
                    continue
                started = w.get("started")
                secs = w.get("timeSpentSeconds") or 0
                if not started or not secs:
                    continue
                dk = work_date_key(started)
                by_date[dk] = by_date.get(dk, 0) + secs
            start_at += len(data.get("worklogs", []))
            if start_at >= data.get("total", 0) or not data.get("worklogs"):
                break
    except Exception:
        pass
    return key, {dk: round(secs / 60) for dk, secs in by_date.items()}


def fetch_my_worklogs_by_workdate(ticket_keys):
    """Returns {ticket_key: {work_date_key: minutes}} - ONLY worklog entries authored by the
    configured JIRA_EMAIL, bucketed by the same 10pm-cutoff work-day rule as everything else.

    Jira's issue-level `timespent`/`aggregatetimespent` fields are a TEAM total across every
    author on the ticket - CAD-6651 alone has three different people's worklogs on it, and using
    that field would silently attribute teammates' logged hours to this user. This instead pages
    each ticket's own /worklog list and filters to entries this user actually wrote. One HTTP
    call per ticket, so these run concurrently - sequential took 38s for 14 tickets.

    ticket_keys spans the WHOLE lookback window (7 days), not just the selected day - so this
    naturally grows as more days of real activity accumulate (12 tickets early in a week, 50+
    by the end of it), and wall-clock time grows with it since it's one request per ticket.
    max_workers=16 (not 8) keeps that scaling closer to flat instead of linear."""
    import base64
    import urllib.request
    from concurrent.futures import ThreadPoolExecutor
    base = os.environ.get("JIRA_BASE_URL")
    email = os.environ.get("JIRA_EMAIL")
    token = os.environ.get("JIRA_API_TOKEN")
    if not (base and email and token) or not ticket_keys:
        return {}
    auth = base64.b64encode(f"{email}:{token}".encode()).decode()
    headers = {"Authorization": f"Basic {auth}"}
    out = {}
    with ThreadPoolExecutor(max_workers=16) as pool:
        for key, by_date in pool.map(
            lambda k: _fetch_one_ticket_worklogs(base, headers, email, k), ticket_keys
        ):
            if by_date:
                out[key] = by_date
    return out


def _jira_auth_headers():
    import base64
    base = os.environ.get("JIRA_BASE_URL")
    email = os.environ.get("JIRA_EMAIL")
    token = os.environ.get("JIRA_API_TOKEN")
    if not (base and email and token):
        return None, None
    auth = base64.b64encode(f"{email}:{token}".encode()).decode()
    return base.rstrip("/"), {"Authorization": f"Basic {auth}", "Content-Type": "application/json"}


def search_jira_users(query):
    """Live Jira user search (/rest/api/3/user/search) - real accounts only, for building a
    team roster. Returns [{"account_id","display_name","email","avatar_url"}], bots excluded."""
    import urllib.parse
    import urllib.request
    base, headers = _jira_auth_headers()
    if not base or not query or not query.strip():
        return []
    url = base + "/rest/api/3/user/search?query=" + urllib.parse.quote(query.strip()) + "&maxResults=15"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.load(resp)
        out = []
        for u in data:
            if u.get("accountType") != "atlassian":
                continue
            out.append({
                "account_id": u.get("accountId"),
                "display_name": u.get("displayName"),
                "email": u.get("emailAddress") or "",
                "avatar_url": (u.get("avatarUrls") or {}).get("24x24", ""),
            })
        return out
    except Exception:
        return []


def _worklog_minutes_for_account(base, headers, key, account_id, date_key):
    """Sums one issue's worklog entries authored by account_id that fall on date_key (the same
    10pm-cutoff work-day bucket as everywhere else in this app)."""
    import urllib.request
    mins = 0
    start_at = 0
    try:
        while True:
            url = base + f"/rest/api/3/issue/{key}/worklog?startAt={start_at}&maxResults=100"
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.load(resp)
            for w in data.get("worklogs", []):
                if (w.get("author") or {}).get("accountId") != account_id:
                    continue
                started = w.get("started")
                secs = w.get("timeSpentSeconds") or 0
                if not started or not secs:
                    continue
                if work_date_key(started) == date_key:
                    mins += round(secs / 60)
            start_at += len(data.get("worklogs", []))
            if start_at >= data.get("total", 0) or not data.get("worklogs"):
                break
    except Exception:
        pass
    return mins


def fetch_team_member_worklogs(account_id, date_key):
    """Per-ticket worklog breakdown for one team member on one work-date. Jira doesn't expose
    "everything this account logged today" directly, so this finds candidate tickets via a
    worklogAuthor/worklogDate JQL search (widened a day either side to survive the 10pm-cutoff
    shift), then fetches and precisely re-buckets each candidate's own worklog list - the same
    real, actual-time approach fetch_my_worklogs_by_workdate uses for the current user."""
    import concurrent.futures
    base, headers = _jira_auth_headers()
    if not base or not account_id:
        return {"tickets": [], "total_minutes": 0}

    day = datetime.strptime(date_key, "%Y-%m-%d")
    lo = (day - timedelta(days=1)).strftime("%Y-%m-%d")
    hi = (day + timedelta(days=1)).strftime("%Y-%m-%d")
    jql = (f'worklogAuthor = "{account_id}" AND worklogDate >= "{lo}" AND worklogDate <= "{hi}"')
    url = base + "/rest/api/3/search/jql"
    body = json.dumps({"jql": jql, "fields": ["summary", "status"], "maxResults": 100}).encode()
    import urllib.request
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            issues = json.load(resp).get("issues", [])
    except Exception:
        return {"tickets": [], "total_minutes": 0}
    if not issues:
        return {"tickets": [], "total_minutes": 0}

    def one(issue):
        key = issue["key"]
        mins = _worklog_minutes_for_account(base, headers, key, account_id, date_key)
        if mins <= 0:
            return None
        f = issue.get("fields") or {}
        status = f.get("status") or {}
        category = (status.get("statusCategory") or {}).get("key")
        return {"key": key, "summary": f.get("summary") or "", "status": status.get("name"),
                "status_category": category, "minutes": mins}

    tickets = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for row in pool.map(one, issues):
            if row:
                tickets.append(row)
    tickets.sort(key=lambda r: r["minutes"], reverse=True)
    return {"tickets": tickets, "total_minutes": sum(t["minutes"] for t in tickets)}


def current_work_date_key():
    return work_date_key(datetime.now(EASTERN).isoformat())


def test_jira_connection(base_url, email, token):
    """Hit /myself to confirm the credentials actually work before we save them."""
    import base64
    import urllib.request
    import urllib.error
    if not (base_url and email and token):
        return {"ok": False, "error": "Base URL, email and API token are all required."}
    url = base_url.rstrip("/") + "/rest/api/2/myself"
    auth = base64.b64encode(f"{email}:{token}".encode()).decode()
    req = urllib.request.Request(url, headers={"Authorization": f"Basic {auth}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.load(resp)
        return {"ok": True, "display_name": data.get("displayName") or email}
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return {"ok": False, "error": "Jira rejected those credentials (401 Unauthorized)."}
        return {"ok": False, "error": f"Jira returned HTTP {e.code}."}
    except Exception as e:
        return {"ok": False, "error": f"Could not reach {base_url}: {e}"}


def fetch_jira_display_name():
    """The real display name on the connected Jira account (/myself) - the authoritative name
    for whoever these credentials belong to, rather than trusting local git config (which is
    just a string someone typed once and could be stale, a nickname, or wrong on another
    machine). Falls back to None (caller keeps whatever it already had) if not connected."""
    import base64
    import urllib.request
    base = os.environ.get("JIRA_BASE_URL")
    email = os.environ.get("JIRA_EMAIL")
    token = os.environ.get("JIRA_API_TOKEN")
    if not (base and email and token):
        return None
    url = base.rstrip("/") + "/rest/api/2/myself"
    auth = base64.b64encode(f"{email}:{token}".encode()).decode()
    req = urllib.request.Request(url, headers={"Authorization": f"Basic {auth}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.load(resp)
        return data.get("displayName") or None
    except Exception:
        return None


def fetch_my_account_id():
    """The connected Jira account's real accountId (/myself) - the only reliable way to later
    match "did I write this comment", since a comment's author.emailAddress is frequently
    withheld by Jira Cloud's own directory-privacy settings even for your own comments on some
    tenants, but accountId is always present."""
    base, headers = _jira_auth_headers()
    if not base:
        return None
    import urllib.request
    req = urllib.request.Request(base + "/rest/api/3/myself", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.load(resp).get("accountId")
    except Exception:
        return None


def fetch_commented_tickets(account_id, lookback_hours):
    """Tickets you left a real Jira comment on but never touched via git at all - the one real
    activity signal git-based scanning can never see at all (see _is_mine's docstring for the
    same structural blind spot on the assignee side). JQL has no commentAuthor/commentDate filter
    (unlike worklogAuthor/worklogDate used for team worklogs), so this narrows candidates to
    issues assigned to or reported by you that were touched in the lookback window, then checks
    each candidate's own comments (newest first) for ones actually authored by you in that
    window - bounded to your own issues, not an instance-wide scan.
    Returns {date_key: [{"key","summary","status","status_category","created"}, ...]}."""
    import concurrent.futures
    import urllib.request
    base, headers = _jira_auth_headers()
    if not base or not account_id:
        return {}

    since_dt = datetime.now(EASTERN) - timedelta(hours=lookback_hours)
    jql = (f'(assignee = "{account_id}" OR reporter = "{account_id}") '
           f'AND updated >= "{since_dt.strftime("%Y/%m/%d %H:%M")}"')
    url = base + "/rest/api/3/search/jql"
    body = json.dumps({"jql": jql, "fields": ["summary", "status"], "maxResults": 100}).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            issues = json.load(resp).get("issues", [])
    except Exception:
        return {}
    if not issues:
        return {}

    def one(issue):
        key = issue["key"]
        f = issue.get("fields") or {}
        status = f.get("status") or {}
        curl = base + f"/rest/api/3/issue/{key}/comment?maxResults=100&orderBy=-created"
        creq = urllib.request.Request(curl, headers=headers)
        try:
            with urllib.request.urlopen(creq, timeout=15) as cresp:
                comments = json.load(cresp).get("comments", [])
        except Exception:
            return []
        rows = []
        for c in comments:
            created = c.get("created")
            if not created:
                continue
            # Ordered newest-first - once one comment (by anyone) is older than the window,
            # every comment after it is guaranteed older too.
            if parse_ts(created) < since_dt:
                break
            if (c.get("author") or {}).get("accountId") != account_id:
                continue
            rows.append({"key": key, "summary": f.get("summary") or "",
                         "status": status.get("name"),
                         "status_category": (status.get("statusCategory") or {}).get("key"),
                         "created": created})
        return rows

    all_rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for rows in pool.map(one, issues):
            all_rows.extend(rows)

    by_date = {}
    for r in all_rows:
        by_date.setdefault(work_date_key(r["created"]), []).append(r)
    return by_date


# ---------------------------------------------------------------- Google Calendar (OAuth)
# The secret-iCal-URL approach is simpler but this account's Workspace admin has external
# calendar sharing disabled - a domain policy, not something fixable from a personal account.
# OAuth app access is a separate policy surface and works instead: a real OAuth 2.0 "installed
# app" loopback flow - open the browser for consent, catch the redirect on a one-shot local
# HTTP server, exchange the code for a refresh token, store that (never the short-lived access
# token) and mint a fresh access token from it on every refresh.

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"

# The published installer bakes in a real pre-registered OAuth client here, so downloading
# the .exe gives you one-click "Connect Google" with no setup. That value isn't in this public
# source file (it's swapped in at build time) - building/running from source, fill in your own
# Client ID/Secret via the "Advanced" section in Settings (create one at
# https://console.cloud.google.com/apis/credentials - Desktop app type, Calendar API enabled).
SHARED_GOOGLE_CLIENT_ID = ""
SHARED_GOOGLE_CLIENT_SECRET = ""


def start_google_oauth(client_id, client_secret):
    """Blocks until the user finishes the browser consent step (or times out). Returns
    {"ok": True, "refresh_token": "..."} or {"ok": False, "error": "..."}."""
    import http.server
    import socketserver
    import urllib.parse
    import urllib.request

    result = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            qs = urllib.parse.urlparse(self.path).query
            params = urllib.parse.parse_qs(qs)
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            if "code" in params:
                result["code"] = params["code"][0]
                self.wfile.write(b"<html><body><h3>Connected - you can close this tab.</h3></body></html>")
            else:
                result["error"] = params.get("error", ["unknown_error"])[0]
                self.wfile.write(b"<html><body><h3>Google sign-in was cancelled or failed.</h3></body></html>")

        def log_message(self, *args):
            pass  # don't spam stderr with HTTP access logs

    with socketserver.TCPServer(("127.0.0.1", 0), Handler) as httpd:
        port = httpd.server_address[1]
        redirect_uri = f"http://127.0.0.1:{port}"
        params = {
            "client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code",
            "scope": GOOGLE_CALENDAR_SCOPE, "access_type": "offline", "prompt": "consent",
        }
        webbrowser.open(GOOGLE_AUTH_URL + "?" + urllib.parse.urlencode(params))
        httpd.timeout = 120
        httpd.handle_request()

    if "error" in result:
        return {"ok": False, "error": f"Google sign-in error: {result['error']}"}
    if "code" not in result:
        return {"ok": False, "error": "Timed out waiting for Google sign-in (2 min)."}

    body = urllib.parse.urlencode({
        "client_id": client_id, "client_secret": client_secret, "code": result["code"],
        "grant_type": "authorization_code", "redirect_uri": redirect_uri,
    }).encode()
    req = urllib.request.Request(GOOGLE_TOKEN_URL, data=body, method="POST",
                                  headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            token_data = json.load(resp)
    except Exception as e:
        return {"ok": False, "error": f"Token exchange failed: {e}"}

    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        return {"ok": False, "error": "Google didn't return a refresh token - revoke this app's "
                                       "access at myaccount.google.com/permissions and try again."}
    return {"ok": True, "refresh_token": refresh_token}


def google_access_token(client_id, client_secret, refresh_token):
    import urllib.parse
    import urllib.request
    body = urllib.parse.urlencode({
        "client_id": client_id, "client_secret": client_secret,
        "refresh_token": refresh_token, "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request(GOOGLE_TOKEN_URL, data=body, method="POST",
                                  headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)["access_token"]


def _event_source(e):
    """Best-effort REAL conferencing/location label for an event - never invented. Prefers the
    actual conference solution name Google reports (e.g. "Microsoft Teams", "Google Meet",
    "Zoom Meeting"), then falls back to the event's own free-text location field, else None."""
    conf = (e.get("conferenceData") or {}).get("conferenceSolution") or {}
    if conf.get("name"):
        return conf["name"]
    if e.get("hangoutLink"):
        return "Google Meet"
    loc = (e.get("location") or "").strip()
    return loc[:30] if loc else None


def fetch_today_calendar():
    """Returns {"count": int, "next": {...} or None, "events": [...]} for today (Eastern
    calendar day), or None if Calendar isn't connected or the call fails. Each event carries
    real start/end (for duration) and a real source label when the calendar actually has one -
    never a fabricated category."""
    import urllib.parse
    import urllib.request
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    refresh_token = os.environ.get("GOOGLE_REFRESH_TOKEN")
    if not (client_id and client_secret and refresh_token):
        return None
    try:
        access_token = google_access_token(client_id, client_secret, refresh_token)
        now = datetime.now(EASTERN)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        qs = urllib.parse.urlencode({
            "timeMin": start.isoformat(), "timeMax": end.isoformat(),
            "singleEvents": "true", "orderBy": "startTime", "maxResults": 20,
        })
        url = f"https://www.googleapis.com/calendar/v3/calendars/primary/events?{qs}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {access_token}"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.load(resp)
        events = []
        for e in data.get("items", []):
            start_val = (e.get("start") or {}).get("dateTime")
            end_val = (e.get("end") or {}).get("dateTime")
            if not start_val:
                continue  # skip all-day events - not real meetings
            events.append({
                "title": (e.get("summary") or "(no title)")[:60],
                "start": parse_ts(start_val),
                "end": parse_ts(end_val) if end_val else None,
                "source": _event_source(e),
            })
        events.sort(key=lambda e: e["start"])
        next_ev = next((e for e in events if e["start"] >= now), None)
        return {"count": len(events), "next": next_ev, "events": events}
    except Exception:
        return None


def calendar_summary_text(cal):
    if cal is None:
        return None
    if cal["count"] == 0:
        return "\U0001F4C5 No meetings today"
    if cal["next"]:
        t = cal["next"]["start"].astimezone(EASTERN).strftime("%I:%M %p").lstrip("0")
        return f"\U0001F4C5 {cal['count']} today · next: {cal['next']['title']} at {t}"
    return f"\U0001F4C5 {cal['count']} meeting" + ("" if cal["count"] == 1 else "s") + " today"


def format_calendar_events(cal):
    """Turns fetch_today_calendar()'s raw events into display-ready rows: real time, real
    duration (when Google gave us an end time), a real source label if the calendar actually
    has one, and is_past so already-finished events can render grayed out - not invented."""
    if cal is None:
        return []
    now = datetime.now(EASTERN)
    rows = []
    for e in cal.get("events", []):
        start_local = e["start"].astimezone(EASTERN)
        duration = None
        if e.get("end"):
            mins = round((e["end"] - e["start"]).total_seconds() / 60)
            if mins > 0:
                duration = fmt_mins(mins)
        is_past = (e["end"] or e["start"]) <= now
        rows.append({
            "time": start_local.strftime("%I:%M %p").lstrip("0"),
            "title": e["title"],
            "duration": duration,
            "source": e.get("source"),
            "is_past": is_past,
        })
    return rows


def fmt_mins(mins):
    mins = int(mins)
    h, m = divmod(mins, 60)
    if h and m:
        return f"{h}h {m}m"
    if h:
        return f"{h}h"
    return f"{m}m"


def fmt_time(ts):
    return parse_ts(ts).astimezone(EASTERN).strftime("%I:%M %p").lstrip("0")


def strip_ticket_prefix(key, text):
    return re.sub(r"^" + re.escape(key) + r"\s*[-:]\s*", "", text)


def git_default_author():
    try:
        out = subprocess.run(["git", "config", "--global", "user.name"], capture_output=True, text=True,
                              timeout=10, stdin=subprocess.DEVNULL,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return out.stdout.strip() or "Unknown"
    except Exception:
        return "Unknown"


def git_default_email():
    try:
        out = subprocess.run(["git", "config", "--global", "user.email"], capture_output=True, text=True,
                              timeout=10, stdin=subprocess.DEVNULL,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return out.stdout.strip()
    except Exception:
        return ""


# ---------------------------------------------------------------- JS <-> Python bridge

def default_repos_root():
    # os.path.expanduser("~") is almost never where actual repos live - it silently
    # finds zero repos and the whole app renders empty. Prefer this machine's known
    # projects root (same one every other Session Log tool in this repo defaults to)
    # when REPOS_ROOT isn't set, and only fall back to home if that path is absent.
    known = r"F:\MLProjects"
    return known if os.path.isdir(known) else os.path.expanduser("~")


class Api:
    def __init__(self):
        self.repos_root = os.environ.get("REPOS_ROOT") or default_repos_root()
        self.author = os.environ.get("GIT_AUTHOR_NAME") or git_default_author()
        # Displayed name only - self.author above still drives `git log --author` matching and
        # must stay whatever string git actually uses. This is cosmetic: Jira's own account name
        # when connected (real, verified), else the same git-derived name as before.
        self.display_name = self.author
        self.by_date = {}
        self.statuses = {}
        self.my_worklogs = {}
        self.calendar = None
        self.repo_count = 0
        self.main_window = None
        self.my_account_id = None
        # Configurable from the Settings panel (0 = off) - break_reminder_loop reads this
        # attribute fresh every minute rather than a fixed constant, so a change takes effect
        # immediately, with no restart.
        self.break_reminder_mins = int(load_config().get("BREAK_REMINDER_MINS", BREAK_REMINDER_MINS))
        # Written by break_reminder_loop (a background thread), read by get_break_status()
        # (polled from the page's own JS timer) - see break_reminder_loop's docstring for why
        # this is a plain attribute instead of a window.evaluate_js() push.
        self.break_status = {"continuous_mins": 0.0, "reminder_mins": self.break_reminder_mins}

    def refresh(self):
        # Calendar is deliberately NOT fetched here - it's the one call that's a genuine
        # round-trip to a third party with no local fallback, so a Google hiccup shouldn't be
        # able to fail the whole dashboard load. The page calls refresh_calendar() itself once
        # this has rendered (see loadInitial() in the page script).
        #
        # NOTE: an earlier attempt made this fully non-blocking (return instantly, page polls
        # get_refresh_result()) on the theory that pywebview dispatches bridge calls on the UI
        # thread. That made things WORSE in testing - the window went unresponsive almost
        # immediately and never recovered, instead of the bounded, self-recovering slowness
        # this version has. Reverted; this is the version that passed a clean 3-minute
        # responsiveness soak test.
        outcome = {}

        def _do_refresh():
            try:
                self.by_date, self.repo_count, active_repos = scan(
                    self.repos_root, self.author, DEFAULT_LOOKBACK_HOURS)
                all_keys = {t["key"] for tickets in self.by_date.values() for t in tickets}
                self.statuses = fetch_jira_statuses(all_keys)
                self.my_worklogs = fetch_my_worklogs_by_workdate(all_keys)
                self.display_name = fetch_jira_display_name() or self.author
                outcome["result"] = self.get_data()
            except Exception as exc:
                outcome["error"] = exc

        # Every individual network/subprocess call above already has its own timeout, but a
        # hung DNS lookup (a real Windows/corporate-network gotcha) can block far longer than
        # any of those - Python's socket `timeout=` doesn't reliably cover name resolution.
        # This is the backstop: no matter what's actually stuck underneath, refresh() itself
        # is guaranteed to return or raise within REFRESH_TIMEOUT_SECS, so the page's retry
        # loop always gets a real answer instead of waiting on the skeleton forever.
        #
        # A plain daemon thread (not ThreadPoolExecutor) is deliberate: a stuck worker can
        # never be killed, and ThreadPoolExecutor registers an atexit hook that JOINS every
        # thread it ever created - which would hang the whole app on exit if one never
        # finishes. A daemon thread is simply abandoned instead, at exit and here alike.
        worker = threading.Thread(target=_do_refresh, daemon=True)
        worker.start()
        worker.join(timeout=REFRESH_TIMEOUT_SECS)

        try:
            if worker.is_alive():
                raise TimeoutError(
                    f"Refreshing timed out after {REFRESH_TIMEOUT_SECS}s - "
                    "this usually means a slow or stuck network connection."
                )
            if "error" in outcome:
                raise outcome["error"]
            return outcome["result"]
        except Exception:
            # The page silently retries on a failed refresh() (never shows a raw error card),
            # so without this the real cause is invisible - log it, then re-raise so the
            # caller's behavior (retry with backoff) is unchanged.
            log_error("refresh() failed", exc=True)
            raise

    def refresh_calendar(self):
        """Fetched separately, after the main dashboard has already rendered - see refresh()."""
        self.calendar = fetch_today_calendar()
        return {
            "calendar_summary": calendar_summary_text(self.calendar),
            "calendar_events": format_calendar_events(self.calendar),
        }

    def refresh_commented_tickets(self, date_key=None):
        """Merges in tickets you left a real Jira comment on but never touched via git at all
        (see fetch_commented_tickets) - fetched separately, after the main dashboard has already
        rendered, same reasoning as refresh_calendar(): a JQL search plus a comment fetch per
        candidate issue is real Jira network work that shouldn't be able to slow down or fail
        the core git-based load. Bounded by COMMENTED_TICKETS_TIMEOUT_SECS the same way
        refresh() is bounded by REFRESH_TIMEOUT_SECS - see that constant's comment for why this
        one exists at all (a real, reproducible freeze without it)."""
        def _do_it():
            if not self.my_account_id:
                self.my_account_id = fetch_my_account_id()
            if not self.my_account_id:
                return

            commented = fetch_commented_tickets(self.my_account_id, DEFAULT_LOOKBACK_HOURS)
            for dk, rows in commented.items():
                bucket = self.by_date.setdefault(dk, [])
                existing_keys = {t["key"] for t in bucket}
                for r in rows:
                    if r["key"] in existing_keys:
                        continue
                    bucket.append({
                        "key": r["key"], "repos": [], "source": "comment",
                        "actions": [{"ts": r["created"], "type": "comment",
                                     "message": r["summary"] or r["key"], "mins": 0}],
                        "worked_mins": 0, "commit_count": 0, "merged": False, "reverted": False,
                    })
                    existing_keys.add(r["key"])
                bucket.sort(key=lambda t: parse_ts(t["actions"][-1]["ts"]), reverse=True)

            if commented:
                # New tickets just appeared - they need the same Jira status lookup git-discovered
                # tickets already got in refresh().
                all_keys = {t["key"] for tickets in self.by_date.values() for t in tickets}
                self.statuses = fetch_jira_statuses(all_keys)

        # A plain daemon thread, not ThreadPoolExecutor, for the same reason refresh() uses one -
        # see refresh()'s own comment. A stuck worker is simply abandoned, never joined at exit.
        worker = threading.Thread(target=_do_it, daemon=True)
        worker.start()
        worker.join(timeout=COMMENTED_TICKETS_TIMEOUT_SECS)
        if worker.is_alive():
            log_error(f"refresh_commented_tickets() timed out after {COMMENTED_TICKETS_TIMEOUT_SECS}s - "
                       "skipping comment-only tickets for this refresh")
        return self.get_data(date_key)

    def get_break_status(self):
        """Polled by the page's own JS timer - see break_reminder_loop's docstring."""
        return self.break_status

    def set_break_reminder_mins(self, mins):
        """Saved from the Settings panel. break_reminder_loop reads self.break_reminder_mins
        fresh every minute, so this takes effect on its own within a minute - no restart."""
        try:
            mins = max(0, int(mins))
        except (TypeError, ValueError):
            return {"ok": False, "error": "Enter a whole number of minutes."}
        cfg = load_config()
        cfg["BREAK_REMINDER_MINS"] = mins
        save_config(cfg)
        self.break_reminder_mins = mins
        self.break_status = {**self.break_status, "reminder_mins": mins}
        return {"ok": True, "reminder_mins": mins}

    def log_client_error(self, message):
        """JS calls this from its own catch blocks (e.g. loadInitial's retry loop) so a
        failure that never reaches Python still lands in the same error.log."""
        log_error(f"client-side error: {message}")
        return True

    def connect_google_calendar(self, client_id="", client_secret=""):
        # Blank fields (the normal case - the "Connect Google" button doesn't ask for
        # credentials) fall back to SHARED_GOOGLE_CLIENT_ID/SECRET above. In the published
        # installer those are real, so most people never need their own Google Cloud project.
        # In this public source they're blank, so fill in the Advanced fields with your own
        # Client ID/Secret when running from source.
        client_id = (client_id or "").strip() or SHARED_GOOGLE_CLIENT_ID
        client_secret = (client_secret or "").strip() or SHARED_GOOGLE_CLIENT_SECRET
        if not client_id or not client_secret:
            return {"ok": False, "error": "Client ID and Client Secret are both required."}
        result = start_google_oauth(client_id, client_secret)
        if not result["ok"]:
            return result
        cfg = load_config()
        cfg["GOOGLE_CLIENT_ID"] = client_id
        cfg["GOOGLE_CLIENT_SECRET"] = client_secret
        cfg["GOOGLE_REFRESH_TOKEN"] = result["refresh_token"]
        save_config(cfg)
        apply_config_to_env(cfg)
        self.calendar = fetch_today_calendar()
        return {"ok": True}

    def _is_mine(self, t):
        """"My ticket" = Jira assigns it to me, OR I authored a real commit on it - not just
        clicked merge on a teammate's PR (several "Merge / Review only" tickets here, e.g.
        CAD-6492/CAD-6401/CAD-7412, are actually assigned to other people). When Jira isn't
        connected there is no assignee signal at all, so fall back to every git-touched ticket
        rather than hiding everything."""
        if t.get("source") == "comment":
            # Already scoped to "assignee = me OR reporter = me" by fetch_commented_tickets'
            # own JQL - there's no git signal to re-derive that from here.
            return True
        jira_row = self.statuses.get(t["key"])
        my_email = os.environ.get("JIRA_EMAIL", "")
        if jira_row and jira_row.get("assignee_email") and my_email:
            if jira_row["assignee_email"].lower() == my_email.lower():
                return True
            return any(a["type"] == "commit" for a in t["actions"])
        return True

    def get_data(self, date_key=None):
        try:
            return self._get_data_impl(date_key)
        except Exception:
            log_error(f"get_data(date_key={date_key!r}) failed", exc=True)
            raise

    def _get_data_impl(self, date_key=None):
        # The dropdown (and the default selection) always includes today's real work-date,
        # even with zero commits yet - otherwise first thing in the morning (or any day
        # before your first commit) the dashboard silently defaults to a stale prior day
        # instead of the actual system date.
        today_key = work_date_key(datetime.now(EASTERN).isoformat())
        dates = sorted(set(self.by_date.keys()) | {today_key}, reverse=True)
        if date_key:
            # The date-jump picker lets you pick ANY calendar date, not just ones already in
            # `dates` (which only ever lists days with real activity, plus today) - a date with
            # no activity is a legitimate, valid selection (shows an empty day), it just isn't
            # one of the dropdown's own options. Only fall back to today for a genuinely
            # malformed value.
            try:
                datetime.strptime(date_key, "%Y-%m-%d")
            except ValueError:
                date_key = None
        if not date_key:
            date_key = dates[0]

        tickets = [t for t in self.by_date.get(date_key, []) if self._is_mine(t)]
        total_mins = sum(t["worked_mins"] for t in tickets)
        total_commits = sum(t["commit_count"] for t in tickets)
        repos_today = sorted({r for t in tickets for r in t["repos"]})
        in_progress = sum(1 for t in tickets if not t["merged"])

        yesterday_commits = None
        if len(dates) > 1 and date_key == dates[0]:
            prev = [t for t in self.by_date.get(dates[1], []) if self._is_mine(t)]
            yesterday_commits = sum(t["commit_count"] for t in prev)

        ticket_rows = []
        for t in tickets:
            last_msg = strip_ticket_prefix(t["key"], t["actions"][-1]["message"])
            # Newest first, mirroring the Recent Activity feed - this is what a ticket row
            # expands to reveal (same real commits, same session-log.html accordion idea).
            detail_actions = [{
                "time": fmt_time(a["ts"]), "type": a["type"],
                "message": strip_ticket_prefix(t["key"], a["message"])[:90],
            } for a in reversed(t["actions"])]
            # Only THIS user's own worklogs, only the ones dated on the SAME work-day bucket
            # being shown - never a team-wide lifetime total (see fetch_my_worklogs_by_workdate).
            # This is the PRIMARY time figure now: real, actual, Jira-logged - not an assumption.
            # The git-gap estimate is kept only as a secondary reference (worked_mins/worked).
            logged_mins = self.my_worklogs.get(t["key"], {}).get(date_key, 0)
            # "commit" comes from git; "comment" comes from refresh_commented_tickets() (a real
            # Jira comment, the one activity signal git scanning alone can never see - see that
            # function's docstring). Neither present means merge/base-sync activity only.
            has_commit = any(a["type"] == "commit" for a in t["actions"])
            has_comment = any(a["type"] == "comment" for a in t["actions"])
            activity_kind = "Development" if has_commit else (
                "Commented only" if has_comment else "Merge / Review only")
            ticket_rows.append({
                "key": t["key"],
                "jira_url": (os.environ.get("JIRA_BASE_URL", "").rstrip("/") + "/browse/" + t["key"])
                            if os.environ.get("JIRA_BASE_URL") else None,
                "summary": last_msg[:70],
                "worked": fmt_mins(t["worked_mins"]),
                "worked_mins": t["worked_mins"],
                "logged_mins": logged_mins,
                "logged": fmt_mins(logged_mins),
                "has_logged": logged_mins > 0,
                "activity_kind": activity_kind,
                "status": (self.statuses.get(t["key"]) or {}).get("status"),
                "status_category": (self.statuses.get(t["key"]) or {}).get("status_category"),
                "merged": t["merged"],
                "reverted": t["reverted"],
                "repos": t["repos"],
                "commit_count": t["commit_count"],
                "first_activity": fmt_time(t["actions"][0]["ts"]),
                "last_activity": fmt_time(t["actions"][-1]["ts"]),
                "actions": detail_actions,
            })

        # Real git-touched tickets with zero minutes actually logged to Jira for this day -
        # a reminder list, not an estimate. "worked on" means an authored commit; "reviewed"
        # means merge/base-sync activity with no commit of your own on it that day. Carries the
        # same detail a ticket row has, since the modal is the only place some of this is seen.
        needs_logging = [{
            "key": r["key"], "jira_url": r["jira_url"], "activity_kind": r["activity_kind"],
            "summary": r["summary"], "status": r["status"], "status_category": r["status_category"],
            "worked": r["worked"], "repos": r["repos"], "commit_count": r["commit_count"],
            "first_activity": r["first_activity"], "last_activity": r["last_activity"],
            "merged": r["merged"], "reverted": r["reverted"],
        } for r in ticket_rows if not r["has_logged"]]

        activity = sorted(
            [{"ticket": t["key"], **a} for t in tickets for a in t["actions"]],
            key=lambda a: parse_ts(a["ts"]), reverse=True,
        )
        activity_rows = [{
            "time": fmt_time(a["ts"]), "type": a["type"], "ticket": a["ticket"],
            "message": strip_ticket_prefix(a["ticket"], a["message"])[:70],
            "mins": round(a.get("mins", 0)),
        } for a in activity]

        # Breaks down ACTUAL logged minutes (never the git-gap estimate) by each ticket's real
        # Jira status - the only real "category" this data actually has. A git action's type
        # (commit/merge/revert) has no counterpart in real worklog data, so that estimate-based
        # split was never truthfully "Development 59% / Code Review 41%" of anything real.
        cat_mins = {}
        for row in ticket_rows:
            if row["logged_mins"] <= 0:
                continue
            label = row["status"] or "No status"
            cat_mins[label] = cat_mins.get(label, 0) + row["logged_mins"]
        donut = sorted(
            [{"key": k, "mins": v} for k, v in cat_mins.items() if v > 0],
            key=lambda d: d["mins"], reverse=True,
        )

        week_total_mins = sum(t["worked_mins"] for tix in self.by_date.values() for t in tix if self._is_mine(t))
        week_total_tickets = sum(1 for tix in self.by_date.values() for t in tix if self._is_mine(t))
        # Sum of this user's own worklogs dated on this same work-day (see
        # fetch_my_worklogs_by_workdate) - a real, day-scoped, author-filtered Jira figure,
        # directly comparable to worked_mins (the git-based estimate for the same day).
        total_logged_mins = sum(r["logged_mins"] for r in ticket_rows)

        return {
            "author": self.display_name,
            "initials": "".join(w[0] for w in self.display_name.split()[:2]).upper() if self.display_name != "Unknown" else "?",
            "first_name": self.display_name.split()[0] if self.display_name != "Unknown" else "there",
            "hour": datetime.now().hour,
            "synced_at": datetime.now().strftime("%I:%M %p").lstrip("0"),
            "dates": dates,
            "selected_date": date_key,
            "selected_date_label": (datetime.strptime(date_key, "%Y-%m-%d").strftime("%a, %b %d, %Y")
                                     if date_key else "—"),
            "stats": {
                # PRIMARY = actual Jira-logged time, not the git-based assumption.
                "logged_mins": total_logged_mins, "logged": fmt_mins(total_logged_mins),
                "daily_target_mins": DAILY_TARGET_MINS,
                "target_pct": min(100, round(100 * total_logged_mins / DAILY_TARGET_MINS)) if DAILY_TARGET_MINS else 0,
                "tickets": len(tickets), "in_progress": in_progress,
                "commits": total_commits, "commits_delta": (
                    (total_commits - yesterday_commits) if yesterday_commits is not None else None),
                "repos": len(repos_today), "repos_active": ", ".join(repos_today) if repos_today else "none",
                # Secondary reference only - the non-overlapping git-gap estimate.
                "worked_mins": total_mins, "worked": fmt_mins(total_mins),
            },
            "tickets": ticket_rows,
            "needs_logging": needs_logging,
            "activity": activity_rows,
            "donut": donut,
            "week": {"mins": fmt_mins(week_total_mins), "tickets": week_total_tickets, "days": len(dates)},
            "jira_base_url": os.environ.get("JIRA_BASE_URL"),
            "repo_count": self.repo_count,
            "calendar_summary": calendar_summary_text(self.calendar),
            "calendar_events": format_calendar_events(self.calendar),
            "config": {
                "repos_root": self.repos_root,
                "author": self.author,
                "lookback_hours": DEFAULT_LOOKBACK_HOURS,
                "daily_target_hours": DAILY_TARGET_MINS / 60,
                "jira_base_url": os.environ.get("JIRA_BASE_URL", ""),
                "jira_email": os.environ.get("JIRA_EMAIL", ""),
                "suggested_jira_email": git_default_email(),
                "jira_has_token": bool(os.environ.get("JIRA_API_TOKEN")),
                "jira_connected": bool(os.environ.get("JIRA_BASE_URL") and os.environ.get("JIRA_API_TOKEN")),
                "calendar_connected": bool(os.environ.get("GOOGLE_REFRESH_TOKEN")),
                "google_client_id": os.environ.get("GOOGLE_CLIENT_ID", ""),
                "break_reminder_mins": self.break_reminder_mins,
            },
        }

    def open_url(self, url):
        webbrowser.open(url)
        return True

    def save_jira_settings(self, base_url, email, token):
        base_url = (base_url or "").strip()
        email = (email or "").strip()
        # The UI sends token=None (unchanged) when the user left the masked field alone.
        if token is None:
            token = os.environ.get("JIRA_API_TOKEN", "")
        else:
            token = token.strip()

        result = test_jira_connection(base_url, email, token)
        if not result["ok"]:
            return result

        cfg = load_config()
        cfg["JIRA_BASE_URL"] = base_url
        cfg["JIRA_EMAIL"] = email
        cfg["JIRA_API_TOKEN"] = token
        save_config(cfg)
        apply_config_to_env(cfg)

        # Re-fetch statuses/worklogs immediately so the change is visible without a manual rescan.
        all_keys = {t["key"] for tickets in self.by_date.values() for t in tickets}
        self.statuses = fetch_jira_statuses(all_keys)
        self.my_worklogs = fetch_my_worklogs_by_workdate(all_keys)
        self.display_name = result.get("display_name") or self.author
        return result

    def clear_settings(self):
        """Wipes the saved Jira/Google config (file + in-memory env vars) and drops this
        session's connected state, so the Settings panel goes back to a first-run, nothing-
        configured state without needing to hunt down and delete the config file by hand."""
        try:
            os.remove(CONFIG_PATH)
        except FileNotFoundError:
            pass
        for key in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN",
                    "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN"):
            os.environ.pop(key, None)
        self.statuses = {}
        self.my_worklogs = {}
        self.calendar = None
        self.display_name = self.author
        return self.get_data()

    # ------------------------------------------------------------ Team worklogs (2nd window)

    def get_team(self):
        return load_config().get("TEAM_MEMBERS", [])

    def search_team_members(self, query):
        return search_jira_users(query)

    def _notify_team_changed(self):
        """Deliberately does NOT push a live refresh into the main window via
        window.evaluate_js() anymore - calling into a *different* window's WebView2 control
        from here was a real, reproducible cause of the whole app freezing (Windows reports
        the window as "not responding"), the same underlying issue as break_reminder_loop's
        old push (see its docstring). The main dashboard's Team Worklog card just picks up
        the new roster on its next natural refresh (rescan, date change, or reload) instead -
        a strictly safer trade-off than an instant update that can hang the app."""

    def add_team_member(self, account_id, display_name, email):
        cfg = load_config()
        team = cfg.get("TEAM_MEMBERS", [])
        if not any(m["account_id"] == account_id for m in team):
            team.append({"account_id": account_id, "display_name": display_name, "email": email or ""})
        cfg["TEAM_MEMBERS"] = team
        save_config(cfg)
        self._notify_team_changed()
        return team

    def remove_team_member(self, account_id):
        cfg = load_config()
        team = [m for m in cfg.get("TEAM_MEMBERS", []) if m["account_id"] != account_id]
        cfg["TEAM_MEMBERS"] = team
        save_config(cfg)
        self._notify_team_changed()
        return team

    def get_team_worklogs(self, date_key=None):
        import concurrent.futures
        date_key = date_key or current_work_date_key()
        team = load_config().get("TEAM_MEMBERS", [])
        results = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(fetch_team_member_worklogs, m["account_id"], date_key): m for m in team}
            for future in concurrent.futures.as_completed(futures):
                m = futures[future]
                results[m["account_id"]] = future.result()
        members = [{
            "account_id": m["account_id"], "display_name": m["display_name"], "email": m.get("email", ""),
            "tickets": results.get(m["account_id"], {}).get("tickets", []),
            "total_minutes": results.get(m["account_id"], {}).get("total_minutes", 0),
        } for m in team]
        members.sort(key=lambda m: m["total_minutes"], reverse=True)
        return {"date": date_key, "date_label": datetime.strptime(date_key, "%Y-%m-%d").strftime("%a, %b %d, %Y"),
                "members": members, "daily_target_mins": DAILY_TARGET_MINS}

    def open_team_window(self):
        webview.create_window("Team Worklogs - MyWorkDay", html=TEAM_HTML, js_api=self,
                               width=760, height=720, min_size=(600, 480))
        return True


HTML = r"""
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
  :root {
    --ink:#1c2430; --muted:#6b7280; --line:#e5e7eb; --page:#eef3fb; --card:#ffffff;
    --blue:#2563eb; --blue-wash:#dbeafe; --green:#16a34a; --green-wash:#dcfce7;
    --purple:#7c3aed; --purple-wash:#ede9fe; --orange:#ea580c; --orange-wash:#ffedd5;
    --red:#dc2626; --red-wash:#fee2e2; --shadow:0 1px 2px rgba(0,0,0,.04), 0 4px 14px -4px rgba(0,0,0,.08);
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--page); color:var(--ink); font-family:"Segoe UI",system-ui,sans-serif; font-size:13.5px; }
  .wrap { margin:0 auto; padding:16px 24px 28px; display:flex; flex-direction:column; gap:14px; }

  .topbar { display:flex; align-items:center; justify-content:space-between; }
  .brand { display:flex; align-items:center; gap:10px; }
  .brand .logo { font-size:22px; }
  .brand h1 { font-size:17px; margin:0; }
  .brand .tag { font-size:11px; color:var(--muted); }
  .topbar-right { display:flex; align-items:center; gap:10px; }
  .sync { display:flex; align-items:center; gap:6px; font-size:11.5px; color:var(--muted); }
  .break-status { font-size:11px; font-weight:600; padding:4px 10px; border-radius:999px;
    background:var(--green-wash); color:var(--green); white-space:nowrap; }
  .break-status.due { background:var(--orange-wash); color:#9a3412; }
  .dot { width:7px; height:7px; border-radius:50%; background:var(--green); display:inline-block; }
  .icon-btn { width:40px; height:40px; border-radius:12px; border:none; background:var(--card);
    cursor:pointer; font-size:14px; display:flex; align-items:center; justify-content:center; box-shadow:var(--shadow); }
  .icon-btn:hover { background:#f9fafb; }
  .icon-btn.spinning { animation: spin 0.9s linear infinite; }
  @keyframes spin { from{transform:rotate(0deg)} to{transform:rotate(360deg)} }

  .loading-overlay { position:fixed; inset:0; background:var(--page); z-index:900;
    display:flex; flex-direction:column; align-items:center; justify-content:center; gap:14px; }
  .loading-overlay[hidden] { display:none; }
  .loading-spinner { width:34px; height:34px; border-radius:50%; border:3px solid var(--line);
    border-top-color:var(--blue); animation:spin 0.8s linear infinite; }
  .loading-text { color:var(--muted); font-size:13px; }

  .card { background:var(--card); border-radius:14px; box-shadow:var(--shadow); border:1px solid var(--line); }
  .card-pad { padding:16px; }

  .greeting { display:flex; align-items:center; gap:16px;
    background:linear-gradient(135deg,#eaf1fd 0%,#f4eefc 100%); border-color:#dce6f7; }
  .avatar { width:52px; height:52px; border-radius:50%; background:var(--blue); color:#fff; font-weight:700;
    display:flex; align-items:center; justify-content:center; font-size:16px; flex:none; }
  .greeting .hi { font-size:12.5px; color:var(--muted); font-weight:500; }
  .greeting h2 { margin:2px 0 0; font-size:22px; font-weight:700; }
  .greeting .sub { color:var(--muted); font-size:11.5px; margin-top:3px; }
  .greeting .quote { font-style:italic; color:var(--muted); font-size:12px; margin-top:6px; }
  .greeting-cal { margin-left:auto; background:#fff; border:1px solid #dce6f7; border-radius:10px;
    padding:8px 14px; font-size:11.5px; font-weight:600; color:#1e3a8a; white-space:nowrap; flex:none; }
  .greeting-deco { display:flex; align-items:center; gap:14px; flex:none; }
  .greeting-deco .tagline { text-align:right; font-size:12px; font-weight:600; color:#1e3a8a;
    max-width:110px; line-height:1.4; border-right:3px solid var(--blue); padding-right:10px; }

  .stat-row { display:grid; grid-template-columns:repeat(4,1fr); gap:12px; }
  .stat { display:flex; gap:10px; align-items:flex-start; min-width:0; }
  .stat > div { min-width:0; }
  .stat .badge { width:38px; height:38px; border-radius:10px; display:flex; align-items:center; justify-content:center; font-size:17px; flex:none; }
  .stat .num { font-size:18px; font-weight:700; line-height:1.1; }
  .stat .lbl { font-size:11px; color:var(--muted); }
  .stat .sub { font-size:10.5px; color:var(--muted); margin-top:4px; }
  .progress { height:5px; background:var(--line); border-radius:3px; overflow:hidden; margin-top:4px; width:100%; }
  .progress > div { height:100%; background:var(--green); }
  .badge.b-green { background:var(--green-wash); color:var(--green); }
  .badge.b-blue { background:var(--blue-wash); color:var(--blue); }
  .badge.b-purple { background:var(--purple-wash); color:var(--purple); }
  .badge.b-orange { background:var(--orange-wash); color:var(--orange); }

  .grid2 { display:grid; grid-template-columns:1.3fr 1fr; gap:14px; align-items:start; min-width:0; }
  .grid2 > .card { min-width:0; }
  .col { display:flex; flex-direction:column; gap:14px; min-width:0; }

  @media (max-width: 920px) {
    .grid2 { grid-template-columns: 1fr; }
  }
  @media (max-width: 680px) {
    .stat-row { grid-template-columns: repeat(2,1fr); }
    .qa-grid { grid-template-columns: 1fr; }
    .greeting { flex-wrap:wrap; }
    .greeting-deco { display:none; }
  }
  .card-head { display:flex; align-items:center; justify-content:space-between; margin-bottom:8px; }
  .card-head h3 { margin:0; font-size:13.5px; display:flex; align-items:center; gap:6px; }
  .card-head h3 svg { flex:none; color:var(--muted); }
  select, .link-btn { font-family:inherit; font-size:12px; }
  select { padding:4px 6px; border-radius:6px; border:1px solid var(--line); background:#fff; }
  a, .link { color:var(--blue); text-decoration:none; cursor:pointer; }
  a:hover, .link:hover { text-decoration:underline; }

  .ticket-row { border-top:1px solid var(--line); }
  .ticket-row:first-child { border-top:none; }
  .ticket-summary { display:flex; align-items:center; gap:10px; padding:9px 0; cursor:pointer; list-style:none; }
  .ticket-summary::-webkit-details-marker { display:none; }
  .ticon { width:26px; height:26px; border-radius:8px; display:flex; align-items:center; justify-content:center; flex:none; }
  .ticon.merged { background:var(--green-wash); color:var(--green); }
  .ticon.reverted { background:var(--red-wash); color:var(--red); }
  .ticon.progress { background:var(--purple-wash); color:var(--purple); }
  .ticket-row .tkey { font-family:Consolas,monospace; font-weight:700; font-size:12px; color:var(--blue); width:66px; flex:none; }
  .ticket-row .tsum { flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .ticket-row .tstatus { flex:none; }
  .status-pill { font-size:10.5px; font-weight:600; padding:2px 8px; border-radius:999px; white-space:nowrap; }
  .status-pill.cat-new { background:#e5e7eb; color:#4b5563; }
  .status-pill.cat-indeterminate { background:#fef3c7; color:#92400e; }
  .status-pill.cat-done { background:var(--green-wash); color:var(--green); }
  .ticket-row .tworked { font-weight:600; font-size:12px; flex:none; width:78px; text-align:right;
    display:flex; flex-direction:column; align-items:flex-end; gap:1px; }
  .ticket-row .tworked .logged { font-weight:500; font-size:9.5px; color:var(--muted); white-space:nowrap; }
  .tchev { flex:none; color:var(--muted); transition:transform .15s ease; }
  .ticket-row[open] .tchev { transform:rotate(90deg); }
  .ticket-body { padding:0 0 10px 36px; display:flex; flex-direction:column; gap:6px; }
  .taction { display:flex; gap:8px; font-size:11.5px; }
  .taction .time { color:var(--muted); width:64px; flex:none; }
  .taction .tlabel { font-weight:600; width:78px; flex:none; }
  .taction .tmsg { color:var(--ink); flex:1; min-width:0; }

  #ticketList { max-height:380px; overflow-y:auto; margin-right:-6px; padding-right:6px; }
  #ticketList::-webkit-scrollbar { width:7px; }
  #ticketList::-webkit-scrollbar-thumb { background:var(--line); border-radius:4px; }
  #ticketList::-webkit-scrollbar-thumb:hover { background:#c7ced6; }

  .wk-actions { display:flex; gap:8px; margin-bottom:8px; }
  .wk-btn { font-family:inherit; font-size:11.5px; font-weight:600; padding:7px 12px; border-radius:8px;
    border:1px solid var(--line); background:#fff; cursor:pointer; color:var(--ink); }
  .wk-btn.primary { background:var(--blue); color:#fff; border-color:var(--blue); }
  .wk-btn.ghost { background:var(--blue-wash); color:var(--blue); border-color:transparent; }
  .wk-btn.warn { background:var(--orange-wash); color:#9a3412; border-color:transparent; }
  .wk-btn.danger { background:var(--red-wash); color:var(--red); border-color:transparent; margin-right:auto; }
  .wk-btn:hover { filter:brightness(0.97); }
  .wk-viewall { text-align:right; margin-top:8px; font-size:11.5px; }
  .needs-log-row { padding:10px 12px; border-radius:10px; background:var(--orange-wash); margin-bottom:8px; }
  .needs-log-row:last-child { margin-bottom:0; }
  .needs-log-row .nl-top { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
  .needs-log-row .nl-top a { font-family:Consolas,monospace; font-weight:700; color:var(--blue); }
  .needs-log-row .nl-kind { font-size:10.5px; font-weight:600; color:#b45309; }
  .needs-log-row .nl-worked { margin-left:auto; font-size:13px; font-weight:700; color:#9a3412; }
  .needs-log-row .nl-sum { font-size:12px; margin-top:5px; color:var(--ink); }
  .needs-log-row .nl-meta { font-size:10.5px; color:#9a3412; margin-top:5px; display:flex; gap:10px; flex-wrap:wrap; }
  .tag { font-size:9.5px; padding:1px 6px; border-radius:999px; margin-left:6px; }
  .tag.merged { background:var(--green-wash); color:var(--green); }
  .tag.reverted { background:var(--red-wash); color:var(--red); }

  .donut-wrap { display:flex; align-items:center; gap:16px; }
  .donut { width:130px; height:130px; border-radius:50%; flex:none; display:flex; align-items:center; justify-content:center; }
  .donut .hole { width:74px; height:74px; border-radius:50%; background:var(--card); display:flex; flex-direction:column; align-items:center; justify-content:center; }
  .donut .hole b { font-size:14px; }
  .donut .hole span { font-size:9.5px; color:var(--muted); }
  .legend { display:flex; flex-direction:column; gap:10px; font-size:11.5px; flex:1; min-width:0; }
  .legend .row { display:flex; align-items:flex-start; gap:8px; }
  .legend .sw { width:10px; height:10px; border-radius:50%; flex:none; margin-top:3px; }
  .legend .body { flex:1; min-width:0; }
  .legend .key { font-weight:700; font-size:12.5px; }
  .legend .mins { color:var(--muted); font-size:11px; margin-top:1px; }
  .legend .pct { font-weight:700; font-size:12.5px; flex:none; }
  .quotebox { margin-top:12px; background:var(--blue-wash); border-radius:8px; padding:8px 12px;
    font-size:11.5px; color:#1e40af; font-style:italic; }

  .agenda-count { font-size:11px; font-weight:700; padding:3px 10px; border-radius:999px;
    background:var(--blue-wash); color:var(--blue); }
  .agenda-list { position:relative; padding-left:14px; }
  .agenda-item { position:relative; padding-bottom:14px; }
  .agenda-item:last-child { padding-bottom:0; }
  .agenda-item::before { content:""; position:absolute; left:-14px; top:3px; width:8px; height:8px;
    border-radius:50%; background:var(--dot-color, var(--blue)); }
  .agenda-item::after { content:""; position:absolute; left:-11px; top:13px; width:1px; bottom:-2px; background:var(--line); }
  .agenda-item:last-child::after { display:none; }
  .agenda-item .a-time { font-size:11px; color:var(--muted); font-weight:600; }
  .agenda-item .a-title { font-size:13px; font-weight:700; margin-top:1px; }
  .agenda-item .a-sub { font-size:11px; color:var(--muted); margin-top:1px; }
  .agenda-item.past { opacity:0.45; }
  .agenda-item.past::before { background:var(--muted) !important; }

  .team-row { display:flex; flex-direction:column; gap:5px; padding:8px 0; border-top:1px solid var(--line); }
  .team-row:first-child { border-top:none; }
  .team-row-top { display:flex; align-items:center; gap:10px; cursor:pointer; }
  .team-row-top:hover { opacity:0.8; }
  .team-avatar { width:26px; height:26px; border-radius:50%; background:var(--purple-wash); color:var(--purple);
    display:flex; align-items:center; justify-content:center; font-size:11px; font-weight:700; flex:none; }
  .team-name { font-size:12.5px; font-weight:600; flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .team-mins { font-size:12.5px; font-weight:700; color:var(--blue); flex:none; }
  .team-chev { color:var(--muted); flex:none; transition:transform .15s; }
  .team-row.open .team-chev { transform:rotate(90deg); }
  .team-strength { height:5px; border-radius:999px; background:var(--line); overflow:hidden; margin-left:36px; }
  .team-strength-fill { height:100%; border-radius:999px; transition:width 0.4s ease; }
  .team-strength-fill.low { background:var(--red); }
  .team-strength-fill.mid { background:var(--orange); }
  .team-strength-fill.high { background:var(--green); }
  .team-date-input { font-family:inherit; font-size:11px; padding:4px 8px; border:1px solid var(--line);
    border-radius:8px; background:#fff; color:var(--ink); }
  .date-controls { display:flex; align-items:center; gap:6px; }
  .team-tickets { display:none; flex-direction:column; gap:4px; margin:6px 0 2px 36px; }
  .team-row.open .team-tickets { display:flex; }
  .team-ticket-row { display:flex; align-items:center; gap:8px; font-size:11.5px; }
  .team-ticket-key { font-weight:600; color:var(--blue); flex:none; width:70px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .team-ticket-summary { flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:var(--muted); }
  .team-ticket-mins { flex:none; font-weight:600; }
  .team-open-link { font-size:10.5px; color:var(--blue); text-decoration:underline; cursor:pointer; margin:2px 0 0 36px; }
  .team-load-prompt { display:flex; flex-direction:column; align-items:center; gap:8px; padding:14px 0 6px; }
  .team-load-prompt .s { font-size:11.5px; color:var(--muted); }
  .team-load-btn { font-family:inherit; font-size:12px; font-weight:600; padding:7px 16px; border-radius:9px;
    border:1px solid var(--blue); background:var(--blue-wash); color:var(--blue); cursor:pointer; }
  .team-load-btn:hover { background:#cfe0fc; }

  .progress-bar { position:relative; height:6px; border-radius:999px; background:var(--blue-wash);
    overflow:hidden; margin:4px 0 2px; }
  .progress-fill { position:absolute; top:0; height:100%; width:40%; border-radius:999px;
    background:var(--blue); animation:progress-slide 1.1s ease-in-out infinite; }
  @keyframes progress-slide { 0% { left:-40%; } 100% { left:100%; } }

  .timeline { position:relative; padding-left:14px; }
  .timeline .item { position:relative; padding-bottom:12px; }
  .timeline .item:last-child { padding-bottom:0; }
  .timeline .item::before { content:""; position:absolute; left:-14px; top:3px; width:7px; height:7px; border-radius:50%; background:var(--blue); }
  .timeline .item.t-commit::before { background:var(--purple); }
  .timeline .item.t-pr-merge::before { background:var(--green); }
  .timeline .item.t-base-merge::before { background:var(--blue); }
  .timeline .item.t-revert::before { background:var(--red); }
  .timeline .item::after { content:""; position:absolute; left:-11px; top:12px; width:1px; bottom:-2px; background:var(--line); }
  .timeline .item:last-child::after { display:none; }
  .timeline .time { font-size:10.5px; color:var(--muted); }
  .timeline .msg { font-size:12px; }
  .timeline .msg b { color:var(--ink); }
  .timeline .msg b .tl { color:var(--blue); }
  .timeline .msg .sub { color:var(--muted); font-size:11px; margin-top:2px; }

  .qa-grid { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
  .qa { border-radius:10px; padding:10px 12px; cursor:pointer; border:1px solid transparent; }
  .qa:hover { filter:brightness(0.97); }
  .qa .t { font-weight:600; font-size:12.5px; display:flex; align-items:center; gap:6px; }
  .qa .t svg { flex:none; }
  .qa .s { font-size:10.5px; color:var(--muted); }
  .qa.green { background:var(--green-wash); }
  .qa.blue { background:var(--blue-wash); }
  .qa.purple { background:var(--purple-wash); }
  .qa.orange { background:var(--orange-wash); }

  .qa-wide { display:flex; align-items:center; justify-content:space-between; gap:10px;
    background:var(--blue-wash); border-radius:10px; padding:12px 14px; margin-top:10px; cursor:pointer; }
  .qa-wide:hover { filter:brightness(0.97); }
  .qa-wide .t { font-weight:600; font-size:12.5px; display:flex; align-items:center; gap:8px; color:#1e3a8a; }
  .qa-wide .s { font-size:10.5px; color:var(--muted); margin-top:2px; }
  .qa-wide .chev { color:var(--blue); flex:none; }

  .footer { display:flex; justify-content:space-between; font-size:11px; color:var(--muted); padding:2px 4px; }
  .empty { color:var(--muted); font-size:12px; padding:8px 0; }

  .modal-overlay[hidden] { display:none; }
  .modal-overlay { position:fixed; inset:0; background:rgba(15,23,42,.45); display:flex;
    align-items:center; justify-content:center; z-index:50; }
  .modal { background:var(--card); border-radius:14px; width:360px; max-width:calc(100vw - 32px);
    box-shadow:0 20px 40px -8px rgba(0,0,0,.25); max-height:calc(100vh - 48px);
    display:flex; flex-direction:column; overflow:hidden; }
  .modal.wide { width:600px; }
  .modal-head { flex:none; display:flex; align-items:center; justify-content:space-between; padding:14px 16px; border-bottom:1px solid var(--line); }
  .modal-head h3 { margin:0; font-size:14.5px; }
  .modal-close { border:none; background:none; font-size:18px; line-height:1; cursor:pointer; color:var(--muted); }
  .modal-body { padding:14px 16px; display:flex; flex-direction:column; gap:12px; flex:1; min-height:0; overflow-y:auto; }
  .settings-label { font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:.03em; color:var(--muted); }
  .settings-section { display:flex; flex-direction:column; gap:6px; }
  .settings-section label { font-size:11.5px; color:var(--muted); margin-top:4px; }
  .settings-section input { font-family:inherit; font-size:12.5px; padding:7px 9px; border-radius:8px;
    border:1px solid var(--line); background:#fff; width:100%; }
  .settings-hint { font-size:10.5px; color:var(--muted); }
  .settings-step { display:flex; align-items:center; gap:8px; font-size:12px; }
  .step-num { width:18px; height:18px; border-radius:50%; background:var(--blue-wash); color:var(--blue);
    font-size:10.5px; font-weight:700; display:flex; align-items:center; justify-content:center; flex:none; }
  .settings-advanced { font-size:11px; margin-top:2px; }
  .settings-advanced summary { color:var(--muted); cursor:pointer; }
  .settings-advanced input { margin-top:4px; }
  .settings-advanced label { display:block; }
  .settings-status { font-size:11.5px; min-height:14px; }
  .settings-status.ok { color:var(--green); }
  .settings-status.err { color:var(--red); }
  .break-reminder-row { display:flex; align-items:center; gap:8px; font-size:12px; color:var(--muted); }
  .settings-section.readonly { border-top:1px solid var(--line); padding-top:10px; }
  .settings-row { display:flex; justify-content:space-between; font-size:11.5px; padding:2px 0; }
  .settings-row span:first-child { color:var(--muted); }
  .modal-foot { flex:none; display:flex; justify-content:flex-end; gap:8px; padding:12px 16px; border-top:1px solid var(--line); }

</style>
</head>
<body>

<div class="loading-overlay" id="loadingOverlay">
  <div class="loading-spinner"></div>
  <div class="loading-text">Loading your data&hellip;</div>
</div>

<div class="wrap">

  <div class="topbar">
    <div class="brand">
      <span class="logo"><svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="#2563eb" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><line x1="20" y1="4" x2="6" y2="18"/><line x1="6" y1="18" x2="4" y2="21"/><line x1="16" y1="8" x2="11" y2="8"/><line x1="13" y1="11" x2="9" y2="11"/><line x1="10" y1="14" x2="7" y2="14"/></svg></span>
      <div><h1>MyWorkDay</h1><div class="tag">Track &bull; Focus &bull; Deliver</div></div>
    </div>
    <div class="topbar-right">
      <div class="break-status" id="breakStatus" hidden></div>
      <div class="sync"><span class="dot"></span><span id="syncedAt">Last synced —</span></div>
      <button class="icon-btn" id="refreshBtn" title="Rescan"><svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-3-6.7"/><polyline points="21 3 21 9 15 9"/></svg></button>
      <button class="icon-btn" id="settingsBtn" title="Settings"><svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="3"/><line x1="12" y1="2" x2="12" y2="5"/><line x1="12" y1="19" x2="12" y2="22"/><line x1="2" y1="12" x2="5" y2="12"/><line x1="19" y1="12" x2="22" y2="12"/><line x1="4.9" y1="4.9" x2="7" y2="7"/><line x1="17" y1="17" x2="19.1" y2="19.1"/><line x1="4.9" y1="19.1" x2="7" y2="17"/><line x1="17" y1="7" x2="19.1" y2="4.9"/></svg></button>
    </div>
  </div>

  <div class="card card-pad greeting">
    <div class="avatar" id="avatar">?</div>
    <div>
      <div class="hi" id="greetHi">Hello,</div>
      <h2 id="greetName">there</h2>
      <div class="quote" id="quote"></div>
    </div>
    <div class="greeting-cal" id="greetingCal" hidden></div>
    <div class="greeting-deco">
      <div class="tagline">Build today for a better tomorrow</div>
      <svg viewBox="0 0 200 130" width="150" height="97">
        <rect x="14" y="104" width="160" height="7" rx="2" fill="#cbd5e1"/>
        <rect x="68" y="52" width="70" height="48" rx="4" fill="#1e293b"/>
        <rect x="74" y="58" width="58" height="36" rx="2" fill="#0f172a"/>
        <line x1="80" y1="67" x2="118" y2="67" stroke="#38bdf8" stroke-width="2.5"/>
        <line x1="80" y1="75" x2="108" y2="75" stroke="#38bdf8" stroke-width="2.5"/>
        <line x1="80" y1="83" x2="124" y2="83" stroke="#38bdf8" stroke-width="2.5"/>
        <rect x="96" y="100" width="14" height="7" fill="#334155"/>
        <path d="M38 104 L43 84 Q58 74 73 84 L78 104 Z" fill="#2563eb"/>
        <circle cx="58" cy="58" r="13" fill="#f5b25a"/>
        <rect x="150" y="90" width="15" height="15" rx="2" fill="#b45309"/>
        <circle cx="154" cy="85" r="6" fill="#22c55e"/>
        <circle cx="163" cy="83" r="6" fill="#16a34a"/>
        <circle cx="158" cy="77" r="6" fill="#22c55e"/>
        <rect x="26" y="94" width="12" height="10" rx="2" fill="#ffffff" stroke="#cbd5e1" stroke-width="1.2"/>
      </svg>
    </div>
  </div>

  <div class="stat-row">
    <div class="card card-pad stat">
      <div class="badge b-green"><svg viewBox="0 0 24 24" width="19" height="19" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15.5 14"/></svg></div>
      <div style="flex:1">
        <div class="num" id="statWorked">—</div><div class="lbl">Logged to Jira (selected day, actual)</div>
        <div class="sub" id="statTarget"></div>
        <div class="sub" id="statLogged"></div>
        <div class="progress"><div id="statProgress" style="width:0%"></div></div>
      </div>
    </div>
    <div class="card card-pad stat">
      <div class="badge b-blue"><svg viewBox="0 0 24 24" width="19" height="19" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="3" width="14" height="18" rx="2"/><line x1="8" y1="8" x2="16" y2="8"/><line x1="8" y1="12" x2="16" y2="12"/><line x1="8" y1="16" x2="13" y2="16"/></svg></div>
      <div><div class="num" id="statTickets">—</div><div class="lbl">Tickets touched</div>
        <div class="sub" id="statInProgress"></div></div>
    </div>
    <div class="card card-pad stat">
      <div class="badge b-purple"><svg viewBox="0 0 24 24" width="19" height="19" fill="currentColor"><path d="M13 2 3 14h7l-1 8 10-12h-7l1-8z"/></svg></div>
      <div><div class="num" id="statCommits">—</div><div class="lbl">Commits</div>
        <div class="sub" id="statCommitsDelta"></div></div>
    </div>
    <div class="card card-pad stat">
      <div class="badge b-orange"><svg viewBox="0 0 24 24" width="19" height="19" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="12" cy="5" rx="7" ry="2.5"/><path d="M5 5v14a7 2.5 0 0 0 14 0V5"/><path d="M5 12a7 2.5 0 0 0 14 0"/></svg></div>
      <div><div class="num" id="statRepos">—</div><div class="lbl">Repos active</div>
        <div class="sub" id="statReposList"></div></div>
    </div>
  </div>

  <div class="grid2">
    <div class="col">
      <div class="card card-pad">
        <div class="card-head">
          <h3><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg> My Work</h3>
          <div class="date-controls">
            <input type="date" id="dateJumpPicker" class="team-date-input" title="Jump to any date">
            <select id="dateSelect"></select>
          </div>
        </div>
        <div class="wk-actions">
          <button class="wk-btn ghost" onclick="openJira()">&#128279; Select from Jira</button>
          <button id="needsLoggingPill" class="wk-btn warn" onclick="openNeedsLogging()" hidden></button>
        </div>
        <div id="ticketList"></div>
        <div class="wk-viewall"><a onclick="showWeek()">View all work &rarr;</a></div>
      </div>

      <div class="card card-pad">
        <div class="card-head"><h3><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 12 8 12 10 6 14 18 16 12 21 12"/></svg> Recent Activity</h3></div>
        <div class="timeline" id="activityList"></div>
      </div>
    </div>

    <div class="col">
      <div class="card card-pad">
        <div class="card-head"><h3><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><line x1="12" y1="12" x2="12" y2="3"/><line x1="12" y1="12" x2="18.5" y2="16"/></svg> Time Breakdown</h3></div>
        <div class="donut-wrap">
          <div class="donut" id="donutChart"><div class="hole"><b id="donutTotal">0m</b><span>Total</span></div></div>
          <div class="legend" id="donutLegend"></div>
        </div>
        <div class="quotebox" id="quoteBox">💡 <span id="quoteText"></span></div>
      </div>

      <div class="card card-pad" id="agendaCard" hidden>
        <div class="card-head">
          <h3><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg> Today's Agenda</h3>
          <span class="agenda-count" id="agendaCount"></span>
        </div>
        <div class="agenda-list" id="agendaList"></div>
      </div>

      <div class="card card-pad">
        <div class="card-head"><h3><svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor"><polygon points="13 2 3 14 11 14 9 22 21 10 13 10"/></svg> Quick Actions</h3></div>
        <div class="qa-grid">
          <div class="qa green" onclick="doRefresh()"><div class="t"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-3-6.7"/><polyline points="21 3 21 9 15 9"/></svg> Rescan Now</div><div class="s">Re-read git history</div></div>
          <div class="qa blue" onclick="openJira()"><div class="t"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg> Open Jira</div><div class="s">In your browser</div></div>
          <div class="qa purple" onclick="alert('Pick a day from the My Work dropdown to see its history.')"><div class="t"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15.5 14"/></svg> Time History</div><div class="s">Browse past days</div></div>
          <div class="qa orange" onclick="openSettings()"><div class="t"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="3"/><line x1="12" y1="2" x2="12" y2="5"/><line x1="12" y1="19" x2="12" y2="22"/><line x1="2" y1="12" x2="5" y2="12"/><line x1="19" y1="12" x2="22" y2="12"/><line x1="4.9" y1="4.9" x2="7" y2="7"/><line x1="17" y1="17" x2="19.1" y2="19.1"/><line x1="4.9" y1="19.1" x2="7" y2="17"/><line x1="17" y1="7" x2="19.1" y2="4.9"/></svg> Settings</div><div class="s">Jira &amp; scan config</div></div>
        </div>
        <div class="qa-wide" onclick="showWeek()">
          <div><div class="t"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="20" x2="12" y2="10"/><line x1="18" y1="20" x2="18" y2="4"/><line x1="6" y1="20" x2="6" y2="16"/></svg> Weekly Summary</div><div class="s">View your last 7 days total</div></div>
          <svg class="chev" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>
        </div>
        <div class="qa-wide" onclick="window.pywebview.api.open_team_window()">
          <div><div class="t"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg> Team Worklogs</div><div class="s">Search &amp; track teammates' logged time</div></div>
          <svg class="chev" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>
        </div>
      </div>

      <div class="card card-pad" id="teamWorklogCard" hidden>
        <div class="card-head">
          <h3><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg> Team Worklog</h3>
          <input type="date" id="teamWorklogDateInput" class="team-date-input">
        </div>
        <div id="teamWorklogList"></div>
      </div>
    </div>
  </div>

  <div class="footer"><span><span class="dot"></span> Ready</span><span>MyWorkDay &middot; local, read-only</span></div>
</div>

<div class="modal-overlay" id="needsLoggingOverlay" hidden>
  <div class="modal wide">
    <div class="modal-head"><h3>Needs time logged</h3><button class="modal-close" onclick="closeNeedsLogging()">&times;</button></div>
    <div class="modal-body" id="needsLoggingBody"></div>
    <div class="modal-foot"><button class="wk-btn" onclick="closeNeedsLogging()">Close</button></div>
  </div>
</div>

<div class="modal-overlay" id="settingsOverlay" hidden>
  <div class="modal">
    <div class="modal-head"><h3>Settings</h3><button class="modal-close" onclick="closeSettings()">&times;</button></div>
    <div class="modal-body">
      <div class="settings-section">
        <div class="settings-label">Jira integration</div>
        <div class="settings-step"><span class="step-num">1</span>Open the token page and create a token.</div>
        <button class="wk-btn" onclick="openUrl('https://id.atlassian.com/manage-profile/security/api-tokens')">&#128279; Open Atlassian token page</button>
        <div class="settings-step" style="margin-top:6px"><span class="step-num">2</span>Paste it below.</div>
        <input id="cfgJiraToken" type="password" placeholder="Paste your Jira API token here" autofocus>
        <details class="settings-advanced">
          <summary>Base URL / email (auto-filled, only change if different)</summary>
          <label>Base URL</label>
          <input id="cfgJiraUrl" placeholder="https://yourcompany.atlassian.net">
          <label>Email</label>
          <input id="cfgJiraEmail" placeholder="you@yourcompany.com">
        </details>
        <div class="settings-hint">Stored locally in <code>%LOCALAPPDATA%\MyWorkDay\config.json</code>, never sent anywhere but Jira.</div>
        <div class="settings-status" id="jiraStatusMsg"></div>
      </div>
      <div class="settings-section">
        <div class="settings-label">Google Calendar</div>
        <button class="wk-btn primary" onclick="connectGoogleCalendar()">&#128197; Connect Google</button>
        <div class="settings-hint">Opens your browser for Google sign-in - only the refresh token is stored, locally.</div>
        <details class="settings-advanced">
          <summary>Advanced: use your own OAuth credentials</summary>
          <label>Client ID</label>
          <input id="cfgGoogleClientId" placeholder="xxxxx.apps.googleusercontent.com">
          <label>Client Secret</label>
          <input id="cfgGoogleClientSecret" type="password" placeholder="Paste your OAuth client secret">
          <div class="settings-hint">Create an OAuth client (Desktop app type, Calendar API enabled) at
            <a onclick="openUrl('https://console.cloud.google.com/apis/credentials')">console.cloud.google.com/apis/credentials</a>
            and fill both fields in above - Connect will use these instead of the app's default.</div>
        </details>
        <div class="settings-status" id="calendarStatusMsg"></div>
      </div>
      <div class="settings-section">
        <div class="settings-label">Break reminder</div>
        <div class="settings-hint">Get a Windows notification after this many minutes of continuous keyboard/mouse activity. Set to 0 to turn it off.</div>
        <div class="break-reminder-row">
          <input id="cfgBreakReminder" type="number" min="0" step="5" style="width:80px">
          <span>minutes</span>
          <button class="wk-btn" onclick="saveBreakReminder()">Save</button>
        </div>
        <div class="settings-status" id="breakReminderStatusMsg"></div>
      </div>
      <div class="settings-section readonly">
        <div class="settings-label">Scan settings (env vars, restart to change)</div>
        <div class="settings-row"><span>Repos root</span><span id="cfgRepoRoot"></span></div>
        <div class="settings-row"><span>Git author</span><span id="cfgAuthor"></span></div>
        <div class="settings-row"><span>Lookback</span><span id="cfgLookback"></span></div>
        <div class="settings-row"><span>Daily target</span><span id="cfgTarget"></span></div>
      </div>
    </div>
    <div class="modal-foot">
      <button class="wk-btn danger" onclick="clearSettings()">Clear settings</button>
      <button class="wk-btn" onclick="closeSettings()">Cancel</button>
      <button class="wk-btn primary" onclick="saveJiraSettings()">Save &amp; Test</button>
    </div>
  </div>
</div>

<script>
let state = null;
const DONUT_COLORS = ["#2563eb","#7c3aed","#16a34a","#ea580c","#dc2626","#0891b2","#ca8a04","#be185d"];
const KIND_LABEL = {commit:"Commit", "pr-merge":"Merged PR", "base-merge":"Synced", revert:"Reverted"};
function statusPillHtml(status, category) {
  if (!status) return "";
  const cat = category || "new";
  return '<span class="status-pill cat-'+cat+'">'+status+'</span>';
}
// Decorative only - never a data claim. Picked deterministically from the selected date so
// it doesn't flicker to a different line on every re-render of the same day.
const QUOTES = [
  "Small steps make big progress.",
  "Consistent progress beats perfect days.",
  "Every commit is a step forward.",
  "Focus on today's ticket, not the whole backlog.",
  "Shipped is better than perfect.",
];
function quoteForDate(dateKey) {
  let h = 0;
  for (const ch of (dateKey || "")) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return QUOTES[h % QUOTES.length];
}

function el(id) { return document.getElementById(id); }

function greetingWord(hour) {
  if (hour < 12) return "Good morning";
  if (hour < 17) return "Good afternoon";
  return "Good evening";
}

function beginLoading() {
  el("loadingOverlay").hidden = false;
  // Chromium treats <html>, not <body>, as the real scrolling element here - suppressing
  // overflow on both avoids a flash of an empty-page scrollbar while there's nothing to show yet.
  document.documentElement.style.overflow = "hidden";
  document.body.style.overflow = "hidden";
}
function endLoading() {
  el("loadingOverlay").hidden = true;
  document.documentElement.style.overflow = "";
  document.body.style.overflow = "";
}

function render(data) {
  state = data;
  endLoading();
  el("syncedAt").textContent = "Last synced " + data.synced_at;
  el("avatar").textContent = data.initials;
  el("greetHi").textContent = greetingWord(data.hour) + ",";
  el("greetName").textContent = data.author;
  renderCalendar(data);
  const q = quoteForDate(data.selected_date);
  el("quote").textContent = "“" + q + "”";
  el("quoteText").textContent = q;

  const s = data.stats;
  el("statWorked").textContent = s.logged;
  el("statTarget").textContent = "Daily target: " + Math.round(s.daily_target_mins/60) + "h";
  el("statLogged").textContent = data.config.jira_connected
    ? "Git-based estimate (not actual): " + s.worked
    : "Connect Jira in Settings to see actual logged time";
  el("statProgress").style.width = s.target_pct + "%";
  el("statTickets").textContent = s.tickets;
  el("statInProgress").textContent = s.in_progress + " not yet merged";
  el("statCommits").textContent = s.commits;
  el("statCommitsDelta").textContent = s.commits_delta === null ? "no prior day to compare" :
    (s.commits_delta >= 0 ? "+" : "") + s.commits_delta + " from prior day";
  el("statRepos").textContent = s.repos;
  el("statReposList").textContent = s.repos_active;

  el("dateJumpPicker").value = data.selected_date;

  const sel = el("dateSelect");
  sel.innerHTML = "";
  // The dropdown only ever lists dates that actually have activity (plus today) - jumping to
  // an arbitrary older date via the date picker won't be in that list, so add it here just so
  // the dropdown's own displayed value stays truthful about what's currently shown.
  let dates = data.dates.slice();
  if (!dates.includes(data.selected_date)) {
    dates.push(data.selected_date);
    dates.sort().reverse();
  }
  dates.forEach(d => {
    const opt = document.createElement("option");
    opt.value = d; opt.textContent = d === data.selected_date ? data.selected_date_label : d;
    if (d === data.selected_date) opt.selected = true;
    sel.appendChild(opt);
  });

  const pill = el("needsLoggingPill");
  const n = (data.needs_logging || []).length;
  if (n) {
    pill.hidden = false;
    pill.textContent = "⚠ " + n + " ticket" + (n === 1 ? "" : "s") + " need" + (n === 1 ? "s" : "") + " time logged";
  } else {
    pill.hidden = true;
  }

  const list = el("ticketList");
  list.innerHTML = "";
  if (!data.tickets.length) {
    list.innerHTML = '<div class="empty">No tickets recorded for this day.</div>';
  }
  data.tickets.forEach(t => {
    const row = document.createElement("details");
    row.className = "ticket-row";
    const iconClass = t.reverted ? "reverted" : (t.merged ? "merged" : "progress");
    const iconSvg = t.reverted
      ? '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 9v4"/><path d="M12 17h.01"/><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/></svg>'
      : t.merged
        ? '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>'
        : '<svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor"><path d="M13 2 3 14h7l-1 8 10-12h-7l1-8z"/></svg>';
    const tags = (t.merged ? '<span class="tag merged">merged</span>' : "") +
                 (t.reverted ? '<span class="tag reverted">reverted</span>' : "");
    const keyHtml = t.jira_url
      ? '<a onclick="event.preventDefault();event.stopPropagation();openUrl(\''+t.jira_url+'\')">'+t.key+'</a>'
      : t.key;
    const actionsHtml = (t.actions || []).map(a =>
      '<div class="taction"><span class="time">'+a.time+'</span>' +
      '<span class="tlabel">'+(KIND_LABEL[a.type]||a.type)+'</span>' +
      '<span class="tmsg">'+a.message+'</span></div>'
    ).join("");
    row.innerHTML =
      '<summary class="ticket-summary">' +
        '<span class="ticon '+iconClass+'">'+iconSvg+'</span>' +
        '<span class="tkey">'+keyHtml+'</span>' +
        '<span class="tsum">'+t.summary+tags+'</span>' +
        '<span class="tstatus">'+statusPillHtml(t.status, t.status_category)+'</span>' +
        '<span class="tworked" title="Your own Jira worklog entries on this ticket, dated this same work day">' + t.logged +
          '<span class="logged" title="Git-based estimate, not actual - shown for reference only">est: '+t.worked+'</span>' +
        '</span>' +
        '<svg class="tchev" viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>' +
      '</summary>' +
      '<div class="ticket-body">'+(actionsHtml || '<div class="empty">No recorded actions.</div>')+'</div>';
    list.appendChild(row);
  });

  // Use the same actual-logged total the stat card shows (not a re-sum of the donut's own
  // rounded per-category slices, and not the estimate) - otherwise per-ticket rounding and
  // per-category rounding can land on different whole minutes and the two totals visibly
  // disagree, or the donut ends up showing a different metric than the headline number.
  const total = data.stats.logged_mins;
  el("donutTotal").textContent = fmtMinsJs(total);
  const legend = el("donutLegend");
  legend.innerHTML = "";
  let gradient = [];
  let acc = 0;
  data.donut.slice(0, 8).forEach((d, i) => {
    const pct = total ? (d.mins/total*100) : 0;
    const color = DONUT_COLORS[i % DONUT_COLORS.length];
    gradient.push(color + " " + acc.toFixed(1) + "% " + (acc+pct).toFixed(1) + "%");
    acc += pct;
    const row = document.createElement("div");
    row.className = "row";
    row.innerHTML = '<span class="sw" style="background:'+color+'"></span>' +
      '<div class="body"><div class="key">'+d.key+'</div><div class="mins">'+fmtMinsJs(d.mins)+'</div></div>' +
      '<span class="pct">'+pct.toFixed(0)+'%</span>';
    legend.appendChild(row);
  });
  el("donutChart").style.background = gradient.length
    ? "conic-gradient(" + gradient.join(",") + ")"
    : "#e5e7eb";

  const act = el("activityList");
  act.innerHTML = "";
  if (!data.activity.length) {
    act.innerHTML = '<div class="empty">No activity recorded for this day.</div>';
  }
  data.activity.slice(0, 12).forEach(a => {
    const item = document.createElement("div");
    item.className = "item t-" + a.type;
    const mainText = a.mins > 0 ? "Logged " + fmtMinsJs(a.mins) + " on " : (KIND_LABEL[a.type]||a.type) + " on ";
    item.innerHTML = '<div class="time">'+a.time+'</div>' +
      '<div class="msg"><b>'+mainText+'<span class="tl">'+a.ticket+'</span></b>' +
      '<div class="sub">'+a.message+'</div></div>';
    act.appendChild(item);
  });
}

function fmtMinsJs(mins) {
  mins = Math.round(mins);
  const h = Math.floor(mins/60), m = mins%60;
  if (h && m) return h+"h "+m+"m";
  if (h) return h+"h";
  return m+"m";
}

function updateBreakStatus(continuousMins, thresholdMins) {
  const el2 = el("breakStatus");
  if (!el2 || !thresholdMins || thresholdMins <= 0 || continuousMins < 10) { if (el2) el2.hidden = true; return; }
  el2.hidden = false;
  const due = continuousMins >= thresholdMins;
  el2.className = "break-status" + (due ? " due" : "");
  el2.textContent = (due ? "⚠ Take a break · " : "⏱ Active ") + fmtMinsJs(continuousMins) + " straight";
}

// Polls break_reminder_loop's state every 60s rather than having that background thread push
// into the page directly - calling window.evaluate_js() from a non-UI thread was a real,
// reproducible cause of the whole app freezing. This direction (JS pulling from Python) is safe.
async function pollBreakStatus() {
  try {
    const status = await window.pywebview.api.get_break_status();
    updateBreakStatus(status.continuous_mins, status.reminder_mins);
  } catch (err) {
    // non-critical - just skip this tick
  }
}
// No immediate call here - the very first tick 60s from now is plenty (there's nothing to
// show this early anyway), and calling it immediately would race with loadInitial()'s own
// first-time bridge calls at page load, which is the same class of concurrency issue as the
// evaluate_js problem above.
setInterval(pollBreakStatus, 60000);

function openUrl(url) { window.pywebview.api.open_url(url); }
function openJira() {
  if (state && state.jira_base_url) openUrl(state.jira_base_url);
  else alert("Set JIRA_BASE_URL to enable this.");
}
function showWeek() {
  if (!state) return;
  alert("Last "+state.week.days+" day(s) scanned: "+state.week.mins+" total, "+state.week.tickets+" ticket touches.");
}

// Guards every top-level entry point that talks to the bridge (manual refresh, the two auto-
// refresh timers below, date pickers, team worklog load) so at most one is ever in flight at
// once. Two SEQUENTIAL awaits within one chain were already safe, but two INDEPENDENT chains
// (e.g. a 5-minute timer firing while the user clicks Refresh, or while the 15-minute team timer
// is mid-fetch) would invoke different bridge methods concurrently - the same real,
// reproducible cause of the whole app hanging/crashing documented elsewhere in this file.
// Only top-level entry points check this; helpers they call sequentially (syncCalendar,
// checkTeamRoster, loadTeamWorklogNow, ...) do not, so a locked entry point can still call them.
let bridgeBusy = false;

async function doRefresh() {
  if (bridgeBusy) return;
  bridgeBusy = true;
  const btn = el("refreshBtn");
  btn.classList.add("spinning");
  try {
    const data = await window.pywebview.api.refresh();
    render(data);
    // Sequential, not concurrent - two different bridge methods being invoked at once
    // (each for the first time in this process) is a real, reproducible cause of the whole
    // app hanging/crashing (a pythonnet/WebView2 bridge concurrency issue), not just a style
    // preference. Never fire more than one of these calls into the bridge at a time.
    await syncCalendar();
    await syncCommentedTickets();
    if (teamWorklogLoaded) await loadTeamWorklogNow(teamWorklogDateKey);
    else await checkTeamRoster();
  } catch (err) {
    alert("Couldn't refresh: " + err);
  }
  btn.classList.remove("spinning");
  bridgeBusy = false;
}

// Auto-refresh: My Work every 5 minutes, Team Worklog on its own, longer 15-minute cadence
// (it's real per-teammate Jira lookups, more expensive than the core refresh). Both silently
// skip a tick if a bridge chain is already running rather than queuing or alerting - it's a
// background timer, not a user action, and the next tick will pick up the same data soon after.
async function autoRefreshMyWork() {
  if (bridgeBusy) return;
  bridgeBusy = true;
  try {
    const data = await window.pywebview.api.refresh();
    render(data);
    await syncCalendar();
    await syncCommentedTickets();
  } catch (err) {
    // silent - loadInitial's own retry loop is what surfaces a persistent failure
  }
  bridgeBusy = false;
}
setInterval(autoRefreshMyWork, 5 * 60 * 1000);

async function autoRefreshTeamWork() {
  if (bridgeBusy || !teamWorklogLoaded) return;
  bridgeBusy = true;
  try {
    await loadTeamWorklogNow(teamWorklogDateKey);
  } catch (err) {
    // silent - loadTeamWorklogNow already leaves the card as-is on its own failures
  }
  bridgeBusy = false;
}
setInterval(autoRefreshTeamWork, 15 * 60 * 1000);

function renderCalendar(data) {
  const calEl = el("greetingCal");
  if (data.calendar_summary) {
    calEl.hidden = false;
    calEl.textContent = data.calendar_summary;
  } else {
    calEl.hidden = true;
  }

  const agendaCard = el("agendaCard");
  // Current/upcoming meetings first (closest to now on top), already-finished ones pushed
  // below - each group keeps its own chronological order.
  const events = (data.calendar_events || []).slice().sort((a, b) => {
    if (a.is_past !== b.is_past) return a.is_past ? 1 : -1;
    return 0;
  });
  if (events.length) {
    agendaCard.hidden = false;
    el("agendaCount").textContent = events.length + " event" + (events.length === 1 ? "" : "s");
    const list = el("agendaList");
    list.innerHTML = "";
    const dotColors = ["#2563eb", "#7c3aed", "#16a34a", "#ea580c", "#dc2626"];
    events.forEach((ev, i) => {
      const item = document.createElement("div");
      item.className = "agenda-item" + (ev.is_past ? " past" : "");
      item.style.setProperty("--dot-color", dotColors[i % dotColors.length]);
      const sub = [ev.duration, ev.source].filter(Boolean).join(" · ");
      item.innerHTML = '<div class="a-time">' + ev.time + '</div>' +
        '<div class="a-title">' + ev.title + '</div>' +
        (sub ? '<div class="a-sub">' + sub + '</div>' : '');
      list.appendChild(item);
    });
  } else {
    agendaCard.hidden = true;
  }
}

// Calendar is fetched separately AFTER the main dashboard is up (see Api.refresh_calendar) -
// a Google hiccup then only leaves the agenda card/pill blank, never breaks the whole page.
async function syncCalendar() {
  try {
    const calData = await window.pywebview.api.refresh_calendar();
    if (state) Object.assign(state, calData);
    renderCalendar(calData);
  } catch (err) {
    // silently leave calendar UI as-is (hidden) - this is enhancement data, not core
  }
}

// Merges in tickets you only left a Jira comment on (see Api.refresh_commented_tickets) -
// fetched separately, after the main dashboard already has its git-based data rendered, for the
// same reason calendar is: a real Jira comment scan shouldn't be able to slow down or fail the
// core load. Re-renders the whole page since this can add rows to the ticket list itself.
async function syncCommentedTickets() {
  try {
    const data = await window.pywebview.api.refresh_commented_tickets(state ? state.selected_date : null);
    render(data);
  } catch (err) {
    // silently leave the ticket list as-is - this is enhancement data, not core
  }
}

let teamWorklogDateKey = null;

// Traffic-light coding for how much of a full day is logged: red = barely started,
// orange = partway, green = a full (or fuller) day's worth.
function strengthClass(pct) {
  if (pct < 40) return "low";
  if (pct < 80) return "mid";
  return "high";
}

const teamOpenRows = new Set();
let teamWorklogLoaded = false;
let teamWorklogData = null;

function toggleTeamRow(accountId) {
  if (teamOpenRows.has(accountId)) teamOpenRows.delete(accountId); else teamOpenRows.add(accountId);
  if (teamWorklogData) renderTeamWorklogSummary(teamWorklogData);
}

function renderTeamWorklogSummary(data) {
  const card = el("teamWorklogCard");
  const members = data.members || [];
  teamWorklogData = data;
  teamWorklogDateKey = data.date;
  el("teamWorklogDateInput").value = data.date;
  if (!members.length) { card.hidden = true; return; }
  card.hidden = false;
  const target = data.daily_target_mins || 480;
  el("teamWorklogList").innerHTML = members.map(m => {
    const initials = (m.display_name || "?").split(/\s+/).filter(Boolean).slice(0, 2)
      .map(w => w[0].toUpperCase()).join("");
    const pct = Math.max(0, Math.min(100, Math.round(100 * m.total_minutes / target)));
    const open = teamOpenRows.has(m.account_id);
    const tickets = m.tickets || [];
    const detail = tickets.length
      ? tickets.map(t => '<div class="team-ticket-row">' +
          '<span class="team-ticket-key">' + t.key + '</span>' +
          statusPillHtml(t.status, t.status_category) +
          '<span class="team-ticket-summary">' + t.summary + '</span>' +
          '<span class="team-ticket-mins">' + fmtMinsJs(t.minutes) + '</span>' +
        '</div>').join("")
      : '<div class="team-ticket-row"><span class="team-ticket-summary">No logged time for this date.</span></div>';
    return '<div class="team-row' + (open ? ' open' : '') + '">' +
      '<div class="team-row-top" onclick="toggleTeamRow(\'' + m.account_id + '\')">' +
        '<svg class="team-chev" viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>' +
        '<div class="team-avatar">' + initials + '</div>' +
        '<div class="team-name">' + m.display_name + '</div>' +
        '<div class="team-mins">' + fmtMinsJs(m.total_minutes) + '</div>' +
      '</div>' +
      '<div class="team-strength" title="' + pct + '% of a full day logged">' +
        '<div class="team-strength-fill ' + strengthClass(pct) + '" style="width:' + pct + '%"></div>' +
      '</div>' +
      (open ? '<div class="team-tickets">' + detail + '</div>' : '') +
    '</div>';
  }).join("") + '<div class="team-open-link" onclick="window.pywebview.api.open_team_window()">Open full Team Worklogs window</div>';
}

// Cheap local-file check only (no Jira network calls) - reveals the card with a "Load" button
// instead of auto-fetching. The real per-account Jira lookups in get_team_worklogs() only run
// once the user explicitly asks for them (button click or changing the date), so they can never
// slow down or fail the core dashboard load.
async function checkTeamRoster() {
  try {
    const roster = await window.pywebview.api.get_team();
    const card = el("teamWorklogCard");
    if (!roster.length) { card.hidden = true; return; }
    card.hidden = false;
    if (!teamWorklogLoaded) {
      el("teamWorklogList").innerHTML = '<div class="team-load-prompt">' +
        '<div class="s">See what your team logged, ticket by ticket.</div>' +
        '<button class="team-load-btn" onclick="loadTeamWorklogGuarded()">Load team worklog</button>' +
      '</div>';
    }
  } catch (err) {
    // silently leave the card as-is (hidden until it first succeeds)
  }
}

// dateKey is independent of the main dashboard's own date selector - this card has its own
// date picker so you can check a team's day without changing your own view.
async function loadTeamWorklogNow(dateKey) {
  try {
    el("teamWorklogCard").hidden = false;
    el("teamWorklogList").innerHTML = '<div class="progress-bar"><div class="progress-fill"></div></div>';
    const data = await window.pywebview.api.get_team_worklogs(dateKey || null);
    teamWorklogLoaded = true;
    renderTeamWorklogSummary(data);
  } catch (err) {
    // silently leave the card as-is
  }
}
// Entry point for direct UI triggers (the "Load team worklog" button, the date input) - unlike
// loadTeamWorklogNow itself, this acquires the shared bridgeBusy lock, since those are top-level
// triggers that could otherwise overlap doRefresh() or the auto-refresh timers.
async function loadTeamWorklogGuarded(dateKey) {
  if (bridgeBusy) return;
  bridgeBusy = true;
  await loadTeamWorklogNow(dateKey);
  bridgeBusy = false;
}
el("teamWorklogDateInput").addEventListener("change", (e) => loadTeamWorklogGuarded(e.target.value));

let firstLoad = true;
let loadRetryDelay = 2000;

async function loadInitial() {
  if (bridgeBusy) { setTimeout(loadInitial, 500); return; }
  bridgeBusy = true;
  beginLoading();
  try {
    const data = await window.pywebview.api.refresh();
    render(data);
    loadRetryDelay = 2000;
    // Sequential, not concurrent - see the comment in doRefresh() for why.
    await syncCalendar();
    await syncCommentedTickets();
    await checkTeamRoster();
    if (firstLoad && !data.config.jira_connected && !data.config.calendar_connected) {
      openSettings();
    }
    firstLoad = false;
  } catch (err) {
    // Never show the error card for a load failure - keep the skeleton up and
    // quietly retry in the background (with backoff) until it succeeds. Still log it
    // (fire-and-forget) so a persistent failure leaves a real record in error.log instead
    // of just retrying forever with nothing to diagnose.
    console.error("loadInitial failed, retrying:", err);
    const detail = (err && (err.stack || err.message)) || String(err);
    window.pywebview.api.log_client_error("loadInitial: " + detail).catch(() => {});
    setTimeout(loadInitial, loadRetryDelay);
    loadRetryDelay = Math.min(loadRetryDelay * 1.5, 15000);
  } finally {
    bridgeBusy = false;
  }
}
el("dateSelect").addEventListener("change", async (e) => {
  if (bridgeBusy) return;
  bridgeBusy = true;
  try {
    const data = await window.pywebview.api.get_data(e.target.value);
    render(data);
  } finally {
    bridgeBusy = false;
  }
});
el("dateJumpPicker").addEventListener("change", async (e) => {
  if (!e.target.value || bridgeBusy) return;
  bridgeBusy = true;
  try {
    const data = await window.pywebview.api.get_data(e.target.value);
    render(data);
  } finally {
    bridgeBusy = false;
  }
});
el("refreshBtn").addEventListener("click", doRefresh);
el("settingsBtn").addEventListener("click", openSettings);

const TOKEN_MASK = "••••••••••••";
const DEFAULT_JIRA_URL = "https://your-domain.atlassian.net";

function openSettings() {
  if (!state) return;
  const c = state.config;
  el("cfgJiraUrl").value = c.jira_base_url || DEFAULT_JIRA_URL;
  el("cfgJiraEmail").value = c.jira_email || c.suggested_jira_email || "";
  el("cfgJiraToken").value = c.jira_has_token ? TOKEN_MASK : "";
  el("cfgRepoRoot").textContent = c.repos_root;
  el("cfgAuthor").textContent = c.author;
  el("cfgLookback").textContent = c.lookback_hours + "h";
  el("cfgTarget").textContent = c.daily_target_hours + "h";
  const msg = el("jiraStatusMsg");
  msg.textContent = c.jira_connected ? "Currently connected." : "Not connected yet.";
  msg.className = "settings-status" + (c.jira_connected ? " ok" : "");

  el("cfgGoogleClientId").value = c.google_client_id || "";
  el("cfgGoogleClientSecret").value = "";
  const calMsg = el("calendarStatusMsg");
  calMsg.textContent = c.calendar_connected ? "Currently connected." : "Not connected yet.";
  calMsg.className = "settings-status" + (c.calendar_connected ? " ok" : "");

  el("cfgBreakReminder").value = c.break_reminder_mins;
  el("breakReminderStatusMsg").textContent = "";
  el("breakReminderStatusMsg").className = "settings-status";

  el("settingsOverlay").hidden = false;
  setTimeout(() => el("cfgJiraToken").focus(), 0);
}

function closeSettings() { el("settingsOverlay").hidden = true; }

function openNeedsLogging() {
  if (!state) return;
  const body = el("needsLoggingBody");
  const items = state.needs_logging || [];
  body.innerHTML = items.length
    ? items.map(n => {
        const keyHtml = n.jira_url
          ? '<a onclick="event.preventDefault();openUrl(\''+n.jira_url+'\')">'+n.key+'</a>'
          : n.key;
        const tags = (n.merged ? '<span class="tag merged">merged</span>' : "") +
                     (n.reverted ? '<span class="tag reverted">reverted</span>' : "");
        return '<div class="needs-log-row">' +
          '<div class="nl-top">'+keyHtml+statusPillHtml(n.status, n.status_category)+
            '<span class="nl-kind">'+n.activity_kind+'</span>'+tags+
            '<span class="nl-worked" title="Worked on this ticket today (git-based estimate) - not yet logged to Jira">'+n.worked+'</span>' +
          '</div>' +
          '<div class="nl-sum">'+n.summary+'</div>' +
          '<div class="nl-meta">' +
            '<span>'+n.commit_count+' commit'+(n.commit_count===1?'':'s')+'</span>' +
            '<span>'+n.first_activity+'&ndash;'+n.last_activity+'</span>' +
            '<span>'+n.repos.join(', ')+'</span>' +
          '</div>' +
        '</div>';
      }).join("")
    : '<div class="empty">Nothing to log - everything touched today already has time logged.</div>';
  el("needsLoggingOverlay").hidden = false;
}
function closeNeedsLogging() { el("needsLoggingOverlay").hidden = true; }

el("cfgJiraToken").addEventListener("keydown", (e) => {
  if (e.key === "Enter") saveJiraSettings();
});

// Settings actions are manual and rare, but the two auto-refresh timers now fire in the
// background unconditionally (even with Settings open), so they need the same bridgeBusy guard
// as everything else - two different bridge methods in flight at once is the real crash cause.
async function saveJiraSettings() {
  if (bridgeBusy) { el("jiraStatusMsg").textContent = "Busy refreshing, try again in a moment."; return; }
  bridgeBusy = true;
  try {
    const url = el("cfgJiraUrl").value.trim();
    const email = el("cfgJiraEmail").value.trim();
    const rawToken = el("cfgJiraToken").value;
    const token = rawToken === TOKEN_MASK ? null : rawToken;
    const msg = el("jiraStatusMsg");
    msg.textContent = "Testing connection…";
    msg.className = "settings-status";
    const result = await window.pywebview.api.save_jira_settings(url, email, token);
    if (result.ok) {
      msg.textContent = "✓ Connected as " + result.display_name;
      msg.className = "settings-status ok";
      const data = await window.pywebview.api.get_data(state.selected_date);
      render(data);
      setTimeout(closeSettings, 1000);
    } else {
      msg.textContent = "✗ " + result.error;
      msg.className = "settings-status err";
    }
  } finally {
    bridgeBusy = false;
  }
}

async function saveBreakReminder() {
  if (bridgeBusy) { el("breakReminderStatusMsg").textContent = "Busy refreshing, try again in a moment."; return; }
  bridgeBusy = true;
  try {
    const msg = el("breakReminderStatusMsg");
    const result = await window.pywebview.api.set_break_reminder_mins(el("cfgBreakReminder").value);
    if (result.ok) {
      if (state) state.config.break_reminder_mins = result.reminder_mins;
      msg.textContent = result.reminder_mins > 0 ? "✓ Saved" : "✓ Saved - break reminders are off";
      msg.className = "settings-status ok";
    } else {
      msg.textContent = "✗ " + result.error;
      msg.className = "settings-status err";
    }
  } finally {
    bridgeBusy = false;
  }
}

async function clearSettings() {
  if (!confirm("Clear saved Jira and Google Calendar settings? You'll need to reconnect them.")) return;
  if (bridgeBusy) { alert("Busy refreshing, try again in a moment."); return; }
  bridgeBusy = true;
  try {
    const data = await window.pywebview.api.clear_settings();
    render(data);
    openSettings();
  } finally {
    bridgeBusy = false;
  }
}

async function connectGoogleCalendar() {
  if (bridgeBusy) { el("calendarStatusMsg").textContent = "Busy refreshing, try again in a moment."; return; }
  bridgeBusy = true;
  try {
    const clientId = el("cfgGoogleClientId").value.trim();
    const clientSecret = el("cfgGoogleClientSecret").value.trim();
    const msg = el("calendarStatusMsg");
    msg.textContent = "Opening your browser for Google sign-in…";
    msg.className = "settings-status";
    const result = await window.pywebview.api.connect_google_calendar(clientId, clientSecret);
    if (result.ok) {
      msg.textContent = "✓ Connected";
      msg.className = "settings-status ok";
      const data = await window.pywebview.api.get_data(state.selected_date);
      render(data);
    } else {
      msg.textContent = "✗ " + result.error;
      msg.className = "settings-status err";
    }
  } finally {
    bridgeBusy = false;
  }
}

window.addEventListener("pywebviewready", loadInitial);
</script>
</body>
</html>
"""


TEAM_HTML = r"""
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
  :root {
    --ink:#1c2430; --muted:#6b7280; --line:#e5e7eb; --page:#eef3fb; --card:#ffffff;
    --blue:#2563eb; --blue-wash:#dbeafe; --green:#16a34a; --green-wash:#dcfce7;
    --purple:#7c3aed; --purple-wash:#ede9fe; --orange:#ea580c; --orange-wash:#ffedd5;
    --red:#dc2626; --red-wash:#fee2e2; --shadow:0 1px 2px rgba(0,0,0,.04), 0 4px 14px -4px rgba(0,0,0,.08);
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--page); color:var(--ink); font-family:"Segoe UI",system-ui,sans-serif; font-size:13.5px; }
  .wrap { padding:16px 20px 24px; display:flex; flex-direction:column; gap:14px; }
  h1 { font-size:16px; margin:0; }
  .sub { font-size:11.5px; color:var(--muted); margin-top:2px; }
  .topbar { display:flex; align-items:center; justify-content:space-between; gap:10px; }
  .date-row { display:flex; align-items:center; gap:8px; }
  input[type=date], input[type=text] { font-family:inherit; font-size:12.5px; padding:6px 9px;
    border:1px solid var(--line); border-radius:8px; background:#fff; color:var(--ink); }
  .card { background:var(--card); border-radius:14px; box-shadow:var(--shadow); padding:14px 16px; }
  .card-head { display:flex; align-items:center; justify-content:space-between; margin-bottom:10px; }
  .card-head h2 { font-size:13px; margin:0; }
  .wk-btn { font-family:inherit; font-size:11.5px; font-weight:600; padding:7px 12px; border-radius:8px;
    border:1px solid var(--line); background:#fff; cursor:pointer; color:var(--ink); }
  .wk-btn.primary { background:var(--blue); color:#fff; border-color:var(--blue); }
  .wk-btn.ghost { background:var(--blue-wash); color:var(--blue); border-color:transparent; }
  .wk-btn.danger { background:var(--red-wash); color:var(--red); border-color:transparent; }
  .wk-btn:hover { filter:brightness(0.97); }
  .search-row { display:flex; gap:8px; }
  .search-row input { flex:1; }
  .search-results { display:flex; flex-direction:column; gap:6px; margin-top:10px; }
  .search-hit { display:flex; align-items:center; gap:10px; padding:7px 8px; border-radius:8px; background:var(--page); }
  .avatar-sm { width:26px; height:26px; border-radius:50%; background:var(--purple-wash); color:var(--purple);
    display:flex; align-items:center; justify-content:center; font-size:11px; font-weight:700; flex:none; }
  .hit-name { font-weight:600; font-size:12.5px; }
  .hit-email { font-size:11px; color:var(--muted); }
  .search-hit .meta { flex:1; min-width:0; }
  .empty { color:var(--muted); font-size:12px; padding:14px 4px; text-align:center; }
  .member-row { border:1px solid var(--line); border-radius:10px; margin-bottom:8px; overflow:hidden; }
  .member-head { display:flex; align-items:center; gap:10px; padding:10px 12px; cursor:pointer; }
  .member-head:hover { background:var(--page); }
  .member-total { font-weight:700; font-size:13px; color:var(--blue); }
  .member-remove { color:var(--red); background:none; border:none; cursor:pointer; font-size:15px; padding:2px 6px; }
  .member-strength { height:5px; border-radius:999px; background:var(--line); overflow:hidden; margin:0 12px 10px 48px; }
  .member-strength-fill { height:100%; border-radius:999px; transition:width 0.4s ease; }
  .member-strength-fill.low { background:var(--red); }
  .member-strength-fill.mid { background:var(--orange); }
  .member-strength-fill.high { background:var(--green); }
  .member-detail { display:none; padding:0 12px 12px 48px; }
  .member-row.open .member-detail { display:block; }
  .member-row.open .chev { transform:rotate(90deg); }
  .chev { transition:transform .15s; color:var(--muted); flex:none; }
  .ticket-row { display:flex; align-items:center; gap:8px; padding:5px 0; font-size:12px; border-top:1px solid var(--line); }
  .ticket-row:first-child { border-top:none; }
  .ticket-key { font-weight:600; color:var(--blue); flex:none; width:78px; }
  .ticket-summary { flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:var(--muted); }
  .ticket-mins { flex:none; font-weight:600; }
  .status-pill { font-size:9.5px; font-weight:700; padding:2px 7px; border-radius:999px; flex:none; }
  .status-pill.cat-new { background:#f1f5f9; color:#475569; }
  .status-pill.cat-indeterminate { background:var(--orange-wash); color:#9a3412; }
  .status-pill.cat-done { background:var(--green-wash); color:#166534; }
  .spin { animation:spin 0.8s linear infinite; }
  @keyframes spin { to { transform:rotate(360deg); } }
</style>
</head>
<body>
<div class="wrap">
  <div class="topbar">
    <div>
      <h1>Team Worklogs</h1>
      <div class="sub" id="dateLabel">Loading…</div>
    </div>
    <div class="date-row">
      <input type="date" id="dateInput">
      <button class="wk-btn" onclick="refreshWorklogs()">&#8635; Refresh</button>
    </div>
  </div>

  <div class="card">
    <div class="card-head"><h2>Add a teammate</h2></div>
    <div class="search-row">
      <input type="text" id="searchInput" placeholder="Search by name or email…">
      <button class="wk-btn primary" onclick="searchMembers()">Search</button>
    </div>
    <div class="search-results" id="searchResults"></div>
  </div>

  <div class="card">
    <div class="card-head"><h2>Your team</h2></div>
    <div id="teamList"><div class="empty">Loading…</div></div>
  </div>
</div>

<script>
function el(id) { return document.getElementById(id); }

function fmtMinsJs(mins) {
  mins = Math.round(mins);
  const h = Math.floor(mins/60), m = mins%60;
  if (h && m) return h+"h "+m+"m";
  if (h) return h+"h";
  return m+"m";
}

function initials(name) {
  return (name || "?").split(/\s+/).filter(Boolean).slice(0, 2).map(w => w[0].toUpperCase()).join("");
}

// Traffic-light coding for how much of a full day is logged: red = barely started,
// orange = partway, green = a full (or fuller) day's worth.
function strengthClass(pct) {
  if (pct < 40) return "low";
  if (pct < 80) return "mid";
  return "high";
}

function statusPillHtml(status, category) {
  if (!status) return "";
  const cls = category === "done" ? "cat-done" : category === "indeterminate" ? "cat-indeterminate" : "cat-new";
  return '<span class="status-pill ' + cls + '">' + status + '</span>';
}

let team = [];
let currentDate = null;
let dailyTargetMins = 480;
const openRows = new Set();

async function loadTeam() {
  await refreshWorklogs();
}

async function searchMembers() {
  const q = el("searchInput").value.trim();
  const box = el("searchResults");
  if (!q) { box.innerHTML = ""; return; }
  box.innerHTML = '<div class="empty">Searching…</div>';
  const hits = await window.pywebview.api.search_team_members(q);
  if (!hits.length) { box.innerHTML = '<div class="empty">No matching Jira users.</div>'; return; }
  box.innerHTML = hits.map(h => {
    const already = team.some(m => m.account_id === h.account_id);
    return '<div class="search-hit">' +
      '<div class="avatar-sm">' + initials(h.display_name) + '</div>' +
      '<div class="meta"><div class="hit-name">' + h.display_name + '</div>' +
        '<div class="hit-email">' + (h.email || "") + '</div></div>' +
      '<button class="wk-btn' + (already ? '' : ' primary') + '" ' +
        (already ? 'disabled' : 'onclick="addMember(\''+h.account_id+'\',\''+
          h.display_name.replace(/'/g,"\\'")+'\',\''+(h.email||"")+'\')"') + '>' +
        (already ? 'Added' : '+ Add') + '</button>' +
    '</div>';
  }).join("");
}

async function addMember(accountId, displayName, email) {
  team = await window.pywebview.api.add_team_member(accountId, displayName, email);
  el("searchInput").value = "";
  el("searchResults").innerHTML = "";
  await refreshWorklogs();
}

async function removeMember(accountId, ev) {
  ev.stopPropagation();
  if (!confirm("Remove this teammate from your team view?")) return;
  team = await window.pywebview.api.remove_team_member(accountId);
  openRows.delete(accountId);
  await refreshWorklogs();
}

function toggleRow(accountId) {
  if (openRows.has(accountId)) openRows.delete(accountId); else openRows.add(accountId);
  renderTeam();
}

function renderTeam() {
  const box = el("teamList");
  if (!team.length) {
    box.innerHTML = '<div class="empty">No teammates yet - search above to add one.</div>';
    return;
  }
  box.innerHTML = team.map(m => {
    const open = openRows.has(m.account_id);
    const tickets = m.tickets || [];
    const detail = tickets.length
      ? tickets.map(t => '<div class="ticket-row">' +
          '<span class="ticket-key">' + t.key + '</span>' +
          statusPillHtml(t.status, t.status_category) +
          '<span class="ticket-summary">' + t.summary + '</span>' +
          '<span class="ticket-mins">' + fmtMinsJs(t.minutes) + '</span>' +
        '</div>').join("")
      : '<div class="empty">No logged time for this date.</div>';
    const pct = Math.max(0, Math.min(100, Math.round(100 * m.total_minutes / dailyTargetMins)));
    return '<div class="member-row' + (open ? ' open' : '') + '">' +
      '<div class="member-head" onclick="toggleRow(\'' + m.account_id + '\')">' +
        '<svg class="chev" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>' +
        '<div class="avatar-sm">' + initials(m.display_name) + '</div>' +
        '<div class="meta"><div class="hit-name">' + m.display_name + '</div>' +
          '<div class="hit-email">' + (m.email || "") + '</div></div>' +
        '<div class="member-total">' + fmtMinsJs(m.total_minutes) + '</div>' +
        '<button class="member-remove" onclick="removeMember(\'' + m.account_id + '\', event)" title="Remove">&times;</button>' +
      '</div>' +
      '<div class="member-strength" title="' + pct + '% of a full day logged">' +
        '<div class="member-strength-fill ' + strengthClass(pct) + '" style="width:' + pct + '%"></div>' +
      '</div>' +
      '<div class="member-detail">' + detail + '</div>' +
    '</div>';
  }).join("");
}

async function refreshWorklogs() {
  const dateVal = el("dateInput").value || null;
  const data = await window.pywebview.api.get_team_worklogs(dateVal);
  currentDate = data.date;
  el("dateInput").value = data.date;
  el("dateLabel").textContent = data.date_label;
  team = data.members;
  dailyTargetMins = data.daily_target_mins || 480;
  renderTeam();
}

el("dateInput").addEventListener("change", refreshWorklogs);

window.addEventListener("pywebviewready", loadTeam);
</script>
</body>
</html>
"""


def main():
    api = Api()
    window = webview.create_window("MyWorkDay", html=HTML, js_api=api, width=1180, height=880, min_size=(760, 600))
    api.main_window = window
    threading.Thread(target=break_reminder_loop, args=(api,), daemon=True).start()
    # Tried private_mode=False + a persistent storage_path here to stop pywebview's default
    # (a brand new %TEMP% WebView2 profile every launch, 90+ orphaned folders / 1.5GB confirmed
    # live) - reverted after it made things WORSE: a real (non-private) profile's first-run setup
    # does full Chromium profile initialization, including real network calls to Microsoft's own
    # variations-seed/component-update services, which hung this exact app on the very first
    # launch with the new profile (caught live with py-spy - the main thread was legitimately
    # blocked, not a false positive). A private/ephemeral profile skips that heavier first-run
    # path, which is exactly why the plain default never showed this failure mode. The temp-
    # folder accumulation is real but cosmetic (disk space only); this hang was not.
    webview.start()


if __name__ == "__main__":
    main()
