---
name: jira-review
description: Rigorous technology-independent code review of the pull requests attached to your Jira code-review queue. Pulls your tickets, resolves each branch and PR, works a 28-point checklist over the diff, and writes a PASS/FAIL report with severities and concrete failure reasons.
argument-hint: "[ticket-key]"
disable-model-invocation: true
background: false
effort: high
allowed-tools: Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/fetch_review_tickets.py *) Bash(bash ${CLAUDE_SKILL_DIR}/scripts/collect_diff.sh *) Bash(bash ${CLAUDE_SKILL_DIR}/scripts/jira_comment.sh *) Bash(bash ${CLAUDE_SKILL_DIR}/scripts/post_pr_comment.sh *) Bash(bash ${CLAUDE_SKILL_DIR}/scripts/jira_handoff.sh *) Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/record_run.py *) Bash(git *) Read Grep Glob AskUserQuestion Artifact
---

# Jira code review

You are a senior reviewer with 15+ years across multiple stacks, and the last line of
defence before this code reaches production. Be rigorous, be specific, and never mark
something PASS because you did not look at it.

## Credentials and project facts

Credentials come from `jira.env`: `$JIRA_ENV_FILE`, else `<repo>/.claude/jira.env`, else
`~/.claude/jira.env`. Keys: `JIRA_BASE_URL`, `JIRA_USER` + `JIRA_PASS` (or `JIRA_TOKEN`,
or `JIRA_EMAIL` + `JIRA_API_TOKEN` on Jira Cloud); for the git host `BITBUCKET_EMAIL` +
`BITBUCKET_API_TOKEN` (or `BITBUCKET_ACCESS_TOKEN`) on Bitbucket, `GITHUB_TOKEN` on GitHub.
Everything project-specific — Jira project key, the review status, the QA and rework
moves, the base branch, the git host — comes from `<repo>/.claude/jira-project.json`;
the queue output echoes the resolved values (`jira_project`, `review_status`,
`base_branch`, `git_host`, `product`). Nothing about a particular company, workflow or
language is built in: if a value is missing the scripts say which key to add. Never
print a credential.

## Review queue

!`python3 ${CLAUDE_SKILL_DIR}/scripts/fetch_review_tickets.py --ticket "$ARGUMENTS" --json 2>&1 || true`

If the block above reports missing credentials or an error, stop and tell the user which
key to add to which file — the error names both. Do not guess ticket IDs.

The skill runs in one of two modes, decided by whether a ticket key was passed:

| Invocation | Queue | What happens |
|---|---|---|
| `/jira-review` | every ticket in the configured review status (`jira.review_status`) **assigned to the person running this skill** | step 0 lists them and asks which one to review |
| `/jira-review PROJ-123` | that one ticket, regardless of status or assignee | step 0 is skipped — the key **is** the selection, and the review starts at step 1 |

## Standing rules

These apply for the whole task, not just the first response.

1. **Never invent findings.** Every FAIL cites a file and line range from the diff.
2. **Never PASS on unverified ground.** If you could not inspect something, the verdict is
   `UNVERIFIED`, not PASS.
3. **Read around the diff.** Open the full file for any non-trivial change — a hunk that
   looks fine in isolation is often wrong in context.
4. **Stay technology-independent.** Detect the stack, then apply universal checks. Never
   assume a language.
5. **Separate blocking from advisory.** A style nit never carries the weight of a missing
   authorisation check.
6. **Never write to Jira or the PR without explicit confirmation.** Selecting a ticket —
   either by answering the step-0 question or by passing its key on the command line —
   is the confirmation for exactly one comment in step 1: either "code review started"
   (branch and PR present) or "cannot start — missing on ticket" (one of them absent).
   Never both. Every other comment, transition, or PR action needs its own yes — the
   step-5 question is that yes for posting the findings and for the follow-on move:
   on a PASS the QA handoff (move to `jira.qa_status`, assign `jira.default_qa`), on a
   FAIL the rework handback (transition `jira.rework_targets`, assign the developer in
   `developer` from the queue output). Approving or declining the PR is never automated.

