---
name: jira-review
description: Rigorous technology-independent code review of the pull requests attached to your Jira code-review queue. Pulls your tickets, resolves each branch and PR, works a 28-point checklist over the diff, and writes a PASS/FAIL report with severities and concrete failure reasons.
argument-hint: "[ticket-key]"
disable-model-invocation: true
background: false
effort: high
allowed-tools: Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/fetch_review_tickets.py *) Bash(bash ${CLAUDE_SKILL_DIR}/scripts/collect_diff.sh *) Bash(bash ${CLAUDE_SKILL_DIR}/scripts/jira_comment.sh *) Bash(bash ${CLAUDE_SKILL_DIR}/scripts/post_pr_comment.sh *) Bash(bash ${CLAUDE_SKILL_DIR}/scripts/jira_handoff.sh *) Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/record_run.py *) Bash(git *) Read Write Edit Grep Glob AskUserQuestion
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

**A ticket can link any number of pull requests** — one PR in one repository, one PR in
each of several repositories (a web app split across repos), or several PRs in the same
repository (backend and frontend folders of one repo). Each linked PR is reviewed as its
own unit. The current checkout is found automatically; any other repository a ticket's
PRs live in needs its local clone declared once in `jira-project.json`:

```json
"repos": { "girnarsoftware/lms-pwa-ui": "../lms-pwa-ui" }
```

The path is absolute or relative to this repository's root. All repositories must be on
the same git host.

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
   read and every search is done in the reviewed PR's worktree
   (`.review/<KEY>/<unit>/wt`, checked out by `collect_diff.sh --worktree`), never in the
   user's checkout, whose branch is unknown and irrelevant.
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
   Reading, searching, building and testing all happen in each reviewed PR's worktree
   (created in step 1, removed in step 7 or on abandon). The user's
   working tree, index and current branch are left exactly as found — the reviewer may
   be on any branch, with uncommitted work, and the result is the same. Every reviewed PR
   gets its own worktree, in its own repository's clone, each removed the same way.
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
   assign the person who linked the failing PR). Approving or declining a PR is never
   automated.

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
- List every PR in `ticket_prs[]` as `<repo> #<id> (<state>)` — a PR number is only
  unique inside its repository — and mark a ticket that will block (anything in
  `missing_on_ticket` or `conflicts`) so the user can see it before choosing.
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

**Then check the ticket names the code.** Two things are required on the ticket itself —
at least one branch name and at least one PR link — and both come from the queue output:

| Required | Where it comes from | Queue field |
|---|---|---|
| Branch name | a `Branch:` line in a comment or the description; up to three words may precede it (`PWA UI branch:`, `backend branch -`) | `ticket_branches[]` |
| PR link | the PR's URL on the git host (`.../pull-requests/<id>` on Bitbucket, `.../pull/<id>` on GitHub) | `ticket_prs[]` |

**Every PR linked on the ticket is one unit of review.** The fetch script collects every
PR link from the developers' comments and the description, and asks the git host for each
one. An entry in `ticket_prs[]` carries: `unit` (`<repo>-pr<id>`, the name of its folder
under `.review/<KEY>/`), `repo`, `slug`, `id`, `title`, `state`, `source`, `destination`,
`merge_commit`, `reviewable` (true for OPEN and MERGED), `branch_on_ticket` (its source
branch is named on the ticket), `clone` and `clone_exists` (where its repository is checked
out on this machine), and `linked_by` — the person who linked it, to whom a failed review
of that PR goes back. This one model covers a single PR, one PR per repository across
several repositories, and several PRs in the same repository.

**No "latest comment wins".** Which PRs still count is decided by each PR's state on the
git host, not by comment order: two developers linking two PRs in separate comments both
count, and a re-raised PR retires the old one because the old one is DECLINED or
SUPERSEDED (`reviewable: false`). The skill's own comments ("Code review started",
"Code review cannot start", the findings) quote PRs back and are ignored.

**The review cannot start** when `missing_on_ticket` or `conflicts` is non-empty:
- `missing_on_ticket`: no branch name, no PR link, or links whose PRs are all declined or
  superseded;
- `conflicts`: a reviewable PR whose source branch the ticket does not name.

