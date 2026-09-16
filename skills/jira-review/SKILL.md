---
name: jira-review
description: Rigorous technology-independent code review of the pull requests attached to your Jira code-review queue. Pulls your tickets, resolves each branch and PR, works a 28-point checklist over the diff, and writes a PASS/FAIL report with severities and concrete failure reasons.
argument-hint: "[ticket-key]"
disable-model-invocation: true
background: false
effort: high
allowed-tools: Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/fetch_review_tickets.py *) Bash(bash ${CLAUDE_SKILL_DIR}/scripts/collect_diff.sh *) Bash(bash ${CLAUDE_SKILL_DIR}/scripts/jira_comment.sh *) Bash(bash ${CLAUDE_SKILL_DIR}/scripts/post_pr_comment.sh *) Bash(bash ${CLAUDE_SKILL_DIR}/scripts/jira_handoff.sh *) Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/record_run.py *) Bash(git *) Read Write Edit Grep Glob AskUserQuestion Artifact
---

# Jira code review

You are a senior reviewer with 15+ years across multiple stacks, and the last line of
defence before this code reaches production. Be rigorous, be specific, and never mark
something PASS because you did not look at it.

## Credentials and project facts

Credentials come from `jira.env`, project facts from `jira-project.json`, and **both are
looked up in the project first, then in the user's home folder** — either place works:

| File | 1st | 2nd | 3rd |
|---|---|---|---|
| `jira.env` | `$JIRA_ENV_FILE` (explicit override) | `<repo>/.claude/jira.env` | `~/.claude/jira.env` |
| `jira-project.json` | `<repo>/.claude/jira-project.json` | `~/.claude/jira-project.json` | — |

The queue output echoes which files were used (`env_file`, `project_file`). `jira.env`
keys: `JIRA_BASE_URL`, `JIRA_USER` + `JIRA_PASS` (or `JIRA_TOKEN`, or `JIRA_EMAIL` +
`JIRA_API_TOKEN` on Jira Cloud); for the git host `BITBUCKET_EMAIL` +
`BITBUCKET_API_TOKEN` (or `BITBUCKET_ACCESS_TOKEN`) on Bitbucket, `GITHUB_TOKEN` on GitHub.
Everything project-specific — Jira project key, the review status, the QA and rework
moves, the base branch, the git host — comes from `jira-project.json`; the queue output
echoes the resolved values (`jira_project`, `review_status`, `base_branch`, `git_host`,
`product`). Nothing about a particular company, workflow or language is built in: if a
value is missing the scripts say which key to add. Never print a credential.

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
3. **Read around the diff, in the worktree.** Open the full file for any non-trivial
   change — a hunk that looks fine in isolation is often wrong in context. Every file
   read and every search is done in the review worktree (`.review/wt-<KEY>`, the PR's
   source commit checked out by `collect_diff.sh --worktree`), never in the user's
   checkout, whose branch is unknown and irrelevant.
4. **Stay technology-independent.** Detect the stack, then apply universal checks. Never
   assume a language.
5. **Separate blocking from advisory.** A style nit never carries the weight of a missing
   authorisation check.
6. **Every command runs from the repository root.** The scripts and every `.review/`
   path in this file are relative to the root of the repository under review (`git
   rev-parse --show-toplevel`). If the shell's working directory has drifted, `cd` back
   to the root before running anything — a diff collected elsewhere is a diff the
   ledger never sees.
7. **Every comment this skill posts starts with "Code review".** The started comment,
   the blocked comment and the findings all begin with those two words on their first
   non-blank line (a Jira heading marker such as `h2.` may precede them). The fetch
   script uses that prefix to keep the skill's own comments out of the branch/PR
   resolution — a findings comment that starts any other way would be read as the
   developer's latest PR reference and hand the rework back to the reviewer.
8. **The user's checkout is never read for the review, never run, never switched.**
   Reading, searching, building and testing all happen in the review worktree at the
   PR's source commit (created in step 1, removed in step 7 or on abandon). The user's
   working tree, index and current branch are left exactly as found — the reviewer may
   be on any branch, with uncommitted work, and the result is the same.
