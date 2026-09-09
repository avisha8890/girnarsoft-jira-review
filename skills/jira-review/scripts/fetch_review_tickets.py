#!/usr/bin/env python3
"""
Fetch the Jira tickets sitting in the code-review status for the user running this
command, and resolve the pull request(s) attached to each on the repository's git host
(Bitbucket Cloud or GitHub).

Credentials come from a jira.env file. Resolution order:

  1. $JIRA_ENV_FILE                       explicit override
  2. <git toplevel>/.claude/jira.env      per-repo file
  3. ~/.claude/jira.env                   shared file

Variables already exported in the shell win over the file. Keys read:

  JIRA_BASE_URL         Jira URL (or jira.base_url in jira-project.json)
  JIRA_USER + JIRA_PASS basic auth (Jira Server / Data Center)
  JIRA_TOKEN            bearer PAT -- wins over user/pass when set
  JIRA_EMAIL + JIRA_API_TOKEN             basic auth (Jira Cloud)
  BITBUCKET_EMAIL + BITBUCKET_API_TOKEN   Bitbucket Cloud basic auth
  BITBUCKET_ACCESS_TOKEN                  Bitbucket bearer -- wins when set
  GITHUB_TOKEN (or GH_TOKEN)              GitHub token
  JIRA_REVIEW_JQL       override the default queue query (optional)

Everything project-specific comes from <git toplevel>/.claude/jira-project.json:
  jira.project          project key the queue is scoped to           (required)
  jira.review_status    the status that means "waiting for review"   (required)
  jira.base_url         Jira URL when not in the environment
  jira.deployment       "server" | "cloud" (inferred from the URL when absent)
  git.base_branch       branch PRs target                             (required)
  git.host              "bitbucket" | "github" (inferred from the remote when absent)
  git.remote            remote name, default "origin"
  git.api_base          API base for a self-hosted instance (optional)
  git.protected_branches  names never taken as a feature branch (base_branch always is)

Usage:
  python3 fetch_review_tickets.py
  python3 fetch_review_tickets.py --json
  python3 fetch_review_tickets.py --ticket PROJ-1234
  python3 fetch_review_tickets.py --jql "project = PROJ AND status = 'In Review'"
"""

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

FIELDS = "summary,description,status,assignee,reporter,priority,issuetype,labels,updated,created,comment"
COMMENT_MAX_CHARS = 3000
TIMEOUT = 30
HOST_API_DEFAULTS = {"bitbucket": "https://api.bitbucket.org/2.0", "github": "https://api.github.com"}


# ---------------------------------------------------------------------------
# Environment and project config
# ---------------------------------------------------------------------------

def git_toplevel():
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True, timeout=10,
        )
        return out.stdout.strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def find_env_file():
    explicit = os.environ.get("JIRA_ENV_FILE", "").strip()
    if explicit:
        return explicit
    top = git_toplevel()
    candidates = []
    if top:
        candidates.append(os.path.join(top, ".claude", "jira.env"))
    candidates.append(os.path.expanduser("~/.claude/jira.env"))
    for path in candidates:
        if os.path.isfile(path):
            return path
    return ""


def load_env_file(path):
    """Parse KEY=VALUE lines. Shell variables already set are not overridden."""
    if not path or not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            if key and not os.environ.get(key):
                os.environ[key] = value