Write `.review/<KEY>-blocked.txt` and post it — without asking; the user's selection in
step 0 is the go-ahead — then stop:

```bash
bash ${CLAUDE_SKILL_DIR}/scripts/jira_comment.sh <KEY> @.review/<KEY>-blocked.txt
python3 ${CLAUDE_SKILL_DIR}/scripts/record_run.py --mark-start <KEY>
```

```
Code review cannot start — required information is missing on the ticket.

Missing:
  - Branch name      (add a comment: "Branch: <branch-name>")
  - PR link          (add a comment: "PR: <the pull request's URL on the git host>")
  - An open or merged PR (every PR linked is declined or superseded)

Conflict:
  - PR #<id> (<owner/repo>) is from branch <b>, which the ticket does not name —
    add "Branch: <b>" or link the right PR

<Only when the fallbacks found something:>
Possibly related, found by searching — please confirm by adding it to the ticket:
  - <repo> PR #<id> <title> [<source> -> <destination>] (<state>)
  - branch <name>

Reviewer: <reviewer.display_name>. The ticket stays in Code Review; re-run the review
once the above is on the ticket.
```

List only what applies. The "possibly related" block comes from `pull_requests[]` entries
with `linked: false` (git-host search by branch name and ticket key) and `branches[]`
(git) — hints, never the source of a review. Then record the blocked run in the ledger
(step 7: verdict `BLOCKED`, outcome `blocked`), tell the user what was posted, and stop.
Do not collect a diff, do not post the "started" comment, do not transition the ticket.

**Problems that are the reviewer's, not the ticket's.** `local_problems` — a reviewable
PR whose repository has no local clone, or no PR on the ticket readable at all (usually
git-host credentials) — are never posted to the ticket and nothing is recorded: tell the
reviewer the exact fix (clone the repository and add its `"owner/repo": "<path>"` under
`repos`, or fix the credentials) and stop. `unreadable_prs` — one link among several that
the git host cannot return — does not block: tell the reviewer, leave it out of the run,
and name it in the started comment as "could not be read". `branches_without_pr` — a
branch named with no PR from it, often a test or merge branch — is only noted in the plan.

**Choose the PRs for this run.** The candidates are the entries with `reviewable: true`.
- Exactly one → no question; it is the review set.
- More than one → one `AskUserQuestion`, `multiSelect: true`, question "Which PRs to
  review in this run?": a first option **"All <N> PRs (Recommended)"**, then one option per
  PR, label `<repo> #<id>`, description `<title> — <source> -> <destination> (<state>)`.
  More than three PRs → the first three individually and "Other" takes the rest as
  `<repo> #<id>`. "All" (alone or with others ticked) means every candidate. Never pick for
  the reviewer.
- The ticked PRs are the **review set** for every step that follows. Candidates left out
  are named in the started comment as "not reviewed in this run".

**Collect each PR into its own folder and worktree**, in its own repository's clone:

```bash
# OPEN PR
bash ${CLAUDE_SKILL_DIR}/scripts/collect_diff.sh <source> <destination> \
     --repo <clone> --out .review/<KEY>/<unit> --worktree .review/<KEY>/<unit>/wt

# MERGED PR — always from its merge commit: the branch is usually deleted after merge,
# and a merged branch diffed against its target is empty
bash ${CLAUDE_SKILL_DIR}/scripts/collect_diff.sh <merge_commit> <merge_commit>^1 \
     --repo <clone> --out .review/<KEY>/<unit> --worktree .review/<KEY>/<unit>/wt
```

Each folder gets `meta.txt`, `stat.txt`, `files.txt`, `full.patch` and `context.txt`,
and `wt/` holds that PR's code checked out, detached — the whole repository as the PR
leaves it, sharing the clone's objects and costing only the working files. Two PRs of the
same repository get two worktrees at two commits. Wherever the steps below say
`full.patch`, `files.txt`, `stat.txt`, `meta.txt`, `context.txt` or "the worktree", they
mean the folder of the PR being looked at. Everything stays under **this** repository's
`.review/`, whichever repository the PR belongs to.

For an OPEN PR, `meta.txt` also says how far its branch is **behind its target**. Zero
means the worktree is exactly what merging would produce; more than zero means it is
not — the plan states the number, and the report lists it under "Not verified" with the
advice to rebase or merge the target before relying on the test run. For a MERGED PR the
worktree is the merged state itself, so the count does not apply; say "merged" instead.