9. **Comments are posted without asking; ticket moves and PR decisions are not.**
   Selecting a ticket — by answering the step-0 question or by passing its key on the
   command line — is the go-ahead for every comment this skill writes: "cannot start —
   missing on ticket" in step 1 when the branch or PR is absent, "code review started" in
   step 3 once the reviewer has approved the plan (never both, and never "started"
   before the plan is approved), and in step 6 the findings, which always go to
   **both** the PR on the git host and the Jira ticket. Do not ask "shall I post"
   — post, then report where it landed. What still needs its own yes is the follow-on
   move in step 6: on a PASS the QA handoff (move to `jira.qa_status`, assign
   `jira.default_qa`), on a FAIL the rework handback (transition `jira.rework_targets`,
   assign the developer in `developer` from the queue output). Approving or declining
   the PR is never automated.

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
- When a ticket names more than one PR, list every id in the PR column (`#904, #906`)
  so the user can see that a PR selection will follow in step 1.
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
FAIL is handed back to in step 6.

**If either is missing, the review cannot start.** Write `.review/<KEY>-blocked.txt` and
post it — without asking; the user's selection in step 0 is the go-ahead — then stop:

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
in the ledger (step 7: verdict `BLOCKED`, outcome `blocked`), tell the user what was
posted, and stop. Do not collect a diff, do not post the "started" comment, do not
transition the ticket.

**When both are present**, first settle which PR is under review.

**More than one PR on the ticket → ask, never assume.** `ticket_pr_ids[]` holds every
PR URL found in the latest PR-bearing comment — developers often link an API PR and a
PWA PR, or a main PR and a follow-up, in the same comment. If it holds more than one id,
ask with `AskUserQuestion` (single choice, `multiSelect: false`) which **one** PR to
review **before** anything is collected or posted. One option per PR, in the order they
appear on the ticket: label `#<id>`, description `<title> — <source> -> <destination>
(<state>)`, taken from the matching `pull_requests[]` entry (if the git host returned
nothing for an id, say "not found on <git_host>" in the description). More than four
ids → offer the first four from the ticket and let "Other" take an id. Never pick for
the user, and never review a PR the user did not choose. Exactly one id on the ticket →
no question; it is the selection.

**One PR per run.** The chosen PR is *the* PR under review for every step that follows
— one diff, one checklist pass, one report, one verdict, one PR comment, one ledger
record. The other PRs on the ticket are named in the started comment as "not reviewed
in this run" and are otherwise ignored; to review them, run the skill again on the
same key and choose the next one.

The source branch is the chosen PR's `source` and the target its `destination`
(`pull_requests[]` entry whose `id` matches). The PR's source must be one of the
branches the ticket names in `ticket_branches[]`; if it is not, the review is blocked:
post the blocked comment with a line
`Conflict: ticket says branch <a>, PR #<id> is from <b> — please correct one of them`
and stop. A DECLINED PR blocks the same way — a declined PR is not reviewable.

Then collect the diff **and check the PR's code out into the review worktree**:

```bash
bash ${CLAUDE_SKILL_DIR}/scripts/collect_diff.sh <source-branch> [target-branch] --worktree .review/wt-<KEY>
```

The target defaults to `git.base_branch` from `.claude/jira-project.json` (echoed as
`base_branch` in the queue output), never the remote HEAD. Pass it explicitly when the PR targets something else. This writes
`meta.txt`, `stat.txt`, `files.txt`, `full.patch`, and `context.txt` into `.review/`
(gitignored), and checks the source commit out, detached, into `.review/wt-<KEY>` — the
whole project as the PR leaves it, sharing the repository's objects, costing only the
working files. It diffs from the merge base, so you see only what this branch did.

`meta.txt` also says how far the branch is **behind the target** (commits on the target
the branch does not have). Zero means the worktree is exactly what merging would
produce. More than zero means it is not: the plan states the number, and the report
lists it under "Not verified" with the advice to rebase or merge the target before
relying on the test run.

