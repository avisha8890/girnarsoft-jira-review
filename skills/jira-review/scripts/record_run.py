#!/usr/bin/env python3
"""
Build the tracking record for one jira-review run and send it to the review ledger
service. Three modes:

  python3 record_run.py --mark-start KEY
      stamps .review/KEY-started.at with the current UTC time (call right after the
      "review started" comment is posted, or at the blocked comment)

  python3 record_run.py KEY --verdict PASS|FAIL|BLOCKED --outcome OUTCOME
                        [--posted pr,jira] [--blocked-missing "branch name,PR link"]
                        [--pr [REPO:]ID,...] [--counts B,M,m,W,U] [--ticket-json FILE] [--out FILE]
      writes .review/KEY-run.json, sends it with PUT <ledger>/api/v1/runs/<run_id>, and prints
      "run_id=<id> file=<path> ledger=<url> result=<created|replaced|queued|rejected> [reason=...]"

  python3 record_run.py --flush
      sends the runs waiting in .review/pending/ and exits

Before sending, runs waiting in .review/pending/ are sent first. A run the ledger cannot take
right now (unreachable, server error, missing token) is queued there and goes out with the
next run; a run the ledger refuses as invalid is not queued (exit status 1).

Ledger location: LEDGER_URL from the shell or jira.env (the same file the Jira credentials
come from: $JIRA_ENV_FILE, <repo>/.claude/jira.env, ~/.claude/jira.env), else
tracking.ledger_url in jira-project.json, else ledger_url in the skill's tracking.json.
LEDGER_TOKEN, when the ledger requires one, comes from the shell or jira.env.

Outcome values: qa_handoff | rework | posted | report_only | blocked.
Verdict and counts default to what .review/KEY-review.md says ("## Verdict: X" and the
"**B blockers · M major · m minor · W warnings · U unverified**" line). Change size comes
from the meta.txt, stat.txt and files.txt collect_diff.sh wrote into each reviewed PR's
folder, .review/KEY/<repo>-pr<id>/, summed over the PRs of the run (the .review/ root for
runs made before per-PR folders). The ticket
itself (type, priority, reviewer, developer, PR) comes from fetch_review_tickets.py
unless --ticket-json is given. No network beyond that Jira read and the ledger.
"""

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL_VERSION = "1.1.0"


def utcnow():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso(t):
    return t.isoformat().replace("+00:00", "Z")


