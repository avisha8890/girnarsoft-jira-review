#!/usr/bin/env python3
"""
Build the tracking record for one jira-review run, ready for the Artifact tool's
write_db (collection "reviews"). Two modes:

  python3 record_run.py --mark-start KEY
      stamps .review/KEY-started.at with the current UTC time (call right after the
      "review started" comment is posted, or at the blocked comment)

  python3 record_run.py KEY --verdict PASS|FAIL|BLOCKED --outcome OUTCOME
                        [--posted pr,jira] [--blocked-missing "branch name,PR link"]
                        [--counts B,M,m,W,U] [--ticket-json FILE] [--out FILE]
      writes .review/KEY-run.json and prints "doc_id=<id> file=<path> url=<ledger> collection=<name>"

Outcome values: qa_handoff | rework | posted | report_only | blocked.
Verdict and counts default to what .review/KEY-review.md says ("## Verdict: X" and the
"**B blockers · M major · m minor · W warnings · U unverified**" line). Change size comes
from .review/meta.txt, stat.txt and files.txt written by collect_diff.sh. The ticket
itself (type, priority, reviewer, developer, PR) comes from fetch_review_tickets.py
unless --ticket-json is given. No network beyond that one Jira read.
"""

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL_VERSION = "1.0.0"


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
    ap.add_argument("--verdict", choices=["PASS", "FAIL", "BLOCKED"])
    ap.add_argument("--outcome", choices=["qa_handoff", "rework", "posted", "report_only", "blocked"])
    ap.add_argument("--posted", default="", help="comma list of pr,jira")
    ap.add_argument("--blocked-missing", default="", help='comma list, e.g. "branch name,PR link"')
    ap.add_argument("--counts", help="B,M,m,W,U overriding the report")
    ap.add_argument("--started", help="ISO time overriding .review/KEY-started.at")
    ap.add_argument("--finished", help="ISO time, default now")
    ap.add_argument("--ticket-json", help="queue JSON file instead of fetching")
    ap.add_argument("--out", help="output path, default .review/KEY-run.json")
    args = ap.parse_args()

    review_dir = repo_top() / ".review"
    review_dir.mkdir(exist_ok=True)

    if args.mark_start:
        stamp = review_dir / f"{args.mark_start}-started.at"
        stamp.write_text(iso(utcnow()) + "\n")
        print(f"started {args.mark_start} at {stamp.read_text().strip()}")
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
    change = {"files": 0, "added": 0, "removed": 0, "commits": 0, "modules": []} if blocked \
        else change_from_review(review_dir)
    linked = [p for p in (t.get("pull_requests") or []) if p["id"] in (t.get("ticket_pr_ids") or [])]
    pr = linked[0] if linked else None
    dev = t.get("developer") or {}
    posted = {x.strip() for x in args.posted.split(",") if x.strip()}

    record = {
        "key": t["key"], "jira_url": t.get("url"), "summary": t.get("summary"),
        "type": t.get("type"), "priority": t.get("priority"), "project": data.get("jira_project"),
        "product": data.get("product") or data.get("jira_project"),
        "repo": data.get("repo"), "git_host": data.get("git_host"),
        "status_at_start": t.get("status"),
        "reviewer": (data.get("reviewer") or {}).get("display_name"),
        "developer": dev.get("display_name"),
        "branch": (t.get("ticket_branches") or [None])[0], "base_branch": t.get("base_branch"),
        "pr_id": pr["id"] if pr else None, "pr_url": pr["url"] if pr else None,
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
    doc_id = f"{key}-{started.strftime('%Y%m%dT%H%M%SZ')}"
    out = Path(args.out) if args.out else review_dir / f"{key}-run.json"
    out.write_text(json.dumps(record, indent=2) + "\n")
    tracking = ledger_target()
    print(f"doc_id={doc_id} file={out} url={tracking.get('artifact_url', '')} collection={tracking.get('collection', 'reviews')}")


def ledger_target():
    """Where the record goes: tracking.artifact_url in the repo's jira-project.json wins,
    else the skill's own tracking.json (one ledger shared by every project that installs
    this plugin)."""
    cfg = {}
    pj = repo_top() / ".claude" / "jira-project.json"
    if pj.exists():
        try:
            cfg = (json.loads(pj.read_text()).get("tracking") or {})
        except json.JSONDecodeError:
            cfg = {}
    skill = HERE.parent / "tracking.json"
    base = json.loads(skill.read_text()) if skill.exists() else {}
    return {"artifact_url": cfg.get("artifact_url") or base.get("artifact_url", ""),
            "collection": cfg.get("collection") or base.get("collection", "reviews")}


if __name__ == "__main__":
    main()