**Then stamp the start time** for the ledger (step 7) — once for the run; the review's
clock starts when the diffs are in hand, planning included:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/record_run.py --mark-start <KEY>
```

**Do not tell the ticket yet.** The "code review started" comment is posted in step 3,
only after the reviewer has approved the plan — so a review abandoned at the plan
leaves nothing on the ticket. Until then the only thing written anywhere is the start
stamp and the files under `.review/`.

### 2. Establish the stack

Before applying the checklist:

- What languages are in each diff? Read the extensions in each PR's `context.txt`, do not assume.
- What is the build and test command? Check the manifest, CI config, or `CLAUDE.md`.
- Read 2-3 neighbouring files the PR did **not** touch. The existing code is the style guide.
- Read `CLAUDE.md`, `AGENTS.md`, `CONTRIBUTING.md`, and every file under `.claude/rules/`
  if present — an `architecture.md` there is the written architecture and is enforced
  as a rule. Project rules **override** generic best practice, and violating a
  documented rule is a FAIL under CR-27.

All of it read from each PR's worktree — a PR in another repository follows that
repository's own manifest, `CLAUDE.md` and rules. This step is what makes the review language-agnostic:
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

**With two or more PRs in the review set, map what connects them.** The context file ends
with a block **between PRs**, whichever repositories they are in: every interface one PR
changes and another consumes or provides — endpoint path and method, request and response
fields with types and nullability, enum values, error codes and statuses, queue or event
message shapes, configuration keys and feature flags, constants or validation duplicated
on both sides — with where it is defined and where it is used, file and line in each PR's
worktree. When one PR's repository consumes another's as a library or package, record the
version, tag or commit the consumer's manifest or lock file points at.

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
change — what *these* diffs need, not a copy of the checklist. Eight parts, and a ninth
when the review set has two or more PRs:

1. **Change under review** — ticket key and summary, then one row per PR in the review
   set: repository, `#id`, state, `source -> destination`, size from its `stat.txt`,
   languages from its `context.txt`, and how far its branch is behind its target (or
   "merged"). Below the table: PRs not reviewed in this run, PRs that could not be read,
   and branches named without a PR.
2. **Expectations to trace** — the numbered list from `.review/<KEY>-requirements.md`,
   one line each, so the reviewer sees the yardstick before the measuring starts.
3. **Areas of the diff** — the file groups or modules from each PR's `files.txt`,
   grouped by PR, and for each one what will be checked and why it matters.
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
   step 2, on which module) in which PR's worktree, and what will only be read. What will *not* be verified, and why, so it is agreed now rather
   than discovered in the report.
8. **Risk focus** — three to five hotspots specific to this diff, in one line each
   ("the new repository query is not scoped by tenant", "the retry loop has no cap").
9. **Checks between PRs** (two or more PRs only) — numbered `X-1`, `X-2`, …, derived from
   the between-PRs block of the context file: every field one side sends and the other
   reads matches in name, type, nullability and allowed values; errors and statuses one
   side can return are handled by the other; the order the PRs must be deployed or merged
   in, and whether each old/new combination works while they roll out; flags and
   configuration keys agree; duplicated constants and validation still say the same
   thing; and when one repository consumes another as a library, that the consumer points
   at the new version, tag or commit — and whether the tests can run against the combined
   change (link the library's worktree into the consumer's) or must be marked
   `UNVERIFIED` with that reason.

**Present the plan in the chat as a document, not as a summary.** The reviewer decides
from what is on screen, so the chat message carries the whole plan, laid out for a
person who has not read the code and does not know the checklist ids. Rules:

- Use the parts above as headings, in order, with the same numbering.
- Every list of checks is a **table**, one row per check, with four columns: **ID**,
  **What will be checked** (a full sentence in plain language), **Where** (file and
  line or file group from the diff), **Why it matters** (the production consequence if
  it is wrong). This applies to the checklist coverage (part 5 — group rows by theme
  and give every one of the 28 items its row, with N/A rows saying why), the
  architecture and approach items (part 4), every technology-specific check (part 6),
  and every check between PRs (part 9, with **Where** naming both sides). A check named only by its id or a two-word label ("Mockito spy idioms",
  "CSV ragged-row contract") is not presented — spell out what the reviewer would see
  if it failed.