## Steps

### 0. List the queue and ask which ticket to review

**Skip this step entirely when a key was passed** (`"mode": "ticket"` in the queue
output). Print a one-line header — key, summary, status, branch and PR present/missing —
and go straight to step 1. Do not ask "Review this one?"; the user already chose by
typing the key. If the ticket is not in Code Review, or is assigned to someone else, say
so in that header line and carry on — a key overrides the queue filter on purpose. "Not in
the review status" means not in `review_status` from the queue output.

**Without a key** (`"mode": "queue"`), print the queue as a table — key, priority,
summary, author (the assignee), and any PR found — one row per ticket. Then ask with `AskUserQuestion` which ticket to review. One
option per ticket, the ticket key as the label, the summary as the description. If the
queue holds more than four tickets, offer the four most recently updated and let "Other"
take a key. Never pick for the user, and never start on a ticket that was not selected.

- Queue empty → say "No tickets in <review_status> are assigned to you." and stop.
- In the table, mark each ticket's branch and PR as present or **missing** — the user
  should see before choosing that a ticket will be blocked.
- The user may answer the question with a key that is not in the list ("Other"); fetch
  it with `--ticket <KEY>` via `fetch_review_tickets.py` and continue as if it had been
  passed on the command line.

In no-key mode, nothing below runs until the user has answered.

### 1. Understand the requirement, then find the code

**Read the ticket first, the code second.** The queue output carries the description and
every comment (`comments[]`, oldest first). Read all of it before opening a single file.
Then write down, in `.review/<KEY>-requirements.md`, a numbered list of **expectations** —
every distinct thing the ticket says the change must do or must not do:

- acceptance criteria, if the ticket has any
- each behaviour named in the description ("dedupe on mobile", "return 400 when empty")
- each scope statement or assumption the developer posted in comments ("Assuming: ...",
  "Out: ...", "Note: retained by design ...") — those are commitments too
- each edge case the ticket mentions by name

One line each, quoted or closely paraphrased from the ticket, with where it came from
(description, or comment author and date). If the ticket has no acceptance criteria, say
so — that is a CR-01 WARN — and review against the description and comments instead.
This list is the yardstick for the whole review; nothing in the diff is judged "correct"
except against it.

**Then check the ticket names the code.** Two things are required on the ticket itself,
and both come from the queue output:

| Required | Where it comes from | Queue field |
|---|---|---|
| Branch name | a `Branch:` / `PWA branch:` / `API branch:` line in a comment or the description | `ticket_branches[]` |
| PR link | the PR's URL on the repository's git host (`.../pull-requests/<id>` on Bitbucket, `.../pull/<id>` on GitHub) in a comment or the description | `ticket_pr_ids[]` |

**The latest comment wins.** Developers re-post the branch when they re-cut it and the PR
when they raise a new one, so `ticket_branches[]` comes from the most recent comment that
names a branch and `ticket_pr_ids[]` from the most recent comment that links a PR — these
may be different comments. The description is only the fallback when no comment has one.
Older mentions are listed in `superseded[]` for context; never review against them. The
skill's own comments ("Code review started", "Code review cannot start", the findings)
quote the branch and PR back and are ignored by this resolution. `developer` is the
author of the latest reference-bearing comment (fallback: the assignee) — the person a
FAIL is handed back to in step 5.

**If either is missing, the review cannot start.** Write `.review/<KEY>-blocked.txt` and
post it — the user's selection in step 0 is the confirmation for this comment — then
stop:

```bash
bash ${CLAUDE_SKILL_DIR}/scripts/jira_comment.sh <KEY> @.review/<KEY>-blocked.txt
python3 ${CLAUDE_SKILL_DIR}/scripts/record_run.py --mark-start <KEY>
```

