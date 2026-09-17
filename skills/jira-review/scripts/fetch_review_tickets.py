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
  LEDGER_URL            Review Ledger service record_run.py sends runs to (optional;
                        default: ledger_url in the skill's tracking.json)
  LEDGER_TOKEN          bearer token, only when the ledger requires one

Everything project-specific comes from jira-project.json, looked up the same way:

  1. <git toplevel>/.claude/jira-project.json   per-repo file
  2. ~/.claude/jira-project.json                shared file

Keys:
  jira.project          project key the queue is scoped to           (required)
  jira.review_status    the status that means "waiting for review"   (required)
  jira.base_url         Jira URL when not in the environment
  jira.deployment       "server" | "cloud" (inferred from the URL when absent)
  git.base_branch       branch PRs target                             (required)
  git.host              "bitbucket" | "github" (inferred from the remote when absent)
  git.remote            remote name, default "origin"
  git.api_base          API base for a self-hosted instance (optional)
  git.protected_branches  names never taken as a feature branch (base_branch always is)
  repos                 OPTIONAL -- local clones of OTHER repositories whose pull requests
                        tickets link, as {"owner/repo": "path"}. The path is absolute or
                        relative to this repository's root. The current checkout is found
                        automatically and never needs an entry. All repositories must be on
                        the same git host.

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


def find_project_file():
    """jira-project.json: the repo's own copy first, the user's ~/.claude/ copy second."""
    top = git_toplevel()
    candidates = []
    if top:
        candidates.append(os.path.join(top, ".claude", "jira-project.json"))
    candidates.append(os.path.expanduser("~/.claude/jira-project.json"))
    for path in candidates:
        if os.path.isfile(path):
            return path
    return ""


def load_project_config(path):
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


ENV_FILE = find_env_file()
load_env_file(ENV_FILE)
PROJECT_FILE = find_project_file()
PROJECT = load_project_config(PROJECT_FILE)
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


def remote_url(path=None):
    cmd = ["git"] + (["-C", path] if path else []) + ["remote", "get-url", GIT_REMOTE]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=10)
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
# "Branch: X", "PWA branch: X", "PWA UI branch: X", "pwa-ui branch - X": up to three words
# may precede "branch". A branch name is only ever compared with the source branch of a PR
# the ticket links, so no word has to name a repository.
BRANCH_LINE = re.compile(
    r"(?im)^\s*(?:[*#\-]+\s*)?(?:[A-Za-z][\w\-]*\s+){0,3}branch(?:\s*name)?\s*[:\-]\s*(?:\{\{|[`'\"*_])*([A-Za-z0-9._/\-]+)"
)
# Bitbucket ".../<ws>/<repo>/pull-requests/<id>", GitHub ".../<owner>/<repo>/pull/<id>",
# GitLab ".../<group>/<project>/-/merge_requests/<id>". The path before the marker is the
# repository slug: a PR number alone is ambiguous as soon as two repositories exist.
# A "create a pull request" form (/pull-requests/new?...) has no number and never matches.
PR_URL = re.compile(r"https?://[^\s/]+/((?:[^\s/?#]+/)+?)(?:-/)?(?:pull-requests|pull|merge_requests)/(\d+)")


def references_in(text):
    """Branch lines and PR links in one text: (branch names, [{"id", "slug", "url"}])."""
    branches, prs = [], []
    for m in BRANCH_LINE.finditer(text or ""):
        name = m.group(1).rstrip(".,;)*_")
        if name.lower() not in PROTECTED and name not in branches:
            branches.append(name)
    for m in PR_URL.finditer(text or ""):
        slug, pid = m.group(1).strip("/"), int(m.group(2))
        if not any(x["id"] == pid and x["slug"].lower() == slug.lower() for x in prs):
            prs.append({"id": pid, "slug": slug, "url": m.group(0)})
    return branches, prs


# ---------------------------------------------------------------------------
# Local clones: the current checkout, plus "repos" {"owner/repo": "path"}
# ---------------------------------------------------------------------------

_CLONES = None