def parse_iso(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def repo_top():
    try:
        return Path(subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                                   text=True, check=True).stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        return Path.cwd()


def ensure_review_ignored(top):
    """The review folder must never reach the repository. Append a .review/ rule to the
    repo's .gitignore when it has none (once, with a comment) and warn when an earlier
    commit already tracks review files -- untracking is a git change the reviewer makes."""
    gi = top / ".gitignore"
    text = gi.read_text() if gi.exists() else ""
    if not re.search(r"^/?\.review/?$", text, re.M):
        prefix = "" if not text or text.endswith("\n") else "\n"
        with gi.open("a", encoding="utf-8") as fh:
            fh.write(prefix + "# jira-review skill working files: diffs, plans, reports, run records, review worktree\n.review/\n")
        print("note: added .review/ to .gitignore -- commit that change.", file=sys.stderr)
    try:
        tracked = subprocess.run(["git", "-C", str(top), "ls-files", ".review"], capture_output=True,
                                 text=True, check=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        tracked = ""
    if tracked:
        print("warning: files under .review/ are tracked by git; run: git rm -r --cached .review  (keeps them on disk)",
              file=sys.stderr)


def read(path):
    return path.read_text(errors="replace") if path.exists() else ""


def ticket_record(key, ticket_json):
    if ticket_json:
        data = json.loads(Path(ticket_json).read_text())
    else:
        out = subprocess.run([sys.executable, str(HERE / "fetch_review_tickets.py"), "--ticket", key,
                              "--json"], capture_output=True, text=True)
        if out.returncode != 0:
            sys.exit(f"error: could not fetch {key}: {out.stderr.strip() or out.stdout.strip()}")
        data = json.loads(out.stdout)
    tickets = data.get("tickets") or []
    if not tickets:
        sys.exit(f"error: {key} not in queue output")
    return data, tickets[0]


def change_from_review(review_dir):
    """files / +added / -removed / commits / modules from collect_diff.sh output."""
    stat = read(review_dir / "stat.txt")
    meta = read(review_dir / "meta.txt")
    files = [l.split("\t", 1)[1].strip() for l in read(review_dir / "files.txt").splitlines() if "\t" in l]
    m = re.search(r"(\d+) files? changed(?:, (\d+) insertions?\(\+\))?(?:, (\d+) deletions?\(-\))?", stat)
    n_files = int(m.group(1)) if m else len(files)
    added = int(m.group(2) or 0) if m else 0
    removed = int(m.group(3) or 0) if m else 0
    commits_block = meta.split("== commits on this branch ==")[-1].split("== authors ==")[0] if meta else ""
    commits = len([l for l in commits_block.splitlines() if re.match(r"^[0-9a-f]{7,}\s", l.strip())])
    modules = sorted({f.split("/")[0] for f in files if "/" in f})
    return {"files": n_files, "added": added, "removed": removed, "commits": commits, "modules": modules}


def pr_folder(review_dir, key, unit):
    folder = review_dir / key / unit
    return folder if (folder / "stat.txt").exists() else None


def change_for_run(review_dir, key, units):
    """Diff size for the run: the per-PR folders summed. Modules are prefixed with the PR
    unit when there are several, so two PRs' "src" never merge into one; with no per-PR
    folder at all, the .review/ root (runs from before per-PR folders)."""
    folders = [(u, pr_folder(review_dir, key, u)) for u in dict.fromkeys(units)]
    folders = [(u, f) for u, f in folders if f]
    if not folders:
        return change_from_review(review_dir)
    total = {"files": 0, "added": 0, "removed": 0, "commits": 0, "modules": set()}
    for unit, f in folders:
        c = change_from_review(f)
        for k in ("files", "added", "removed", "commits"):
            total[k] += c[k]
        total["modules"].update(f"{unit}/{m}" if len(folders) > 1 else m for m in c["modules"])
    total["modules"] = sorted(total["modules"])
    return total


def parse_pr_args(text):
    """--pr "girnarsoft-one-lms:1210,lms-pwa-ui:45", "lms-pwa-ui-pr45" or "1210"
    -> [(repo or None, id)]."""
    out = []
    for part in (x.strip() for x in (text or "").split(",")):
        if not part:
            continue
        repo, sep, pid = part.rpartition(":")
        if not sep and "-pr" in part:
            repo, _, pid = part.rpartition("-pr")
        if not pid.isdigit():
            sys.exit(f"error: --pr value '{part}' is not [REPO:]ID or <repo>-pr<id>")
        out.append((repo or None, int(pid)))
    return out


def reviewed_prs(t, chosen, review_dir, key):
    """The PRs this run covers. The chosen ones when --pr is given, else every reviewable
    (open or merged) PR the ticket links. Matched by repository AND number, since a number
    is only unique within its repository."""
    units = t.get("ticket_prs")
    if units is None:  # queue JSON from before PR units
        host = t.get("pull_requests") or []
        linked = [p for p in host if p["id"] in (t.get("ticket_pr_ids") or [])]
        if chosen:
            by_id = {p["id"]: p for p in linked}
            linked = [by_id[i] for _, i in chosen if i in by_id]
        return [dict(p, unit=None, repo=None, slug=None, change=None, developer=None) for p in linked]
    wanted = chosen or [(u["repo"], u["id"]) for u in units if u.get("reviewable")]
    out = []
    for repo, pid in wanted:
        match = [u for u in units if u["id"] == pid and (repo is None or u["repo"] == repo)]
        if not match:
            print(f"warning: --pr {repo + ':' if repo else ''}{pid} is not linked on the ticket; left out of the record",
                  file=sys.stderr)
            continue
        if len(match) > 1:
            sys.exit(f"error: PR #{pid} is linked from several repositories ({', '.join(u['repo'] for u in match)}) "
                     f"-- pass --pr <repo>:{pid}")
        u = match[0]
        folder = pr_folder(review_dir, key, u["unit"])
        out.append({"unit": u["unit"], "repo": u["repo"], "slug": u["slug"], "id": pid, "url": u.get("url"),
                    "title": u.get("title"), "state": u.get("state"), "source": u.get("source"),
                    "destination": u.get("destination"), "merge_commit": u.get("merge_commit"),
                    "developer": (u.get("linked_by") or {}).get("display_name"),
                    "change": change_from_review(folder) if folder else None})
    return out


def complexity(change, issue_type):
    """Deterministic size-based score so runs are comparable across reviewers.
    lines: <100 =1, <400 =2, <1000 =3, else 4 · files: >=10 +2, >=3 +1 · modules >1 +1
    · Bug/Prod Bug +1 (a fix needs the surrounding code understood, not just the diff)."""
    lines = change["added"] + change["removed"]
    score = 1 if lines < 100 else 2 if lines < 400 else 3 if lines < 1000 else 4
    score += 2 if change["files"] >= 10 else 1 if change["files"] >= 3 else 0
    score += 1 if len(change["modules"]) > 1 else 0
    score += 1 if "bug" in (issue_type or "").lower() else 0
    label = "Low" if score <= 2 else "Medium" if score <= 4 else "High"
    return {"score": score, "label": label,
            "basis": f"{lines} lines, {change['files']} files, {len(change['modules'])} module(s), {issue_type}"}


def verdict_and_counts(report_text):
    v = re.search(r"^## Verdict:\s*(PASS|FAIL)", report_text, re.M)
    c = re.search(r"\*\*(\d+) blockers? · (\d+) major · (\d+) minor · (\d+) warnings? · (\d+) unverified\*\*",
                  report_text)
    counts = {"blocker": 0, "major": 0, "minor": 0, "warn": 0, "unverified": 0}
    if c:
        counts = dict(zip(counts.keys(), map(int, c.groups())))
    return (v.group(1) if v else None), counts


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("key", nargs="?")
    ap.add_argument("--mark-start", metavar="KEY", help="stamp the start time for KEY and exit")
    ap.add_argument("--flush", action="store_true", help="send the runs waiting in .review/pending/ and exit")
    ap.add_argument("--verdict", choices=["PASS", "FAIL", "BLOCKED"])
    ap.add_argument("--outcome", choices=["qa_handoff", "rework", "posted", "report_only", "blocked"])
    ap.add_argument("--posted", default="", help="comma list of pr,jira")
    ap.add_argument("--blocked-missing", default="", help='comma list, e.g. "branch name,PR link"')
    ap.add_argument("--pr", default="", help="PR(s) reviewed in this run, comma separated, as REPO:ID, "
                    "<repo>-pr<id> or a bare ID when unambiguous -- e.g. girnarsoft-one-lms:1210,lms-pwa-ui:45. "
                    "Required whenever the reviewer left some reviewable PRs of the ticket out of the run. "
                    "Sizes are summed over the .review/KEY/<repo>-pr<id>/ folders")
    ap.add_argument("--counts", help="B,M,m,W,U overriding the report")
    ap.add_argument("--started", help="ISO time overriding .review/KEY-started.at")
    ap.add_argument("--finished", help="ISO time, default now")
    ap.add_argument("--ticket-json", help="queue JSON file instead of fetching")
    ap.add_argument("--out", help="output path, default .review/KEY-run.json")
    args = ap.parse_args()

    review_dir = repo_top() / ".review"
    review_dir.mkdir(exist_ok=True)
    ensure_review_ignored(repo_top())

    if args.mark_start:
        stamp = review_dir / f"{args.mark_start}-started.at"
        stamp.write_text(iso(utcnow()) + "\n")
        print(f"started {args.mark_start} at {stamp.read_text().strip()}")
        return

    ledger = ledger_target()
    if args.flush:
        sent, left = flush_pending(review_dir / "pending", ledger)
        print(f"ledger={ledger['url'] or '(not configured)'} pending_sent={sent} pending_left={left}")
        return

    key = args.key or sys.exit("error: KEY required (or --mark-start KEY)")
    if not args.verdict or not args.outcome:
        sys.exit("error: --verdict and --outcome are required")

    data, t = ticket_record(key, args.ticket_json)
    started_txt = args.started or read(review_dir / f"{key}-started.at").strip()
    finished = parse_iso(args.finished) if args.finished else utcnow()
    started = parse_iso(started_txt) if started_txt else finished
    report_verdict, counts = verdict_and_counts(read(review_dir / f"{key}-review.md"))
    if args.counts:
        counts = dict(zip(counts.keys(), map(int, args.counts.split(","))))
    if args.verdict != "BLOCKED" and report_verdict and report_verdict != args.verdict:
        print(f"warning: --verdict {args.verdict} but the report says {report_verdict}", file=sys.stderr)

    blocked = args.verdict == "BLOCKED"
    chosen = parse_pr_args(args.pr)
    prs = reviewed_prs(t, chosen, review_dir, key)
    change = {"files": 0, "added": 0, "removed": 0, "commits": 0, "modules": []} if blocked \
        else change_for_run(review_dir, key, [p["unit"] for p in prs if p.get("unit")])
    pr = prs[0] if prs else None
    dev = t.get("developer") or {}
    posted = {x.strip() for x in args.posted.split(",") if x.strip()}

    record = {
        "key": t["key"], "jira_url": t.get("url"), "summary": t.get("summary"),
        "type": t.get("type"), "priority": t.get("priority"), "project": data.get("jira_project"),
        "product": data.get("product") or data.get("jira_project"),
        "repo": data.get("repo"), "git_host": data.get("git_host"),
        "repos": sorted({p["repo"] for p in prs if p.get("repo")}),
        "status_at_start": t.get("status"),
        "reviewer": (data.get("reviewer") or {}).get("display_name")
        or os.environ.get("USER") or os.environ.get("USERNAME") or "unknown",
        "developer": dev.get("display_name"),
        "branch": pr["source"] if pr and pr.get("source") else (t.get("ticket_branches") or [None])[0],
        "base_branch": t.get("base_branch"),
        "pr_id": pr["id"] if pr else None, "pr_url": pr["url"] if pr else None,
        "pr_ids": [p["id"] for p in prs], "pr_urls": [p["url"] for p in prs],
        "prs": prs,
        "started_at": iso(started), "finished_at": iso(finished),
        "duration_sec": max(0, int((finished - started).total_seconds())),
        "verdict": args.verdict,
        "counts": counts,
        "change": change,
        "complexity": None if blocked else complexity(change, t.get("type")),
        "outcome": args.outcome,
        "blocked_missing": [x.strip() for x in args.blocked_missing.split(",") if x.strip()]
        if blocked else (t.get("missing_on_ticket") or []),
        "posted": {"pr": "pr" in posted, "jira": "jira" in posted},
        "recorded_by": os.environ.get("USER") or os.environ.get("USERNAME") or "",
        "recorded_at": iso(utcnow()),
        "skill_version": SKILL_VERSION,
    }
    run_id = f"{key}-{started.strftime('%Y%m%dT%H%M%SZ')}"
    out = Path(args.out) if args.out else review_dir / f"{key}-run.json"
    out.write_text(json.dumps(record, indent=2) + "\n")

    pending = review_dir / "pending"
    sent, left = flush_pending(pending, ledger)
    result, reason = deliver(ledger, run_id, record, pending)
    line = f"run_id={run_id} file={out} ledger={ledger['url'] or '(not configured)'} result={result}"
    print(line + (f" reason={reason}" if reason else ""))
    if sent or left or result == "queued":
        print(f"pending_sent={sent} pending_left={left + (1 if result == 'queued' else 0)}")
    if result == "rejected":
        sys.exit(1)


def read_env_file_values(names):
    """LEDGER_* values from jira.env, found the way fetch_review_tickets.py finds it.
    Variables already set in the shell win."""
    values = {n: os.environ.get(n, "").strip() for n in names}
    candidates = [os.environ.get("JIRA_ENV_FILE", "").strip(), str(repo_top() / ".claude" / "jira.env"),
                  str(Path.home() / ".claude" / "jira.env")]
    path = next((c for c in candidates if c and os.path.isfile(c)), "")
    if not path:
        return values
    for raw in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if line.startswith("export "):
            line = line[len("export "):]
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = (part.strip() for part in line.split("=", 1))
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if name in values and not values[name]:
            values[name] = value
    return values


def ledger_target():
    """Where the record goes: LEDGER_URL (shell or jira.env) wins, then tracking.ledger_url in
    jira-project.json (the repo's .claude/ copy, else the user's ~/.claude/ copy), then the
    skill's own tracking.json (one ledger shared by every project that installs this plugin)."""
    env = read_env_file_values(["LEDGER_URL", "LEDGER_TOKEN"])
    cfg = {}
    pj = repo_top() / ".claude" / "jira-project.json"
    if not pj.exists():
        pj = Path.home() / ".claude" / "jira-project.json"
    if pj.exists():
        try:
            cfg = (json.loads(pj.read_text()).get("tracking") or {})
        except json.JSONDecodeError:
            cfg = {}
    skill = HERE.parent / "tracking.json"
    base = json.loads(skill.read_text()) if skill.exists() else {}
    url = env["LEDGER_URL"] or cfg.get("ledger_url") or base.get("ledger_url", "")
    return {"url": url.rstrip("/"), "token": env["LEDGER_TOKEN"]}


def put_run(ledger, run_id, record):
    """PUT one run. Returns (HTTP status or None when unreachable, short reason)."""
    request = urllib.request.Request(
        f"{ledger['url']}/api/v1/runs/{run_id}", data=json.dumps(record).encode("utf-8"), method="PUT",
        headers={"Content-Type": "application/json", "Accept": "application/json",
                 **({"Authorization": f"Bearer {ledger['token']}"} if ledger["token"] else {})})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, ""
    except urllib.error.HTTPError as error:
        try:
            reply = json.loads(error.read().decode("utf-8") or "{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            reply = {}
        details = "; ".join(reply.get("details") or [])
        return error.code, f"HTTP {error.code} {reply.get('error', '')}{': ' + details if details else ''}".strip()
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return None, f"unreachable ({getattr(error, 'reason', error)})"


def deliver(ledger, run_id, record, pending):
    """Send one run; queue it in pending/ when the ledger cannot take it now.
    Returns (created|replaced|queued|rejected, reason)."""
    if not ledger["url"]:
        status, reason = None, "no ledger URL configured (LEDGER_URL in jira.env)"
    else:
        status, reason = put_run(ledger, run_id, record)
    if status == 201:
        (pending / f"{run_id}.json").unlink(missing_ok=True)
        return "created", ""
    if status == 200:
        (pending / f"{run_id}.json").unlink(missing_ok=True)
        return "replaced", ""
    if status is not None and 400 <= status < 500 and status not in (401, 403, 404, 408, 429):
        # The ledger read the record and refused it; sending it again cannot succeed.
        return "rejected", reason
    if status == 401:
        reason += " -- the ledger requires LEDGER_TOKEN in jira.env"
    pending.mkdir(parents=True, exist_ok=True)
    (pending / f"{run_id}.json").write_text(json.dumps(record, indent=2) + "\n")
    return "queued", reason


def flush_pending(pending, ledger):
    """Send every queued run, oldest first. Stops at the first run the ledger cannot take
    now. Returns (sent, still waiting)."""
    files = sorted(pending.glob("*.json")) if pending.is_dir() else []
    if not files or not ledger["url"]:
        return 0, len(files)
    sent = 0
    for path in files:
        try:
            record = json.loads(path.read_text())
        except json.JSONDecodeError:
            print(f"warning: {path} is not JSON; left in place", file=sys.stderr)
            continue
        result, reason = deliver(ledger, path.stem, record, pending)
        if result in ("created", "replaced"):
            sent += 1
        elif result == "rejected":
            path.rename(path.with_suffix(".rejected"))
            print(f"warning: ledger refused queued run {path.stem}: {reason}; kept as {path.with_suffix('.rejected')}",
                  file=sys.stderr)
        else:
            print(f"note: ledger still unavailable ({reason}); {path.stem} and later runs stay queued",
                  file=sys.stderr)
            break
    return sent, len(list(pending.glob("*.json")))


if __name__ == "__main__":
    main()