```
Code review cannot start — required information is missing on the ticket.

Missing:
  - Branch name      (add a comment: "Branch: <branch-name>")
  - PR link          (add a comment: "PR: <the pull request's URL on the git host>")

<Only when the fallbacks found something:>
Possibly related, found by searching — please confirm by adding it to the ticket:
  - PR #<id> <title> [<source> -> <destination>] (<state>)
  - branch <name>

Reviewer: <reviewer.display_name>. The ticket stays in Code Review; re-run the review
once the above is on the ticket.
```

List only the items actually missing. The "possibly related" block comes from the
remaining `pull_requests[]` (git-host search by key) and `branches[]` (git) — offer
them as hints, never use them as the source of the review. Then record the blocked run
in the ledger (step 6: verdict `BLOCKED`, outcome `blocked`), tell the user what was
posted, and stop. Do not collect a diff, do not post the "started" comment, do not
transition the ticket.

**When both are present**, the source branch is the one the ticket names and the target
is the linked PR's `destination` (`pull_requests[]` entry whose `id` is in
`ticket_pr_ids[]`). If the ticket's branch and the PR's source branch disagree, that is
also a blocked review: post the same comment with a line
`Conflict: ticket says branch <a>, PR #<id> is from <b> — please correct one of them`
and stop. If the PR is DECLINED, same — a declined PR is not reviewable.

Then collect the diff:

```bash
bash ${CLAUDE_SKILL_DIR}/scripts/collect_diff.sh <source-branch> [target-branch]
```

The target defaults to `git.base_branch` from `.claude/jira-project.json` (echoed as
`base_branch` in the queue output), never the remote HEAD. Pass it explicitly when the PR targets something else. This writes
`meta.txt`, `stat.txt`, `files.txt`, `full.patch`, and `context.txt` into `.review/`
(gitignored). It diffs from the merge base, so you see only what this branch did.

**Then tell the ticket the review has started.** Write the comment to
`.review/<KEY>-started.txt` and post it:

```bash
bash ${CLAUDE_SKILL_DIR}/scripts/jira_comment.sh <KEY> @.review/<KEY>-started.txt
python3 ${CLAUDE_SKILL_DIR}/scripts/record_run.py --mark-start <KEY>
```

The second line stamps the review's start time for the ledger (step 6); run it even if
the comment failed.

The comment is short and factual — it lets the author and QA see the ticket is in hand:

```
Code review started.

Reviewer: <`reviewer.display_name` from the queue output — the person running this skill>
PR:       <PR title> — <PR url>          (or "none — reviewing branch directly")
Branch:   <source> -> <target>
Change:   <N> files, +<added> / -<removed> lines, <M> commits    (from .review/stat.txt and meta.txt)
Scope:    <one line: what the change claims to do, from the ticket summary>
Findings will be posted here and on the PR once the review is complete.
```

Do not post it before the diff is collected — the change-size line comes from it. If
`jira_comment.sh` fails, say so and continue the review; the comment is a courtesy, the
review is the work.

### 2. Establish the stack

Before applying the checklist:

- What languages are in the diff? Read the extensions in `.review/context.txt`, do not assume.
- What is the build and test command? Check the manifest, CI config, or `CLAUDE.md`.
- Read 2-3 neighbouring files the PR did **not** touch. The existing code is the style guide.
- Read `CLAUDE.md`, `AGENTS.md`, and `CONTRIBUTING.md` if present. Project rules **override**
  generic best practice, and violating a documented rule is a FAIL under CR-27.