def load_project_config():
    top = git_toplevel()
    if not top:
        return {}
    path = os.path.join(top, ".claude", "jira-project.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


ENV_FILE = find_env_file()
load_env_file(ENV_FILE)
PROJECT = load_project_config()
JIRA_CFG = PROJECT.get("jira") or {}
GIT_CFG = PROJECT.get("git") or {}
JIRA_PROJECT = JIRA_CFG.get("project", "")
REVIEW_STATUS = JIRA_CFG.get("review_status", "")
BASE_BRANCH = GIT_CFG.get("base_branch", "")
PRODUCT = PROJECT.get("product", "")
GIT_REMOTE = GIT_CFG.get("remote") or "origin"
PROTECTED = {b.lower() for b in (GIT_CFG.get("protected_branches") or [])} | ({BASE_BRANCH.lower()} if BASE_BRANCH else set())
if JIRA_CFG.get("base_url") and not os.environ.get("JIRA_BASE_URL"):
    os.environ["JIRA_BASE_URL"] = JIRA_CFG["base_url"]


def jira_deployment():
    d = (JIRA_CFG.get("deployment") or "").lower()
    if d in ("server", "cloud"):
        return d
    return "cloud" if ".atlassian.net" in os.environ.get("JIRA_BASE_URL", "") else "server"


def user_ident(user):
    """The identifier assignment needs: `name` on Server/DC, `accountId` on Cloud."""
    user = user or {}
    return user.get("accountId") if jira_deployment() == "cloud" else user.get("name")


def env(name, required=True):
    value = os.environ.get(name, "").strip()
    if required and not value:
        where = ENV_FILE or "~/.claude/jira.env (not found)"
        sys.exit(
            f"error: {name} is not set.\n"
            f"Add it to {where} or export it in your shell, then run again.\n"
            f"See the header of this script for the full list of keys."
        )
    return value


def basic(user, secret):
    raw = f"{user}:{secret}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def jira_auth_header():
    token = os.environ.get("JIRA_TOKEN", "").strip()
    if token:
        return f"Bearer {token}"
    user = os.environ.get("JIRA_USER", "").strip()
    password = os.environ.get("JIRA_PASS", "").strip()
    if user and password:
        return basic(user, password)
    if user:
        sys.exit(f"error: JIRA_USER is set but JIRA_PASS is empty in {ENV_FILE or 'the environment'}")
    # Jira Cloud style fallback, kept so the script still works elsewhere.
    email = os.environ.get("JIRA_EMAIL", "").strip()
    api_token = os.environ.get("JIRA_API_TOKEN", "").strip()
    if email and api_token:
        return basic(email, api_token)
    sys.exit(
        "error: no Jira credentials. Set JIRA_USER and JIRA_PASS (or JIRA_TOKEN) in "
        f"{ENV_FILE or '~/.claude/jira.env'}"
    )


def remote_url():
    try:
        out = subprocess.run(["git", "remote", "get-url", GIT_REMOTE], capture_output=True, text=True,
                             check=True, timeout=10)
        return out.stdout.strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def git_host():
    h = (GIT_CFG.get("host") or "").lower()
    if h:
        return h
    url = remote_url()
    if "bitbucket.org" in url:
        return "bitbucket"
    if "github.com" in url:
        return "github"
    return ""


def host_api_base():
    return (GIT_CFG.get("api_base") or HOST_API_DEFAULTS.get(git_host(), "")).rstrip("/")


def host_auth_header():
    host = git_host()
    if host == "bitbucket":
        token = os.environ.get("BITBUCKET_ACCESS_TOKEN", "").strip()
        if token:
            return f"Bearer {token}"
        email = os.environ.get("BITBUCKET_EMAIL", "").strip()
        api_token = os.environ.get("BITBUCKET_API_TOKEN", "").strip()
        return basic(email, api_token) if email and api_token else ""
    if host == "github":
        token = (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()
        return f"Bearer {token}" if token else ""
    return ""


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def http_get(url, auth, params=None, what="the server"):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": auth,
            "Accept": "application/json",
            "User-Agent": "claude-code-jira-review-skill",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:500]
        if exc.code in (401, 403):
            sys.exit(
                f"error: {what} rejected the credentials (HTTP {exc.code}). "
                f"Check {ENV_FILE or 'your environment'}.\n{body}"
            )
        if exc.code == 400:
            sys.exit(f"error: {what} rejected the request (HTTP 400). Check the JQL / query.\n{body}")
        sys.exit(f"error: {what} returned HTTP {exc.code}.\n{body}")
    except urllib.error.URLError as exc:
        sys.exit(f"error: could not reach {what} at {url.split('?')[0]} -- {exc.reason}")


def jira_get(path, params=None):
    base = env("JIRA_BASE_URL").rstrip("/")
    return http_get(base + path, jira_auth_header(), params, what="Jira")


# ---------------------------------------------------------------------------
# Jira
# ---------------------------------------------------------------------------

def flatten_description(node):
    """Server returns wiki markup as a string; Cloud returns ADF. Handle both."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(flatten_description(n) for n in node)
    if not isinstance(node, dict):
        return ""
    if node.get("type") == "text":
        return node.get("text", "")
    if node.get("type") == "hardBreak":
        return "\n"
    inner = flatten_description(node.get("content"))
    if node.get("type") in ("paragraph", "heading", "listItem", "codeBlock"):
        return inner + "\n"
    return inner


def default_jql():
    override = os.environ.get("JIRA_REVIEW_JQL", "").strip()
    if override:
        return override
    if not REVIEW_STATUS:
        sys.exit("error: jira.review_status is not set in .claude/jira-project.json "
                 "(the status that means 'waiting for code review'), and no JIRA_REVIEW_JQL override is set")
    scope = f"project = {JIRA_PROJECT} AND " if JIRA_PROJECT else ""
    return (
        f'{scope}status = "{REVIEW_STATUS}" '
        "AND assignee = currentUser() "
        "AND resolution = Unresolved "
        "ORDER BY priority DESC, updated ASC"
    )


# ---------------------------------------------------------------------------
# Branch and PR references written on the ticket
# ---------------------------------------------------------------------------

# "Branch: X", "PWA branch: X", "API branch: X", "Branch name - X" ... the team writes
# the branch on the ticket when development starts; that line is the source of truth.
# Jira wiki markup is tolerated: a leading bullet (* - #), and *bold*, _italic_, {{monospace}}
# or quotes around the name.
BRANCH_LINE = re.compile(
    r"(?im)^\s*(?:[*#\-]+\s*)?(?:[A-Za-z]+\s+)?branch(?:\s*name)?\s*[:\-]\s*(?:\{\{|[`'\"*_])*([A-Za-z0-9._/\-]+)"
)
# Bitbucket ".../pull-requests/<id>", GitHub ".../pull/<id>", GitLab ".../merge_requests/<id>"
PR_URL = re.compile(r"https?://[^\s/]+/[^\s]*?/(?:pull-requests|pull|merge_requests)/(\d+)")


def references_in(text):
    branches, prs = [], []
    for m in BRANCH_LINE.finditer(text or ""):
        name = m.group(1).rstrip(".,;)*_")
        if name.lower() not in PROTECTED and name not in branches:
            branches.append(name)
    for m in PR_URL.finditer(text or ""):
        pid = int(m.group(1))
        if pid not in prs:
            prs.append(pid)
    return branches, prs


# Comments this skill posts itself quote the branch and PR back to the ticket. They must
# never count as the developer's word, or the reviewer's own "started" comment would win.
SKILL_COMMENT_PREFIXES = ("Code review started", "Code review cannot start", "Code review \u2014",
                          "h2. Code review", "Code review -")


def is_skill_comment(comment):
    body = (comment.get("body") or "").lstrip()
    return body.startswith(SKILL_COMMENT_PREFIXES)


def developer_comments(rec):
    """Comments written by people, oldest first -- the skill's own comments removed."""
    return [c for c in (rec.get("comments") or []) if not is_skill_comment(c)]


def current_references(rec):
    """The branch(es) and PR(s) the ticket currently points at.

    Developers re-post "Branch: ..." / "PR: ..." when they re-cut a branch or raise a new
    PR, so the LATEST comment carrying a branch reference wins for branches and the latest
    carrying a PR reference wins for PRs (they may be different comments). The description
    is the fallback when no comment has one. Everything older is returned as `superseded`
    so the reviewer can see what was replaced -- it is never used as the source.
    Returns (branches, pr_ids, superseded)."""
    branches, prs = [], []
    superseded = []
    for c in reversed(developer_comments(rec)):
        b, p = references_in(c.get("body"))
        if not (b or p):
            continue
        take_b = b and not branches
        take_p = p and not prs
        if take_b:
            branches = b
        if take_p:
            prs = p
        if (b and not take_b) or (p and not take_p):
            superseded.append({"author": c.get("author"), "created": (c.get("created") or "")[:19],
                               "branches": b if not take_b else [], "pr_ids": p if not take_p else []})
    db, dp = references_in(rec.get("description"))
    if not branches:
        branches = db
    elif db:
        superseded.append({"author": "description", "created": "", "branches": db, "pr_ids": []})
    if not prs:
        prs = dp
    elif dp:
        superseded.append({"author": "description", "created": "", "branches": [], "pr_ids": dp})
    return branches, prs, superseded


def developer_of(rec):
    """Who to hand a failed review back to: the author of the most recent comment that put
    a branch or PR reference on the ticket. Falls back to the assignee when the reference
    lives in the description (or nowhere)."""
    for c in reversed(developer_comments(rec)):
        branches, prs = references_in(c.get("body"))
        if (branches or prs) and c.get("author_name"):
            return {"name": c["author_name"], "display_name": c.get("author"),
                    "source": f"comment by {c.get('author')} on {(c.get('created') or '')[:10]}"}
    if rec.get("assignee_name"):
        return {"name": rec["assignee_name"], "display_name": rec.get("assignee"), "source": "ticket assignee"}
    return None


def comments_of(fields):
    out = []
    for c in ((fields.get("comment") or {}).get("comments") or []):
        body = flatten_description(c.get("body")).strip()
        out.append({
            "author": (c.get("author") or {}).get("displayName"),
            "author_name": user_ident(c.get("author")),
            "created": c.get("created"),
            "body": body[:COMMENT_MAX_CHARS] + (" ...[truncated]" if len(body) > COMMENT_MAX_CHARS else ""),
        })
    return out


# ---------------------------------------------------------------------------
# Git host: Bitbucket Cloud or GitHub, chosen by git.host or the remote URL
# ---------------------------------------------------------------------------

def repo_slug():
    """owner/repo (workspace/repo) from the remote, credentials and .git stripped."""
    url = remote_url()
    if not url:
        return ""
    url = re.sub(r"^[a-z]+://", "", url)
    url = re.sub(r"^[^@/]+@", "", url)
    url = re.sub(r"^[^:/]+[:/]", "", url)
    url = re.sub(r"\.git$", "", url).strip("/")
    return url if url.count("/") == 1 else ""


def shape_pr(pr):
    host = git_host()
    if host == "github":
        state = "MERGED" if pr.get("merged_at") else ("OPEN" if pr.get("state") == "open" else "DECLINED")
        return {
            "id": pr.get("number"), "title": pr.get("title"), "state": state,
            "source": (pr.get("head") or {}).get("ref"), "destination": (pr.get("base") or {}).get("ref"),
            "author": (pr.get("user") or {}).get("login"), "url": pr.get("html_url"), "updated": pr.get("updated_at"),
        }
    return {
        "id": pr.get("id"), "title": pr.get("title"), "state": pr.get("state"),
        "source": ((pr.get("source") or {}).get("branch") or {}).get("name"),
        "destination": ((pr.get("destination") or {}).get("branch") or {}).get("name"),
        "author": (pr.get("author") or {}).get("display_name"),
        "url": ((pr.get("links") or {}).get("html") or {}).get("href"),
        "updated": pr.get("updated_on"),
    }


def host_ready():
    host = git_host()
    auth = host_auth_header()
    slug = repo_slug()
    if not host:
        return None, None, f"PR lookup skipped: cannot tell the git host from remote '{GIT_REMOTE}' -- set git.host"
    if host not in HOST_API_DEFAULTS:
        return None, None, f"PR lookup skipped: unsupported git host '{host}' (bitbucket, github)"
    if not auth:
        return None, None, f"PR lookup skipped: no {host} credentials in {ENV_FILE or 'the environment'}"
    if not slug:
        return None, None, f"PR lookup skipped: cannot read owner/repo from remote '{GIT_REMOTE}'"
    return auth, slug, ""


def host_pr_by_id(pr_id):
    auth, slug, note = host_ready()
    if note:
        return None
    base = host_api_base()
    if git_host() == "github":
        return shape_pr(http_get(f"{base}/repos/{slug}/pulls/{pr_id}", auth, what="GitHub"))
    return shape_pr(http_get(f"{base}/repositories/{slug}/pullrequests/{pr_id}", auth, what="Bitbucket"))


def host_prs_for_branches(branches):
    """PRs whose source branch is one the ticket names."""
    auth, slug, note = host_ready()
    if note or not branches:
        return []
    base = host_api_base()
    found = []
    for b in branches:
        if git_host() == "github":
            owner = slug.split("/")[0]
            data = http_get(f"{base}/repos/{slug}/pulls", auth, {"head": f"{owner}:{b}", "state": "all", "per_page": 10},
                            what="GitHub")
            found.extend(shape_pr(pr) for pr in data)
        else:
            data = http_get(f"{base}/repositories/{slug}/pullrequests", auth,
                            {"q": f'source.branch.name = "{b}"', "pagelen": 10, "state": ["OPEN", "MERGED", "DECLINED"]},
                            what="Bitbucket")
            found.extend(shape_pr(pr) for pr in data.get("values", []))
    return found


def host_prs(key):
    """PRs whose source branch or title mentions the ticket key -- a hint, never the source."""
    auth, slug, note = host_ready()
    if note:
        return {"prs": [], "note": note}
    base = host_api_base()
    found = []
    if git_host() == "github":
        data = http_get(f"{base}/search/issues", auth, {"q": f"repo:{slug} is:pr {key} in:title", "per_page": 20},
                        what="GitHub")
        for item in data.get("items", []):
            pr = host_pr_by_id(item.get("number"))
            if pr:
                found.append(pr)
        return {"prs": found, "note": ""}
    q = f'(source.branch.name ~ "{key}" OR title ~ "{key}")'
    for state in ("OPEN", "MERGED", "DECLINED"):
        data = http_get(f"{base}/repositories/{slug}/pullrequests", auth,
                        {"q": f'{q} AND state = "{state}"', "pagelen": 20}, what="Bitbucket")
        found.extend(shape_pr(pr) for pr in data.get("values", []))
        if found and state == "OPEN":
            break
    return {"prs": found, "note": ""}


def git_branches(key):
    """Local and remote branches whose name contains the ticket key."""
    try:
        out = subprocess.run(
            ["git", "branch", "-a", "--list", f"*{key}*", "--format=%(refname:short)"],
            capture_output=True, text=True, check=True, timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return []
    return sorted({b.strip() for b in out.stdout.splitlines() if b.strip()})


# ---------------------------------------------------------------------------
# Shape and render
# ---------------------------------------------------------------------------

def shape(issue, with_dev=True):
    f = issue.get("fields", {})
    key = issue.get("key")
    base = env("JIRA_BASE_URL").rstrip("/")
    rec = {
        "key": key,
        "url": f"{base}/browse/{key}",
        "summary": f.get("summary"),
        "description": flatten_description(f.get("description")).strip(),
        "status": (f.get("status") or {}).get("name"),
        "type": (f.get("issuetype") or {}).get("name"),
        "priority": (f.get("priority") or {}).get("name"),
        "assignee": (f.get("assignee") or {}).get("displayName"),
        "assignee_name": user_ident(f.get("assignee")),
        "reporter": (f.get("reporter") or {}).get("displayName"),
        "labels": f.get("labels", []),
        "updated": f.get("updated"),
        "base_branch": BASE_BRANCH,
        "comments": comments_of(f),
    }
    # What the ticket itself says about where the code is. Description first, then
    # comments oldest to newest, so the developer's "Branch:" / "PR:" lines are found.
    rec["ticket_branches"], rec["ticket_pr_ids"], rec["superseded"] = current_references(rec)
    rec["developer"] = developer_of(rec)
    rec["missing_on_ticket"] = [name for name, val in (("branch name", rec["ticket_branches"]),
                                                       ("PR link", rec["ticket_pr_ids"])) if not val]

    if with_dev and key:
        seen, prs = set(), []

        def add(pr):
            if pr and pr.get("id") not in seen:
                seen.add(pr["id"])
                prs.append(pr)

        for pid in rec["ticket_pr_ids"]:                                    # 1. PR linked on the ticket
            add(host_pr_by_id(pid))
        for pr in host_prs_for_branches(rec["ticket_branches"]):   # 2. PR for the named branch
            add(pr)
        bb = host_prs(key)                               # 3. PR mentioning the key
        for pr in bb["prs"]:
            add(pr)
        rec["pull_requests"] = prs
        rec["branches"] = git_branches(key)
        if bb["note"]:
            rec["note"] = bb["note"]
    return rec


def render(tickets):
    if not tickets:
        print("No tickets are waiting for your review.")
        return
    print(f"{len(tickets)} ticket(s) in your review queue:\n")
    for t in tickets:
        print(f"  {t['key']}  [{t.get('priority') or '-'}]  {t.get('summary')}")
        print(f"      status:   {t.get('status')}   author: {t.get('assignee') or '-'}")
        print(f"      link:     {t['url']}")
        for b in t.get("ticket_branches", []):
            print(f"      ticket says branch: {b}")
        for pid in t.get("ticket_pr_ids", []):
            print(f"      ticket says PR:     #{pid}")
        missing = [name for name, val in (("branch name", t.get("ticket_branches")),
                                          ("PR link", t.get("ticket_pr_ids"))) if not val]
        if missing:
            print(f"      MISSING on ticket:  {', '.join(missing)}  -> review blocked until added")
        for pr in t.get("pull_requests", []):
            print(
                f"      PR #{pr.get('id')}: {pr.get('title')} "
                f"[{pr.get('source')} -> {pr.get('destination')}] ({pr.get('state')})"
            )
            if pr.get("url"):
                print(f"                {pr['url']}")
        for br in t.get("branches", []):
            print(f"      branch:   {br}")
        if not t.get("pull_requests") and not t.get("branches"):
            print("      branch:   (none found -- resolve from the ticket key)")
        if t.get("note"):
            print(f"      note:     {t['note']}")
        print()


def main():
    ap = argparse.ArgumentParser(description="Fetch your Jira code-review queue.")
    ap.add_argument("--ticket", help="Fetch one specific ticket instead of the queue")
    ap.add_argument("--jql", help="Override the JQL query entirely")
    ap.add_argument("--json", action="store_true", dest="as_json", help="JSON output")
    ap.add_argument("--max", type=int, default=25, help="Max tickets (default 25)")
    ap.add_argument("--no-dev", action="store_true", help="Skip branch/PR lookup")
    args = ap.parse_args()

    # $ARGUMENTS expands to an empty string when the skill is invoked with no ticket key,
    # so an empty --ticket must mean "fetch the whole queue", not "fetch ticket ''".
    ticket = (args.ticket or "").strip()
    m = re.search(r"\b([A-Z][A-Z0-9]+-\d+)\b", ticket)
    if ticket and not m:
        sys.exit(f"error: could not find a ticket key in '{ticket}'")
    if m:
        ticket = m.group(1)

    if ticket:
        issue = jira_get(f"/rest/api/2/issue/{ticket}", {"fields": FIELDS})
        tickets = [shape(issue, not args.no_dev)]
    else:
        jql = args.jql or default_jql()
        data = jira_get("/rest/api/2/search", {"jql": jql, "fields": FIELDS, "maxResults": args.max})
        tickets = [shape(i, not args.no_dev) for i in data.get("issues", [])]

    me = jira_get("/rest/api/2/myself")
    reviewer = {"name": me.get("name"), "display_name": me.get("displayName")}

    if args.as_json:
        print(json.dumps({
            "mode": "ticket" if ticket else "queue",
            "count": len(tickets),
            "product": PRODUCT or (repo_slug().split("/")[-1] if repo_slug() else ""),
            "repo": repo_slug(),
            "git_host": git_host(),
            "review_status": REVIEW_STATUS,
            "reviewer": reviewer,
            "env_file": ENV_FILE,
            "jira_project": JIRA_PROJECT,
            "base_branch": BASE_BRANCH,
            "tickets": tickets,
        }, indent=2))
    else:
        print(f"(reviewer: {reviewer['display_name']}; project {JIRA_PROJECT or '?'}; "
              f"base {BASE_BRANCH or '?'}; credentials: {ENV_FILE or 'environment'})")
        render(tickets)


if __name__ == "__main__":
    main()