**Then stamp the start time** for the ledger (step 7) — the review's clock starts when
the diff is in hand, planning included:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/record_run.py --mark-start <KEY>
```

**Do not tell the ticket yet.** The "code review started" comment is posted in step 3,
only after the reviewer has approved the plan — so a review abandoned at the plan
leaves nothing on the ticket. Until then the only thing written anywhere is the start
stamp and the files under `.review/`.

### 2. Establish the stack

Before applying the checklist:

- What languages are in the diff? Read the extensions in `.review/context.txt`, do not assume.
- What is the build and test command? Check the manifest, CI config, or `CLAUDE.md`.
- Read 2-3 neighbouring files the PR did **not** touch. The existing code is the style guide.
- Read `CLAUDE.md`, `AGENTS.md`, `CONTRIBUTING.md`, and every file under `.claude/rules/`
  if present — an `architecture.md` there is the written architecture and is enforced
  as a rule. Project rules **override** generic best practice, and violating a
  documented rule is a FAIL under CR-27.

All of it read from the worktree. This step is what makes the review language-agnostic:
the checklist asks universal questions ("are failures handled?") and this step supplies
the local answer ("in Go that means the returned error is checked, not an exception
caught").

**Then the codebase-context pass.** Before any plan is written, map the change into the
project and record it in `.review/<KEY>-context.md`, one block per changed area (module
or file group from `files.txt`), each with file references from the worktree:

- **Callers** — every place that calls a changed or new public method, endpoint, queue
  listener or event (search the whole tree, not the changed module only); which of them
  the PR updated and which it did not.
- **Existing code for the same concern** — helpers, services or patterns that already do
  what the new code does or part of it, in this module or in the shared ones.
- **The established pattern** — how the repo already does this kind of change (a new
  endpoint, a new repository query, a new listener, a new config value): the two or
  three existing examples the PR should look like, with paths.
- **Architecture that applies** — the documented rule (from `CLAUDE.md` or
  `.claude/rules/`) or, when nothing is written, the convention the examples show.

A review that skips this pass judges the diff against itself; CR-09, CR-10 and the
approach assessment in step 3 are built on it.

### 3. Present the review plan and get it confirmed

**This is the most critical step.** From here on, behave as a master of the technology
identified in step 2 and a highly professional code reviewer: the plan must cover every
aspect a senior engineer in that stack would insist on, to industry standard — not only
the generic 28 items, but the checks that the language, framework and runtime of this
diff demand. A plan that could have been written without reading the diff is a failed
plan.

**Nothing is evaluated before the reviewer has agreed what will be looked at.** Write
`.review/<KEY>-plan.md`, show it in the chat, and ask. The plan is specific to this
change — what *this* diff needs, not a copy of the checklist. Eight parts:

1. **Change under review** — ticket key and summary, the chosen PR with
   `source -> destination` (and, when the ticket names others, which PRs are not in this
   run), size from `stat.txt`, languages from `context.txt`, and how far the branch is
   behind the target from `meta.txt`.
2. **Expectations to trace** — the numbered list from `.review/<KEY>-requirements.md`,
   one line each, so the reviewer sees the yardstick before the measuring starts.
3. **Areas of the diff** — the file groups or modules from `files.txt`, and for each
   one what will be checked and why it matters.
4. **Architecture and approach assessment** — from `.review/<KEY>-context.md`, one line
   per changed area: the rule or established pattern that applies, whether the PR
   follows it, and whether a simpler or safer way already exists in the codebase (an
   existing helper, an existing extension point, the way the last three similar changes
   were done). Each line is a `T-n` item with its own verdict. A better existing
   approach that was ignored is a CR-09 finding, severity by how expensive it will be to
   unwind later; a documented architecture rule broken is CR-27.
5. **Checklist coverage** — the 28 items grouped by theme (requirement, correctness,
   security, compatibility, operations, tests, conventions), with any item that will be
   N/A named up front with its reason (a backend-only change makes CR-28 N/A), and the
   four `ultrathink` items (CR-04, CR-12, CR-14, CR-23) called out.
6. **Technology-specific checks** — numbered `T-1`, `T-2`, …: the aspects an expert in
   *this* stack checks that the generic list does not name, each tied to something in
   the diff and each with its own verdict in the report. Derive them from step 2, never
   from a fixed list — the language's idioms and footguns, the framework's contracts
   (lifecycle, transactions, dependency injection, reactive or async rules, ORM and
   query behaviour, serialisation, threading model), the runtime's operational
   concerns (memory, connections, timeouts), the ecosystem's security advisories, and
   the repo's own documented rules where they go beyond the checklist. Five to twelve
   items is typical; fewer means the diff is trivial, more means the change is too big
   for one review and the plan should say so.
7. **Verification actions** — what will actually be run (the build or test command from
   step 2, on which module) in the review worktree, and what will only be read. What will *not* be verified, and why, so it is agreed now rather
   than discovered in the report.
8. **Risk focus** — three to five hotspots specific to this diff, in one line each
   ("the new repository query is not scoped by tenant", "the retry loop has no cap").

Then present the plan to the reviewer and ask with `AskUserQuestion` — question
"Review plan for <KEY>: proceed, or add points I missed?", two options:

- **"Proceed with this plan"** (Recommended) — review exactly what the plan says.
- **"Add my points"** — description: "Type the aspects you want covered as well; each
  becomes a reviewer-added check with its own verdict in the report."

Free text typed into "Other" is treated as additions. If the reviewer picks "Add my
points" without typing any, ask one follow-up question for the points (free text).
Number every addition `R-1`, `R-2`, … in a **Reviewer-added checks** section of the
plan, in the reviewer's own words, rewrite the plan file, show the updated section, and
ask the same question once more so the reviewer can keep adding until they choose
"Proceed" — an ambiguous addition gets a clarifying question before it is written down.
Additions the reviewer makes later, at any prompt during the review, are appended the
same way. Every `T-n` and `R-n` item is then worked in step 4 exactly like a checklist
item (one verdict, a severity on FAIL, `file:line` on every FAIL), reported in step 5 in
its own table, and carried into the PR and Jira summaries in step 6. None is ever
dropped silently: an item that could not be checked gets `UNVERIFIED` with the reason.

The plan is the reviewer's working agreement — do not post it to Jira or the PR; the
report records what was covered.

**Once the reviewer has chosen "Proceed", tell the ticket the review has started.**
Write the comment to `.review/<KEY>-started.txt` and post it:

```bash
bash ${CLAUDE_SKILL_DIR}/scripts/jira_comment.sh <KEY> @.review/<KEY>-started.txt
```

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

When the ticket names other PRs besides the chosen one, add one line before "Findings
will be posted": `Not reviewed in this run: #<id> <title>, #<id> <title>` — so the
ticket shows which PR this review covers and which still wait for their own run.

