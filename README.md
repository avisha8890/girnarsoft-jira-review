# jira-review — Claude Code plugin

Rigorous code review of the pull requests attached to a Jira ticket. Pulls your review
queue (or one ticket by key), resolves every PR the ticket links — one PR, one PR per
repository, or several PRs in one repository — and checks each out in its own git
worktree. It shows you a review plan to approve or extend, works a 28-point
technology-independent checklist plus stack-specific checks, writes a PASS/FAIL report with
severities and concrete `file:line` findings, posts the findings to each PR (Bitbucket
Cloud or GitHub) and to the Jira ticket, and — on your yes — moves the ticket on:
**PASS → your QA status, assigned to your QA person; FAIL → your rework transition,
assigned back to the developer who linked the failing PR.** Every run is recorded in the
team's Review Ledger service.

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

The review never approves or declines a PR, and never moves a ticket anywhere other
than the configured QA status (PASS) or rework transition (FAIL). Selecting a ticket is
the go-ahead for its comments ("cannot start", "review started" once you approve the plan,
and the findings), which are posted without asking; the ticket move always asks first.
Your own checkout is never switched or modified: each PR is read, built and tested in a
worktree under `.review/`, removed at the end.

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
| `repos` | local clones of **other** repositories whose PRs tickets link, as `{"owner/repo": "path"}` (path absolute or relative to this repo's root). The current checkout is found automatically; one repo with several PRs needs nothing here |
| `tracking.ledger_url` | send this repo's runs to a different Review Ledger (default: `ledger_url` in the plugin's `tracking.json`) |

Both files are looked up in the repository first (`<repo>/.claude/`), then in `~/.claude/`.
The review writes its working files to `.review/` and adds that folder to `.gitignore`
itself the first time.

A ticket whose PRs span repositories, for example a web app in its own repo:

```json
"repos": { "girnarsoftware/lms-pwa-ui": "../lms-pwa-ui" }
```

Every repository must be cloned on the reviewer's machine and live on the same git host.

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
- Network access to the Review Ledger service (see below). `LEDGER_URL` in `jira.env`
  overrides its address; `LEDGER_TOKEN` is needed only if the ledger requires one.

## How the ticket is read

- The ticket must name at least one **branch** (a `Branch:` line; up to three words may
  precede it, e.g. `PWA UI branch:`) and link at least one **PR** (`.../pull-requests/<id>`
  on Bitbucket, `.../pull/<id>` on GitHub). Jira markup around them is fine.
- **Every linked PR is a unit of review.** Which ones count is decided by each PR's state
  on the git host, not by comment order: open and merged PRs are reviewable (a merged PR is
  diffed from its merge commit), declined and superseded ones are ignored. The skill's own
  comments are never read as links.
- Missing branch or PR, or a reviewable PR from a branch the ticket does not name → the
  review posts a "cannot start" comment naming what to add and stops.
- With several reviewable PRs you choose which to review (default: all); the plan and
  report then add checks between the PRs (shared fields, statuses, deploy order).
- A FAIL goes back to the person who linked the failing PR.

## The ledger

Every run — pass, fail or blocked — is sent to the team's **Review Ledger** service, a small
Spring Boot + PostgreSQL application hosted inside the network, with a dashboard at the
same address. The address is `ledger_url` in `skills/jira-review/tracking.json`;
`record_run.py` sends the record itself over HTTP (`PUT /api/v1/runs/<run_id>`), so no
Claude account, subscription or tool permission is involved and anyone on the network can
record work. Sending the same run again replaces it rather than duplicating it.

If the ledger cannot be reached (off the network, service down), the run is kept in
`.review/pending/` and sent with the next run, or immediately with
`python3 skills/jira-review/scripts/record_run.py --flush`.

Per ticket the dashboard shows iterations, time to PASS, reviewer and developer; per run:
start time, duration, verdict, finding counts, diff size, complexity, outcome and every
reviewed PR. A fork for another organisation changes `ledger_url` once; one repository can
point elsewhere with `tracking.ledger_url`, one machine with `LEDGER_URL` in `jira.env`.

## Layout

```
.claude-plugin/plugin.json          plugin manifest
.claude-plugin/marketplace.json     marketplace manifest (this repo is its own marketplace)
skills/jira-review/SKILL.md         the skill
skills/jira-review/scripts/         fetch_review_tickets.py, collect_diff.sh, jira_comment.sh,
                                    post_pr_comment.sh, jira_handoff.sh, record_run.py
skills/jira-review/scripts/lib/     common.sh — credentials, Jira URL, git host, API bases
skills/jira-review/tracking.json    the Review Ledger address every run is sent to
skills/jira-review/references/      review-checklist.md, report-template.md
examples/                           jira-project.json, jira.env templates
```

## Supported platforms

- Jira Server / Data Center and Jira Cloud (REST v2; user identity by `name` or `accountId`).
- Bitbucket Cloud and GitHub (github.com, or a self-hosted instance via `git.api_base`).
- Any language or build: the checklist asks behavioural questions and step 2 of the
  skill reads the repository's own conventions (`CLAUDE.md`, manifests, neighbouring
  files) before judging the diff.