def git_ok(path, *args):
    try:
        return subprocess.run(["git", "-C", path, *args], capture_output=True, text=True, timeout=10).returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def clone_map():
    """lower-case slug -> {"slug", "path", "exists", "current"}: where a PR's repository is
    checked out on this machine."""
    global _CLONES
    if _CLONES is not None:
        return _CLONES
    top = os.path.realpath(git_toplevel() or os.getcwd())
    out = {}
    current = repo_slug()
    if current:
        out[current.lower()] = {"slug": current, "path": top, "exists": True, "current": True}
    declared = PROJECT.get("repos") or {}
    if isinstance(declared, list):  # tolerate [{"slug": ..., "path": ...}]
        declared = {d.get("slug"): d.get("path") for d in declared if isinstance(d, dict) and d.get("slug")}
    if not isinstance(declared, dict):
        sys.exit(f'error: "repos" in {PROJECT_FILE} must map "owner/repo" to a local path')
    for slug, raw in declared.items():
        slug = str(slug).strip("/")
        raw = os.path.expanduser(str(raw or ""))
        path = os.path.realpath(raw if os.path.isabs(raw) else os.path.join(top, raw))
        exists = os.path.isdir(path) and git_ok(path, "rev-parse", "--git-dir")
        out.setdefault(slug.lower(), {"slug": slug, "path": path, "exists": exists, "current": path == top})
    _CLONES = out
    return out


def clone_for(slug):
    return clone_map().get((slug or "").lower())


def repo_name(slug):
    return (slug or "").rstrip("/").split("/")[-1]


# Comments this skill posts itself quote the branch and PR back to the ticket. They must
# never count as the developer's word, or the reviewer's own "started" or findings comment
# would win the branch/PR resolution and the reviewer would become the "developer".
# Every comment the skill posts therefore begins with "Code review" -- optionally behind a
# Jira heading marker (h2.), bold (*...*), or a bullet -- and that is what is matched here,
# case-insensitively, on the first non-blank line only.
SKILL_COMMENT_FIRST_LINE = re.compile(
    r"^\s*(?:h[1-6]\.\s*)?(?:[*_{]+\s*)?(?:[-*#]+\s*)?code review\b", re.IGNORECASE
)


def is_skill_comment(comment):
    body = comment.get("body") or ""
    first = next((l for l in body.splitlines() if l.strip()), "")
    return bool(SKILL_COMMENT_FIRST_LINE.match(first))


def developer_comments(rec):
    """Comments written by people, oldest first -- the skill's own comments removed."""
    return [c for c in (rec.get("comments") or []) if not is_skill_comment(c)]


def ticket_references(rec):
    """Every branch named and every PR linked on the ticket, from developer comments (newest
    first) and then the description.

    There is no "latest comment wins" any more. Which PRs still count is decided by each
    PR's own state on the git host: two developers linking two PRs in separate comments
    both count, and a re-raised PR retires the old one because the old one is declined or
    superseded. Each PR remembers who linked it -- the person a failed review of that PR
    goes back to. Returns (branch names, [{"id", "slug", "url", "linked_by"}])."""
    branches, prs = [], []
    sources = [(c.get("body"), c) for c in reversed(developer_comments(rec))] + [(rec.get("description"), None)]
    for text, c in sources:
        names, links = references_in(text)
        for name in names:
            if name not in branches:
                branches.append(name)
        for link in links:
            if any(x["id"] == link["id"] and x["slug"].lower() == link["slug"].lower() for x in prs):
                continue
            if c and c.get("author_name"):
                link["linked_by"] = {"name": c["author_name"], "display_name": c.get("author"),
                                     "source": f"comment by {c.get('author')} on {(c.get('created') or '')[:10]}"}
            elif rec.get("assignee_name"):
                link["linked_by"] = {"name": rec["assignee_name"], "display_name": rec.get("assignee"),
                                     "source": "ticket assignee"}
            else:
                link["linked_by"] = None
            prs.append(link)
    return branches, prs


def developer_of(rec):
    """Who to hand a failed review back to when no single PR says: the author of the most
    recent comment that put a branch or PR reference on the ticket, else the assignee."""
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

def repo_slug(path=None):
    """owner/repo (workspace/repo) from the remote, credentials and .git stripped."""
    url = remote_url(path)
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
            "source_commit": (pr.get("head") or {}).get("sha"), "destination_commit": (pr.get("base") or {}).get("sha"),
            "merge_commit": pr.get("merge_commit_sha") if pr.get("merged_at") else None,
        }
    return {
        "id": pr.get("id"), "title": pr.get("title"), "state": pr.get("state"),
        "source": ((pr.get("source") or {}).get("branch") or {}).get("name"),
        "destination": ((pr.get("destination") or {}).get("branch") or {}).get("name"),
        "author": (pr.get("author") or {}).get("display_name"),
        "url": ((pr.get("links") or {}).get("html") or {}).get("href"),
        "updated": pr.get("updated_on"),
        "source_commit": (((pr.get("source") or {}).get("commit")) or {}).get("hash"),
        "destination_commit": (((pr.get("destination") or {}).get("commit")) or {}).get("hash"),
        "merge_commit": (pr.get("merge_commit") or {}).get("hash"),
    }