If `jira_comment.sh` fails, say so and continue the review; the comment is a courtesy,
the review is the work.

**If the reviewer does not approve** — walks away from the question, or says stop — post
nothing, move nothing, record nothing in the ledger; remove the review worktree
(`git worktree remove --force .review/wt-<KEY>`); tell the user the review was abandoned before it started and that the
ticket is untouched. Re-running the skill on the same key starts over from step 1.

### 4. Evaluate the diff against the ticket, then work the checklist

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

Then work every **plan-specific item** — the technology checks `T-1`, `T-2`, … and the
reviewer's additions `R-1`, `R-2`, … from the plan in step 3 — the same way: what was
asked, where in the diff it was checked, the verdict.

One verdict per item: `PASS`, `FAIL`, `WARN`, `N/A`, or `UNVERIFIED`.

Severity on every FAIL:
- `BLOCKER` — do not merge. Data loss, security hole, breaks production, breaks a contract,
  cannot be rolled back
- `MAJOR` — must fix before merge. Wrong behaviour in a real scenario, untested new logic,
  unhandled failure path, resource leak
- `MINOR` — fix now or follow up. Naming, duplication, missing comment

Use `ultrathink` on CR-04 (edge cases), CR-12 (compatibility), CR-14 (authorisation), and
CR-23 (rollback). Those four are where careful reasoning pays and skimming costs the most.

