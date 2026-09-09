# jira-review — Claude Code plugin

Rigorous code review of the pull request attached to a Jira ticket. Pulls your review
queue (or one ticket by key), resolves the branch and PR the ticket names, diffs from the
merge base, works a 28-point technology-independent checklist, writes a PASS/FAIL report
with severities and concrete `file:line` findings, and — with your confirmation — posts
the findings to the Bitbucket PR and the Jira ticket, then moves the ticket on:
**PASS → In QA, assigned to your QA person; FAIL → Needs Re-Work, assigned back to the
developer who put the branch/PR on the ticket.**

## Install

```
/plugin marketplace add girnarsoft/girnarsoft-jira-review
/plugin install jira-review@girnarsoft-review
```

Once installed it is available in every repository on that machine.

## Use

```
/jira-review              # list the Code Review tickets assigned to you, pick one
/jira-review OLMS-123     # review that ticket straight away
```

The review never approves or declines the PR, and never moves a ticket anywhere other
than the configured QA status (PASS) or rework transition (FAIL). Every write to Jira or
Bitbucket is confirmed first.

## What each repository needs

`<repo>/.claude/jira-project.json` — see [`examples/jira-project.json`](examples/jira-project.json):

| Key | Meaning |
|---|---|
| `jira.project` | Jira project key the queue is scoped to |
| `jira.default_qa` | who a PASS is assigned to (username or email) |
| `jira.qa_status` | status a PASS moves to (default `In QA`) |
| `jira.qa_path` | intermediate statuses allowed on the way there (default `["Dev Complete"]`) |
| `jira.rework_targets` | transition names / statuses tried in order on a FAIL (default `["Needs Re-Work", "Dev In Progress"]`) |
| `jira.rework_path` | intermediates for the rework move (default `[]`) |
| `git.base_branch` | branch PRs target; the diff is taken from the merge base with it |

Add `.review/` to the repository's `.gitignore` — the review writes its working files there.

## What each machine needs

- `~/.claude/jira.env` (or `<repo>/.claude/jira.env`, or `$JIRA_ENV_FILE`) with Jira and
  Bitbucket credentials — see [`examples/jira.env`](examples/jira.env). Never committed.
- `python3`, `jq`, `curl`, `git` on the PATH.

## How the ticket is read

- The **branch** is the `Branch:` line (also `PWA branch:` / `API branch:` / `Branch name -`)
  and the **PR** is a `bitbucket.org/.../pull-requests/<id>` link, on the ticket itself.
  Jira markup around them (bullets, bold, `{{monospace}}`) is fine.
- **The latest comment wins** for each. Older mentions are shown as superseded, never used.
- If either is missing the review posts a "cannot start" comment naming what to add and stops.
- The developer a FAIL goes back to is the author of the latest comment that carried the
  branch/PR (fallback: the assignee).

## Workflow moves

Jira only exposes transitions from the current status, so the handoff script walks the
configured path. In the OLMS workflow that is `Code Review → Dev Complete → In QA` for a
PASS, and the `Needs Re-Work` transition (Stories) or a plain `Dev In Progress` move
(Sub-tasks) for a FAIL. Adjust `qa_path` / `rework_targets` per project; both scripts take
`--dry-run`.

## Layout

```
.claude-plugin/plugin.json          plugin manifest
.claude-plugin/marketplace.json     marketplace manifest (this repo is its own marketplace)
skills/jira-review/SKILL.md         the skill
skills/jira-review/scripts/         fetch_review_tickets.py, collect_diff.sh, jira_comment.sh,
                                    post_pr_comment.sh, jira_handoff.sh
skills/jira-review/references/      review-checklist.md, report-template.md
examples/                           jira-project.json, jira.env templates
```