- Expectations (part 2) are a table: **#**, **Expectation** (the ticket's words),
  **Source** (description, or comment author and date).
- Areas of the diff (part 3) are a table: **Area**, **Files**, **What will be checked**,
  **Why it matters**.
- Verification actions (part 7) are a table: **Action**, **Command or method**,
  **Where it runs**, **What it proves** — followed by a short "Will not be verified"
  list with the reason for each.
- Risk focus (part 8) is a numbered list; each item is two or three plain sentences:
  what the risk is, where it sits, what happens in production if it is real.
- No abbreviations the ticket does not use; expand every acronym the first time; no
  code identifiers except file names and the exact method or field under check.
- The plan file `.review/<KEY>-plan.md` has the same layout, so the report can point to
  it and the reviewer can reread it.

Then ask with `AskUserQuestion` — question "Review plan for <KEY>: proceed, or add
points I missed?", two options:

- **"Proceed with this plan"** (Recommended) — description: "Review exactly what the
  plan above says."
- **"Add my points"** — description: "Press Submit, then type your points in the chat
  box below as your next message — or choose Other here and type them directly. They
  are added to this plan as numbered checks and the plan is shown again for approval."

**How the reviewer's points are collected — a real text box, not a hidden one.** The
question dialog only offers a text field behind its "Other" row, and reviewers do not
find it. So when the reviewer picks "Add my points", do **not** ask another
`AskUserQuestion`: reply with one short message — "Type the points you want covered, one
per line, in the chat box as your next message; I will add them to the plan above and
show it again" — and **end the turn**. The reviewer then types freely
in the normal chat box. Treat their next message as the additions: every line or
sentence becomes one `R-n` item. (Text the reviewer types into "Other" on the question
itself is also accepted as additions — that path just never has to be discovered.)