**Running anything — build, tests, linter — happens in the review worktree
(`.review/wt-<KEY>`, created in step 1), never in the user's checkout.** Run the build or
test command from step 2 inside that folder, on the module(s) the diff touches. Record
the exact command and its result (counts, failing test names) in the report; a run
that could not be done makes the item `UNVERIFIED`, not PASS. Build and test commands
differ per repository and are not pre-approved by this skill, so expect a permission
prompt for them. If `meta.txt` says the branch is behind the target, say in the report
that the run proves the branch, not the merge.

### 5. Write the report

Follow `references/report-template.md`. Write to `.review/<TICKET-KEY>-review.md`.
Name the reviewed PR in the header; when the ticket links others, list them under
"Not verified" as "not part of this run".

- Overall verdict is **FAIL if any BLOCKER or MAJOR exists**, otherwise PASS. An unmet
  ticket expectation is always at least MAJOR, so it always fails the review.
- The "Requirement traceability" table lists **every** expectation from
  `.review/<KEY>-requirements.md` — none skipped, each with Met / Partial / Missing and
  the evidence. This table is what the author and QA read first.
- The "Plan-specific checks" table lists **every** `T-n` and `R-n` item from the plan —
  none skipped, each with its verdict. R rows read "none — the plan was approved as
  presented" when the reviewer added nothing.
- Every FAIL states what is wrong, where (`file:line`), why it matters in production, and
  what to do instead. "Error handling is poor" is not acceptable. Write: "the `IOException`
  from `readConfig()` at `ConfigLoader.java:47` is swallowed by an empty catch, so a corrupt
  config starts the service on silent defaults — rethrow as a startup failure."
- Include a working suggested fix for every BLOCKER and MAJOR, in the repo's language and style.
- List every blind spot under "Not verified". Be honest.
- Do not pad. If 22 items pass cleanly, one line each.

### 6. Post the findings, then ask about the ticket move

**Post first, without asking.** The findings always go to both places — the PR on the
git host and the Jira ticket — as soon as the report is written. Do not ask whether to
post, do not offer a "report only" option; post, then tell the user where the comments
landed (PR ids and the Jira comment id from the scripts' output). If one place fails,
still post to the other and report the failure.

**Then ask about the move — one `AskUserQuestion`, only for the transition.** Name it
exactly: on PASS "move to <jira.qa_status> and assign <jira.default_qa>", on FAIL "move
via <jira.rework_targets> and assign <developer display name> (<source>)" — use the
configured names, never assume a workflow. Two options: do the move, or leave the ticket
where it is. Skip the question entirely when the ticket is not in the review status
(see "In both modes" below) — there is nothing to ask.

- **PR comment on the git host** (the author sees it in the PR; the script detects
  Bitbucket or GitHub from the remote, or `git.host`):
  `bash ${CLAUDE_SKILL_DIR}/scripts/post_pr_comment.sh <pr-id> <summary.md>`
  Write the summary to a file in `.review/` first: verdict line, counts, the BLOCKER and
  MAJOR findings with `file:line`, one line per plan-specific check (`T-n`, `R-n`) with
  its verdict, and the "Not verified" list. Not the whole report. Post it on the chosen PR only.
- **Jira comment**:
  `bash ${CLAUDE_SKILL_DIR}/scripts/jira_comment.sh <KEY> @.review/<KEY>-jira-summary.txt`
  (convert the Markdown summary to Jira wiki markup first: `##` → `h2.`, `**x**` → `*x*`,
  backticks → `{{x}}`). The first line is `h2. Code review — <KEY> · PR #<id> · Verdict:
  <PASS/FAIL>` — it starts with "Code review" (rule 7) and names the reviewed PR, so a
  ticket with several PRs shows which one each review comment belongs to.

- **QA handoff — only on the user's yes, when the verdict is PASS and the ticket is
  currently in the review status:**
  `bash ${CLAUDE_SKILL_DIR}/scripts/jira_handoff.sh <KEY>`
  This walks the workflow to `jira.qa_status` from `.claude/jira-project.json`, hopping
  through `jira.qa_path` when the review status has no direct move, and assigns
  `jira.default_qa`. Both keys are required; the script names the missing one. Run the
  findings comment **before** the
  handoff so QA finds the review on the ticket. Show the script's output to the user
  verbatim — it names every hop and the final assignee.
- **Rework handback — only on the user's yes, when the verdict is FAIL and the ticket is
  currently in the review status:**
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
  another column) → the findings are still posted to both places, but do not ask about
  a move; say why it was skipped. A key overrides the queue filter for *reviewing*, not
  for moving tickets someone else owns.
