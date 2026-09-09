# jira-review — Claude Code plugin

Rigorous code review of the pull request attached to a Jira ticket. Pulls your review
queue (or one ticket by key), resolves the branch and PR the ticket names, diffs from the
merge base, works a 28-point technology-independent checklist, writes a PASS/FAIL report
with severities and concrete `file:line` findings, and — with your confirmation — posts
the findings to the PR (Bitbucket Cloud or GitHub) and the Jira ticket, then moves the
ticket on: **PASS → your QA status, assigned to your QA person; FAIL → your rework
transition, assigned back to the developer who put the branch/PR on the ticket.**

Nothing about a company, a Jira workflow, a git host or a programming language is built
in. Each repository declares its own project key, status names, people, base branch and
host in one file, and the review checklist is written in terms of behaviour, not syntax.

## Install

```
/plugin marketplace add avisha8890/girnarsoft-jira-review
/plugin install jira-review@girnarsoft-review
```

Once installed it is available in every repository on that machine.

## Use

```
/jira-review              # list the Code Review tickets assigned to you, pick one
/jira-review PROJ-123     # review that ticket straight away
```

The review never approves or declines the PR, and never moves a ticket anywhere other
than the configured QA status (PASS) or rework transition (FAIL). Every write to Jira or
the git host is confirmed first.

## What each repository needs

`<repo>/.claude/jira-project.json` — copy [`examples/jira-project.json`](examples/jira-project.json)
and fill it in. Required keys are marked; everything else is inferred or optional.

| Key | Meaning |
|---|---|
| `product` | name shown on the ledger (default: repository name) |
| `jira.base_url` | Jira URL, if not set as `JIRA_BASE_URL` in `jira.env` |
| `jira.project` | Jira project key the queue is scoped to — **required** |
| `jira.review_status` | the status that means "waiting for code review" — **required** |
| `jira.deployment` | `server` or `cloud`; inferred from the URL (`*.atlassian.net` = cloud) |
| `jira.default_qa` | who a PASS is assigned to (username or email) — **required for handoff** |
| `jira.qa_status` | status a PASS moves to — **required for handoff** |
| `jira.qa_path` | intermediate statuses when there is no direct move, in order (default none) |
| `jira.rework_targets` | transition names or statuses tried in order on a FAIL — **required for handback** |
| `jira.rework_path` | intermediates for the rework move (default none) |
| `git.base_branch` | branch PRs target; the diff is taken from the merge base with it — **required** |
| `git.host` | `bitbucket` or `github`; inferred from the remote URL |
| `git.remote` | remote name (default `origin`) |
| `git.api_base` | API base for a self-hosted instance (optional) |
| `git.protected_branches` | names never taken as a feature branch (`base_branch` always is) |
| `tracking.artifact_url` | point this repo at a different ledger page (default: the plugin's shared one) |

Add `.review/` to the repository's `.gitignore` — the review writes its working files there.

Jira only exposes workflow moves from a ticket's *current* status, so if your review
status has no direct move to the QA status, list the stepping-stone statuses in
`qa_path`. `rework_targets` accepts transition **names** as well as statuses, so a
workflow that calls the move "Needs Re-Work" for one issue type and plain "In Progress"
for another is one list, tried in order. Both scripts take `--dry-run`, which prints the
first hop without writing anything.

## What each machine needs

- `~/.claude/jira.env` (or `<repo>/.claude/jira.env`, or `$JIRA_ENV_FILE`) with Jira
  credentials and the git host's — see [`examples/jira.env`](examples/jira.env). Never committed.
- `python3`, `jq`, `curl`, `git` on the PATH.

## How the ticket is read

- The **branch** is the `Branch:` line (also `PWA branch:` / `API branch:` / `Branch name -`)
  and the **PR** is its link on the git host (`.../pull-requests/<id>` on Bitbucket,
  `.../pull/<id>` on GitHub), on the ticket itself.
  Jira markup around them (bullets, bold, `{{monospace}}`) is fine.
- **The latest comment wins** for each. Older mentions are shown as superseded, never used.
- If either is missing the review posts a "cannot start" comment naming what to add and stops.
- The developer a FAIL goes back to is the author of the latest comment that carried the
  branch/PR (fallback: the assignee).

## The ledger

Every run — pass, fail or blocked — records itself on one shared page:

**https://claude.ai/code/artifact/f4af8e7b-658e-43d4-95a8-776e7d276211**

Per ticket: type, priority, complexity, how many review iterations, time to PASS, who
reviewed and who developed. Per run: start time, duration, verdict, finding counts, diff
size, outcome and links to the PR and ticket. The URL is fixed in
`skills/jira-review/tracking.json`; the skill's step 6 writes the record through the
Artifact tool, so no extra credentials are needed. The page keeps its data in the
artifact's own store, which means it is organisation-internal: sign in to claude.ai
with an account in the owning organisation to view it or to have your runs recorded.
Records carry `project`, `product` and `repo`, and the page filters by project, so many
repositories share one ledger. A fork of this plugin for another organisation replaces
the URL in `tracking.json` once; a single repository can point elsewhere with
`tracking.artifact_url`.

## Layout

```
.claude-plugin/plugin.json          plugin manifest
.claude-plugin/marketplace.json     marketplace manifest (this repo is its own marketplace)
skills/jira-review/SKILL.md         the skill
skills/jira-review/scripts/         fetch_review_tickets.py, collect_diff.sh, jira_comment.sh,
                                    post_pr_comment.sh, jira_handoff.sh, record_run.py
skills/jira-review/scripts/lib/     common.sh — credentials, Jira URL, git host, API bases
skills/jira-review/tracking.json    the ledger page every run records to
ledger/review-ledger.html           the ledger page's source — publish it as a Claude artifact with the
                                    db capability to run your own, then put its URL in tracking.json
skills/jira-review/references/      review-checklist.md, report-template.md
examples/                           jira-project.json, jira.env templates
```

## Supported platforms

- Jira Server / Data Center and Jira Cloud (REST v2; user identity by `name` or `accountId`).
- Bitbucket Cloud and GitHub (github.com, or a self-hosted instance via `git.api_base`).
- Any language or build: the checklist asks behavioural questions and step 2 of the
  skill reads the repository's own conventions (`CLAUDE.md`, manifests, neighbouring
  files) before judging the diff.