Number every addition `R-1`, `R-2`, … in a **Reviewer-added checks** section of the
plan, in the reviewer's own words, rewrite the plan file, show the updated section, and
ask the plan question once more so the reviewer can keep adding until they choose
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
Change:   <N> files, +<added> / -<removed> lines, <M> commits    (from that PR's stat.txt and meta.txt)
Scope:    <one line: what the change claims to do, from the ticket summary>
Findings will be posted here and on the PR once the review is complete.
```

With several PRs in the review set, repeat the `PR:` / `Branch:` / `Change:` lines once
per PR, each `PR:` line starting with its repository. Add before "Findings will be
posted", only when they apply: `Not reviewed in this run: <repo> #<id> <title>, …` and
`Could not be read: <repo> #<id>, …` — so the ticket shows which PRs this review covers.

If `jira_comment.sh` fails, say so and continue the review; the comment is a courtesy,
the review is the work.

**If the reviewer does not approve** — walks away from the question, or says stop — post
nothing, move nothing, record nothing in the ledger; remove every PR's worktree
(`git -C <clone> worktree remove --force .review/<KEY>/<unit>/wt`); tell the user the review was abandoned before it started and that the
ticket is untouched. Re-running the skill on the same key starts over from step 1.

### 4. Evaluate the diff against the ticket, then work the checklist

**CR-01 comes first and carries the review.** For every expectation in
`.review/<KEY>-requirements.md`, find the code in the reviewed PRs' `full.patch` files that
satisfies it and the test that proves it. Record each as **Met**, **Partial**, or **Missing** with the
`file:line`. Then go the other way: every behavioural change in the diff must trace back
to an expectation — anything that does not is either scope creep (CR-02) or a
misreading of the ticket (CR-01).

An expectation that is Missing or Partial is a **FAIL** on CR-01, and the failure reason
is written in the ticket's own words: *"the ticket asks for X (description, para 2); the
diff does nothing for X"* or *"comment by A on <date> says Y is out of scope, but the
diff changes Y"*. Severity: Missing criterion = MAJOR; the change solving a different
problem than the one asked = BLOCKER. The overall verdict is FAIL whenever an expectation
is not met — a clean checklist does not rescue a change that does not do what was asked.

With several PRs, an expectation is Met when any reviewed PR satisfies it and Missing only
when none does; one that belongs to a PR **not reviewed in this run** is not Missing — it
is "not in this run" and goes under Not verified. Every finding cites
`<unit>:<path>:<line>` when the review set has more than one PR.

Then read `references/review-checklist.md` and work the remaining 27 items in order. Do not skip to the
interesting ones — the boring items are where production incidents come from.

Then work every **plan-specific item** — the technology checks `T-1`, `T-2`, … and the
reviewer's additions `R-1`, `R-2`, … and the checks between PRs `X-1`, `X-2`, … from the
plan in step 3 — the same way: what was asked, where in the diff it was checked, the verdict.

One verdict per item: `PASS`, `FAIL`, `WARN`, `N/A`, or `UNVERIFIED`.

Severity on every FAIL:
- `BLOCKER` — do not merge. Data loss, security hole, breaks production, breaks a contract,
  cannot be rolled back
- `MAJOR` — must fix before merge. Wrong behaviour in a real scenario, untested new logic,
  unhandled failure path, resource leak
- `MINOR` — fix now or follow up. Naming, duplication, missing comment

Use `ultrathink` on CR-04 (edge cases), CR-12 (compatibility), CR-14 (authorisation), and
CR-23 (rollback). Those four are where careful reasoning pays and skimming costs the most.

**Running anything — build, tests, linter — happens in the reviewed PR's worktree
(`.review/<KEY>/<unit>/wt`, created in step 1), never in the user's checkout.** Run the build or
test command from step 2 inside that folder, on the module(s) the diff touches. Record
the exact command and its result (counts, failing test names) in the report; a run
that could not be done makes the item `UNVERIFIED`, not PASS. Build and test commands
differ per repository and are not pre-approved by this skill, so expect a permission
prompt for them. If `meta.txt` says the branch is behind the target, say in the report
that the run proves the branch, not the merge.

### 5. Write the report

Follow `references/report-template.md`. Write to `.review/<TICKET-KEY>-review.md` — one
report for the run, however many PRs it covers. Name every reviewed PR in the header;
list PRs not in this run and unreadable ones under "Not verified".

- Overall verdict is **FAIL if any BLOCKER or MAJOR exists** in any PR or between PRs, otherwise PASS. An unmet
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
  `bash ${CLAUDE_SKILL_DIR}/scripts/post_pr_comment.sh <id> .review/<KEY>/<unit>/pr-summary.md --repo <slug>`
  One comment **per reviewed PR**, always with `--repo` — a PR number is only unique in its
  repository, and without it the comment lands on whatever PR has that number here.
  Write each summary first: the ticket verdict line and counts, the BLOCKER and MAJOR
  findings **of that PR** and the checks between PRs that touch it, with `file:line`, one
  line per plan-specific check with its verdict, and the "Not verified" list. Not the
  whole report. A merged PR gets its comment too.
- **Jira comment**:
  `bash ${CLAUDE_SKILL_DIR}/scripts/jira_comment.sh <KEY> @.review/<KEY>-jira-summary.txt`
  (convert the Markdown summary to Jira wiki markup first: `##` → `h2.`, `**x**` → `*x*`,
  backticks → `{{x}}`). **One** comment for the run. The first line is
  `h2. Code review — <KEY> · <repo> PR #<id>[, <repo> PR #<id> …] · Verdict: <PASS/FAIL>`
  — it starts with "Code review" (rule 7) and names every reviewed PR, so a ticket shows
  which PRs each review covered.

- **QA handoff — only on the user's yes, when the verdict is PASS, every reviewable PR on
  the ticket was in this run's review set, and the ticket is currently in the review
  status.** When some reviewable PR was left out, skip the move and say which PRs still
  need their review:
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
  `<developer.name>` is `linked_by.name` of the PR whose findings failed the review —
  the person who linked that PR on the ticket. When failing findings sit in PRs linked by
  different people, the move question offers one option per person (and "leave the
  ticket where it is"); when only checks between PRs failed, use the ticket's top-level
  `developer`. The script tries `jira.rework_targets` in order — each entry is a transition
  name or a status, so a workflow that names the move differently per issue type is one
  list. Post the findings comment **first** so the developer finds the reasons on the
  ticket. Show the script's output verbatim.
  - No name is available (`linked_by` and `developer` both null) → do not guess; post the
    findings and tell the user the handback needs a name.

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

Every execution of this skill, on every machine, sends one record to the team's Review
Ledger service so the team can see the agent's throughput and turnaround. The script does
the sending itself over HTTP — no Claude tool, no Claude account or subscription involved.
The ledger address comes from `LEDGER_URL` in `jira.env` (or the shell), else
`tracking.ledger_url` in `.claude/jira-project.json`, else `${CLAUDE_SKILL_DIR}/tracking.json`;
never substitute another URL. This step is not optional and needs no confirmation: it
writes to the review store, not to Jira or the PR.

1. Build and send the record — after step 6 has finished (or right after the blocked comment):
   ```bash
   python3 ${CLAUDE_SKILL_DIR}/scripts/record_run.py <KEY> --verdict <PASS|FAIL|BLOCKED> --outcome <qa_handoff|rework|posted|report_only|blocked> [--posted pr,jira] [--blocked-missing "branch name,PR link"] [--pr <repo>:<id>,...]
   ```
   Pass `--pr` with every PR of the review set (`girnarsoft-one-lms:1210,lms-pwa-ui:45`)
   whenever the reviewer left a reviewable PR out of the run — the record must carry the
   PRs actually reviewed. Without `--pr` the record covers every reviewable PR linked on
   the ticket. The size is summed over the `.review/<KEY>/<unit>/` folders.
   `--outcome` is what actually happened in step 6: `qa_handoff` / `rework` when the
   handoff script ran, `posted` when the findings were posted but the ticket was not
   moved (the user declined, or the ticket was not in the review status), `report_only`
   only when posting failed at both places, `blocked` for step-1 blocks.
   `--posted` lists where the findings actually landed (`pr,jira`, or the one that
   succeeded). The script writes `.review/<KEY>-run.json`, first sends any runs still
   waiting in `.review/pending/`, then sends this one, and prints
   `run_id=<id> file=<path> ledger=<url> result=<result> [reason=<why>]`, plus
   `pending_sent=<n> pending_left=<n>` when the queue was involved.
2. Tell the user what `result` says — never report a run as recorded unless it says so:
   - `created` / `replaced` → recorded; give the dashboard address (the `ledger` URL).
   - `queued` → the ledger could not take it now (`reason`: unreachable — the reviewer is
     off the office network or the service is down; `HTTP 401` — `LEDGER_TOKEN` missing
     from `jira.env`; no URL configured). The run waits in `.review/pending/` and is sent
     automatically with the next run, or now with
     `python3 ${CLAUDE_SKILL_DIR}/scripts/record_run.py --flush`. Not an error for the review.
   - `rejected` (exit status 1) → the ledger refused the record as invalid; quote `reason`
     and keep `.review/<KEY>-run.json`. Retrying the same record will not help; this is a
     bug to report, not something to work around.
   A `pending_left` above 0 after a successful send means older runs are still waiting;
   say so in one line.
3. Never write the record anywhere else (no artifact, no Jira property, no file outside
   `.review/`).
4. Remove every PR's worktree from its own clone:
   `git -C <clone> worktree remove --force .review/<KEY>/<unit>/wt`. The reports, plan,
   context, diffs and run record under `.review/` stay; only the checkouts go.

The record carries the ticket (key, type, priority, summary), reviewer, developer, every reviewed PR with its repository,
start/finish/duration, verdict, finding counts, diff size, a size-based complexity score
and the outcome. Iteration numbers and time-to-PASS are computed by the ledger from all
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
- **Compressed plan.** A plan shown as name lists ("13 technology-specific checks
  T-1..T-13: positional row contract, Mockito spy idioms, …") tells the reviewer
  nothing they can approve or add to. Every check is a table row that says what will be
  looked at, where, and why it matters, in plain language.
- **Half a ticket.** Reviewing one PR of a change another PR consumes and moving the
  ticket to QA; or skipping the checks between PRs because each diff looked fine alone.
  The contract between them is where such changes break.
- **Missing the missing.** The costliest defects are absent from the diff — the test not
  written, the caller not updated, the migration not made reversible. Scan for absence
  deliberately.