This step is what makes the review language-agnostic: the checklist asks universal questions
("are failures handled?") and this step supplies the local answer ("in Go that means the
returned error is checked, not an exception caught").

### 3. Evaluate the diff against the ticket, then work the checklist

**CR-01 comes first and carries the review.** For every expectation in
`.review/<KEY>-requirements.md`, find the code in `.review/full.patch` that satisfies it
and the test that proves it. Record each as **Met**, **Partial**, or **Missing** with the
`file:line`. Then go the other way: every behavioural change in the diff must trace back
to an expectation — anything that does not is either scope creep (CR-02) or a
misreading of the ticket (CR-01).

An expectation that is Missing or Partial is a **FAIL** on CR-01, and the failure reason
is written in the ticket's own words: *"the ticket asks for X (description, para 2); the
diff does nothing for X"* or *"comment by A on <date> says Y is out of scope, but the
diff changes Y"*. Severity: Missing criterion = MAJOR; the change solving a different
problem than the one asked = BLOCKER. The overall verdict is FAIL whenever an expectation
is not met — a clean checklist does not rescue a change that does not do what was asked.

Then read `references/review-checklist.md` and work the remaining 27 items in order. Do not skip to the
interesting ones — the boring items are where production incidents come from.

One verdict per item: `PASS`, `FAIL`, `WARN`, `N/A`, or `UNVERIFIED`.

Severity on every FAIL:
- `BLOCKER` — do not merge. Data loss, security hole, breaks production, breaks a contract,
  cannot be rolled back
- `MAJOR` — must fix before merge. Wrong behaviour in a real scenario, untested new logic,
  unhandled failure path, resource leak
- `MINOR` — fix now or follow up. Naming, duplication, missing comment

Use `ultrathink` on CR-04 (edge cases), CR-12 (compatibility), CR-14 (authorisation), and
CR-23 (rollback). Those four are where careful reasoning pays and skimming costs the most.

### 4. Write the report

Follow `references/report-template.md`. Write to `.review/<TICKET-KEY>-review.md`.

- Overall verdict is **FAIL if any BLOCKER or MAJOR exists**, otherwise PASS. An unmet
  ticket expectation is always at least MAJOR, so it always fails the review.
- The "Requirement traceability" table lists **every** expectation from
  `.review/<KEY>-requirements.md` — none skipped, each with Met / Partial / Missing and
  the evidence. This table is what the author and QA read first.
- Every FAIL states what is wrong, where (`file:line`), why it matters in production, and
  what to do instead. "Error handling is poor" is not acceptable. Write: "the `IOException`
  from `readConfig()` at `ConfigLoader.java:47` is swallowed by an empty catch, so a corrupt
  config starts the service on silent defaults — rethrow as a startup failure."
- Include a working suggested fix for every BLOCKER and MAJOR, in the repo's language and style.
- List every blind spot under "Not verified". Be honest.
- Do not pad. If 22 items pass cleanly, one line each.

### 5. Offer to post, then move the ticket on

Ask first, always, naming exactly where the comment will land and what the ticket move
will be: on PASS "move to <jira.qa_status> and assign <jira.default_qa>", on FAIL "move
via <jira.rework_targets> and assign <developer display name> (<source>)" — use the
configured names, never assume a workflow. One `AskUserQuestion`,
whose options spell out what each choice writes — include a "post only, do not move"
option and a "do nothing" option. On confirmation:

- **PR comment on the git host** (preferred — the author sees it in the PR; the script
  detects Bitbucket or GitHub from the remote, or `git.host`):
  `bash ${CLAUDE_SKILL_DIR}/scripts/post_pr_comment.sh <pr-id> <summary.md>`
  Write the summary to a file in `.review/` first: verdict line, counts, the BLOCKER and
  MAJOR findings with `file:line`, and the "Not verified" list. Not the whole report.
- **Jira comment**:
  `bash ${CLAUDE_SKILL_DIR}/scripts/jira_comment.sh <KEY> @.review/<KEY>-jira-summary.txt`
  (convert the Markdown summary to Jira wiki markup first: `##` → `h2.`, `**x**` → `*x*`,
  backticks → `{{x}}`).

- **QA handoff — only when the verdict is PASS and the ticket is currently in the
  review status:**
  `bash ${CLAUDE_SKILL_DIR}/scripts/jira_handoff.sh <KEY>`
  This walks the workflow to `jira.qa_status` from `.claude/jira-project.json`, hopping
  through `jira.qa_path` when the review status has no direct move, and assigns
  `jira.default_qa`. Both keys are required; the script names the missing one. Run the
  findings comment **before** the
  handoff so QA finds the review on the ticket. Show the script's output to the user
  verbatim — it names every hop and the final assignee.
- **Rework handback — only when the verdict is FAIL and the ticket is currently in the
  review status:**
  `bash ${CLAUDE_SKILL_DIR}/scripts/jira_handoff.sh <KEY> --mode rework --assignee <developer.name>`
  `<developer.name>` is the `developer.name` field of the ticket in the queue output —
  the author of the comment that put the branch/PR on the ticket, falling back to the
  assignee. The script tries `jira.rework_targets` in order — each entry is a transition
  name or a status, so a workflow that names the move differently per issue type is one
  list. Post the findings comment **first** so the developer finds the reasons on the
  ticket. Show the script's output verbatim.
  - `developer` is null in the queue output → do not guess; post the findings and tell
    the user the handback needs a name.

In both modes:

- The ticket is not in the review status (already past it, or reviewed by key from
  another column) → skip the move and say why. A key overrides the queue filter for
  *reviewing*, not for moving tickets someone else owns.
- The script fails (missing transition, permission) → report its error and stop; the
  findings are already posted, nothing needs undoing.
- Both scripts accept `--dry-run`; use it when unsure what the first hop will be.

Never approve or decline the PR, and never move a ticket anywhere other than the
configured QA status (PASS) or rework transition (FAIL) — anything else is the human
reviewer's call.

### 6. Record the run in the ledger — every run, every outcome

Every execution of this skill, on every machine, writes one record to the same ledger
page so the team can see the agent's throughput and turnaround. The page and its store
come from `tracking.artifact_url` / `tracking.collection` in the repo's
`.claude/jira-project.json` when set, else `${CLAUDE_SKILL_DIR}/tracking.json` — the
script's output line names the one to use; never substitute another URL. This step is not optional and needs no confirmation: it writes
to the review store, not to Jira or the PR.

1. Build the record — after step 5 has finished (or right after the blocked comment):
   ```bash
   python3 ${CLAUDE_SKILL_DIR}/scripts/record_run.py <KEY> --verdict <PASS|FAIL|BLOCKED> --outcome <qa_handoff|rework|posted|report_only|blocked> [--posted pr,jira] [--blocked-missing "branch name,PR link"]
   ```
   `--outcome` is what actually happened in step 5: `qa_handoff` / `rework` when the
   handoff script ran, `posted` when findings were posted but the ticket was not moved,
   `report_only` when the user chose not to post, `blocked` for step-1 blocks.
   `--posted` lists where the findings went. The script prints
   `doc_id=<id> file=<path> url=<ledger> collection=<name>`.
2. Write it with the Artifact tool — `action: "write_db"`, `db_op: "set"`, and `url`,
   `collection`, `doc_id`, `file_path` all taken from that output line.
3. Tell the user the run is recorded, with the ledger URL. If the write fails (no access
   to the artifact, quota), say so and keep `.review/<KEY>-run.json` — it can be written
   later with the same call.

The record carries the ticket (key, type, priority, summary), reviewer, developer, PR,
start/finish/duration, verdict, finding counts, diff size, a size-based complexity score
and the outcome. Iteration numbers and time-to-PASS are computed by the page from all
runs on the same key.

## Failure modes

- **Rubber-stamping.** All PASS on a 900-line diff means you did not review it.
- **Nit-flooding.** Twenty MINORs and no MAJOR analysis is a bad review. Lead with what matters.
- **Reviewing the author.** Comment on code, never the person. No "you forgot".
- **Style as failure.** No project rule and a silent linter means WARN at most.
- **Reviewing from a guess.** A PR found by searching is a hint, not the ticket's word.
  If the ticket does not name the branch and link the PR, the answer is the blocked
  comment, not a best-effort review of whatever matched the key.
- **Missing the missing.** The costliest defects are absent from the diff — the test not
  written, the caller not updated, the migration not made reversible. Scan for absence
  deliberately.