def host_ready(slug=None):
    host = git_host()
    auth = host_auth_header()
    slug = slug if slug is not None else repo_slug()
    if not host:
        return None, None, f"PR lookup skipped: cannot tell the git host from remote '{GIT_REMOTE}' -- set git.host"
    if host not in HOST_API_DEFAULTS:
        return None, None, f"PR lookup skipped: unsupported git host '{host}' (bitbucket, github)"
    if not auth:
        return None, None, f"PR lookup skipped: no {host} credentials in {ENV_FILE or 'the environment'}"
    if not slug:
        return None, None, f"PR lookup skipped: cannot read owner/repo from remote '{GIT_REMOTE}'"
    return auth, slug, ""


def host_pr_by_id(pr_id, slug=None):
    auth, slug, note = host_ready(slug)
    if note:
        return None
    base = host_api_base()
    if git_host() == "github":
        return shape_pr(http_get(f"{base}/repos/{slug}/pulls/{pr_id}", auth, what="GitHub"))
    return shape_pr(http_get(f"{base}/repositories/{slug}/pullrequests/{pr_id}", auth, what="Bitbucket"))


def host_prs_for_branches(branches, slug=None):
    """PRs whose source branch is one the ticket names."""
    auth, slug, note = host_ready(slug)
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


def host_prs(key, slug=None):
    """PRs whose source branch or title mentions the ticket key -- a hint, never the source."""
    auth, slug, note = host_ready(slug)
    if note:
        return {"prs": [], "note": note}
    base = host_api_base()
    found = []
    if git_host() == "github":
        data = http_get(f"{base}/search/issues", auth, {"q": f"repo:{slug} is:pr {key} in:title", "per_page": 20},
                        what="GitHub")
        for item in data.get("items", []):
            pr = host_pr_by_id(item.get("number"), slug)
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