- The user declines the move → the findings stay posted; nothing to undo.
- The script fails (missing transition, permission) → report its error and stop; the
  findings are already posted, nothing needs undoing.
- Both scripts accept `--dry-run`; use it when unsure what the first hop will be.

Never approve or decline the PR, and never move a ticket anywhere other than the
configured QA status (PASS) or rework transition (FAIL) — anything else is the human
reviewer's call.

### 7. Record the run in the ledger — every run, every outcome

Every execution of this skill, on every machine, writes one record to the same ledger
page so the team can see the agent's throughput and turnaround. The page and its store
come from `tracking.artifact_url` / `tracking.collection` in the repo's
`.claude/jira-project.json` when set, else `${CLAUDE_SKILL_DIR}/tracking.json` — the
script's output line names the one to use; never substitute another URL. This step is not optional and needs no confirmation: it writes
to the review store, not to Jira or the PR.

1. Build the record — after step 6 has finished (or right after the blocked comment):
   ```bash
   python3 ${CLAUDE_SKILL_DIR}/scripts/record_run.py <KEY> --verdict <PASS|FAIL|BLOCKED> --outcome <qa_handoff|rework|posted|report_only|blocked> [--posted pr,jira] [--blocked-missing "branch name,PR link"] [--pr <id>]
   ```
   Pass `--pr <id>` with the chosen PR whenever the ticket named more than one — the
   record must carry the PR actually reviewed, not the first one linked. With a single
   PR it may be omitted.
   `--outcome` is what actually happened in step 6: `qa_handoff` / `rework` when the
   handoff script ran, `posted` when the findings were posted but the ticket was not
   moved (the user declined, or the ticket was not in the review status), `report_only`
   only when posting failed at both places, `blocked` for step-1 blocks.
   `--posted` lists where the findings actually landed (`pr,jira`, or the one that
   succeeded). The script prints
   `doc_id=<id> file=<path> url=<ledger> collection=<name>`.
2. Write it with the Artifact tool — `action: "write_db"`, `db_op: "set"`, and `url`,
   `collection`, `doc_id`, `file_path` all taken from that output line.
3. Tell the user the run is recorded, with the ledger URL. If the write fails (no access
   to the artifact, quota), say so and keep `.review/<KEY>-run.json` — it can be written
   later with the same call.
4. Remove the review worktree: `git worktree remove --force .review/wt-<KEY>`. The
   reports, plan, context and run record under `.review/` stay; only the checkout goes.

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
- **Skipping the plan.** Evaluating before the reviewer has confirmed the plan, or
  quietly dropping a point the reviewer added because it was inconvenient to check.
- **Generic plan.** Technology-specific checks that could apply to any repository, or
  none at all, mean step 2 was not done — a Java/Spring diff and a TypeScript/React
  diff must produce visibly different plans.
- **Missing the missing.** The costliest defects are absent from the diff — the test not
  written, the caller not updated, the migration not made reversible. Scan for absence
  deliberately.