def git_branches(key, path=None):
    """Local and remote branches whose name contains the ticket key."""
    try:
        out = subprocess.run(
            ["git"] + (["-C", path] if path else []) + ["branch", "-a", "--list", f"*{key}*", "--format=%(refname:short)"],
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
    # What the ticket itself says about where the code is: every branch named and every
    # PR linked. Each linked PR is one unit of review.
    branches, links = ticket_references(rec)
    rec["ticket_branches"] = branches
    rec["ticket_pr_ids"] = [link["id"] for link in links]
    rec["developer"] = developer_of(rec)
    rec["ticket_prs"] = []
    rec["missing_on_ticket"] = [name for name, val in (("branch name", branches), ("PR link", links)) if not val]
    rec["conflicts"], rec["local_problems"], rec["branches_without_pr"], rec["unreadable_prs"] = [], [], [], []

    if with_dev and key:
        notes = []

        def soft(fn, *args, default=None):
            """One failed lookup (a deleted PR, a repository this login cannot see) must not
            abort the whole queue: keep going, say why."""
            try:
                return fn(*args)
            except SystemExit as exc:
                notes.append(str(exc).splitlines()[0][:200])
                return default

        units = []
        for link in links:
            before = len(notes)
            pr = soft(host_pr_by_id, link["id"], link["slug"])
            clone = clone_for(link["slug"])
            name = repo_name(link["slug"])
            unit = {"unit": f"{name}-pr{link['id']}", "repo": name, "slug": link["slug"], "id": link["id"],
                    "url": link["url"], "linked_by": link["linked_by"],
                    "clone": clone["path"] if clone else None, "clone_exists": bool(clone and clone["exists"]),
                    "current_repo": bool(clone and clone["current"]), "found": pr is not None}
            for field in ("title", "state", "source", "destination", "author", "updated",
                          "source_commit", "destination_commit", "merge_commit"):
                unit[field] = (pr or {}).get(field)
            if pr and pr.get("url"):
                unit["url"] = pr["url"]
            unit["reviewable"] = (unit["state"] or "").upper() in ("OPEN", "MERGED")
            unit["branch_on_ticket"] = bool(unit["source"]) and unit["source"] in branches
            if not pr:
                why = notes[before] if len(notes) > before else "no details returned"
                rec["unreadable_prs"].append(f"PR #{link['id']} in {link['slug']} could not be read from the git host "
                                             f"({why}) -- a wrong or deleted link, or no access for this login")
            elif unit["reviewable"] and not unit["branch_on_ticket"]:
                rec["conflicts"].append(f"PR #{link['id']} ({link['slug']}) is from branch {unit['source']}, "
                                        "which the ticket does not name")
            if pr and unit["reviewable"] and not unit["clone_exists"]:
                rec["local_problems"].append(
                    f"PR #{link['id']} is in {link['slug']}, which has no local clone -- clone it and add "
                    f'"{link["slug"]}": "<path>" under "repos" in {PROJECT_FILE or ".claude/jira-project.json"}')
            units.append(unit)
        rec["ticket_prs"] = units
        found = [u for u in units if u["found"]]
        if links and not found:
            rec["local_problems"].append("none of the PRs linked on the ticket could be read from the git host -- "
                                         "check the git-host credentials: " + "; ".join(rec["unreadable_prs"])[:300])
        if found and not any(u["reviewable"] for u in found):
            rec["missing_on_ticket"].append("an open or merged PR (every PR linked is declined or superseded)")
        sources = {u["source"] for u in units if u["source"]}
        rec["branches_without_pr"] = [b for b in branches if b not in sources]

        # Hints for a blocked review: PRs found by branch name or ticket key in every
        # repository this machine knows -- never the source of a review.
        seen, prs = set(), []

        def add(pr, slug, linked):
            if not pr:
                return
            ident = ((slug or "").lower(), pr.get("id"))
            if ident in seen:
                return
            seen.add(ident)
            prs.append(dict(pr, slug=slug, repo=repo_name(slug), linked=linked))

        for u in found:
            add({k: u[k] for k in ("id", "title", "state", "source", "destination", "author", "url", "updated")},
                u["slug"], True)
        known = [c["slug"] for c in clone_map().values()] or [repo_slug()]
        for slug in known:
            for pr in soft(host_prs_for_branches, branches, slug, default=[]) or []:
                add(pr, slug, False)
            bb = soft(host_prs, key, slug, default={"prs": [], "note": ""})
            for pr in bb["prs"]:
                add(pr, slug, False)
            if bb["note"]:
                notes.append(bb["note"])
        rec["pull_requests"] = prs
        rec["branches"] = [b if c["current"] else f"{repo_name(c['slug'])}: {b}"
                           for c in clone_map().values() if c["exists"] for b in git_branches(key, c["path"])] \
            or git_branches(key)
        if notes:
            rec["note"] = "; ".join(dict.fromkeys(notes))
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
        for u in t.get("ticket_prs", []):
            state = u.get("state") or "not found"
            flags = [] if u.get("reviewable") else ["not reviewable"]
            if u.get("reviewable") and not u.get("branch_on_ticket"):
                flags.append("branch not on ticket")
            if u.get("reviewable") and not u.get("clone_exists"):
                flags.append("no local clone")
            print(f"      ticket PR:  {u['repo']} #{u['id']} {u.get('title') or ''} "
                  f"[{u.get('source')} -> {u.get('destination')}] ({state})" + (f"  <- {', '.join(flags)}" if flags else ""))
        for label, items in (("MISSING on ticket", t.get("missing_on_ticket")), ("CONFLICT", t.get("conflicts")),
                             ("LOCAL SETUP", t.get("local_problems")), ("UNREADABLE", t.get("unreadable_prs"))):
            for item in items or []:
                print(f"      {label}: {item}")
        for b in t.get("branches_without_pr") or []:
            print(f"      branch without a PR: {b}")
        for pr in [x for x in t.get("pull_requests", []) if not x.get("linked")]:
            print(f"      found by search: {pr.get('repo')} #{pr.get('id')} {pr.get('title')} "
                  f"[{pr.get('source')} -> {pr.get('destination')}] ({pr.get('state')})")
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
            "project_file": PROJECT_FILE,
            "jira_project": JIRA_PROJECT,
            "base_branch": BASE_BRANCH,
            "repos": list(clone_map().values()),
            "tickets": tickets,
        }, indent=2))
    else:
        print(f"(reviewer: {reviewer['display_name']}; project {JIRA_PROJECT or '?'}; "
              f"base {BASE_BRANCH or '?'}; credentials: {ENV_FILE or 'environment'})")
        render(tickets)


if __name__ == "__main__":
    main()
