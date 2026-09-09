# Code Review Report Template

Fill this out completely. Write it to `.review/<TICKET-KEY>-review.md`.

---

# Code Review — {TICKET-KEY}: {Ticket summary}

| | |
|---|---|
| **Ticket** | {TICKET-KEY} — {url} |
| **Pull request** | {PR title} — {url} |
| **Branches** | `{source}` → `{target}` |
| **Author** | {author} |
| **Reviewer** | {current user} |
| **Reviewed at** | {ISO timestamp} |
| **Change size** | {N} files, +{added} / −{removed} lines |
| **Languages** | {detected from the diff} |

## Verdict: {PASS / FAIL}

> FAIL if any BLOCKER or MAJOR finding exists. Otherwise PASS.

**{X} blockers · {Y} major · {Z} minor · {W} warnings · {V} unverified**

{Two or three sentences: what this change does, and the single most important thing the
author needs to know. If FAIL, name the blocking issue here — do not make them scan the
table for it.}

---

## Checklist results

| ID | Check | Verdict | Severity | Notes |
|---|---|---|---|---|
| CR-01 | Requirement fidelity | | | |
| CR-02 | Scope discipline | | | |
| CR-03 | Diff hygiene | | | |
| CR-04 | Logic correctness and edge cases | | | |
| CR-05 | Error handling and failure paths | | | |
| CR-06 | Input validation and trust boundaries | | | |
| CR-07 | Concurrency and shared state | | | |
| CR-08 | Idempotency and retry safety | | | |
| CR-09 | Architecture and placement | | | |
| CR-10 | Duplication and reuse | | | |
| CR-11 | Naming and readability | | | |
| CR-12 | Contract and backward compatibility | | | |
| CR-13 | Data and persistence | | | |
| CR-14 | Authentication and authorisation | | | |
| CR-15 | Injection and untrusted data | | | |
| CR-16 | Secrets and sensitive data | | | |
| CR-17 | Dependencies and supply chain | | | |
| CR-18 | Algorithmic and resource efficiency | | | |
| CR-19 | Resource lifecycle | | | |
| CR-20 | External call resilience | | | |
| CR-21 | Observability | | | |
| CR-22 | Configuration and feature control | | | |
| CR-23 | Deployability and rollback | | | |
| CR-24 | Test coverage of new behaviour | | | |
| CR-25 | Test quality | | | |
| CR-26 | Documentation | | | |
| CR-27 | Project convention conformance | | | |
| CR-28 | Accessibility, i18n, and UX | | | |

Notes column: one line. For PASS, what you verified ("all three new branches covered by
tests in OrderServiceTest"). For N/A, why ("no persistence layer touched"). For FAIL,
point to the detailed finding below.

---

## Findings

Repeat this block for each FAIL and WARN, ordered BLOCKER → MAJOR → MINOR → WARN.

### {SEVERITY} · {CR-XX} · {Short title}

**Where:** `path/to/file.ext:120-134`

**What is wrong**
{Precise description of the defect. Quote the relevant lines.}

**Why it matters**
{The concrete consequence in production. Not "this is bad practice" — describe the
failure scenario, who it affects, and how it would surface.}

**Suggested fix**
```{language}
{Working code in the repo's language and style. Required for BLOCKER and MAJOR.}
```

---

## Requirement traceability

| Acceptance criterion | Implemented in | Covered by test | Status |
|---|---|---|---|
| {AC-1 text} | `file.ext:45` | `FileTest.ext:20` | Met / Partial / Missing |

Any criterion marked Partial or Missing must also appear as a CR-01 finding.

---

## Not verified

State every blind spot. This section protects the author and you.

- {What you could not check, and why — e.g. "the migration was not run against a
  production-sized table; lock duration on `orders` (14M rows) is unverified"}

If everything was verifiable, write "Nothing — all 28 checks were evaluated against the
full diff."

---

## Positive notes

One to three things done well. Be specific — "the retry wrapper in `HttpClient.ext:88`
correctly excludes non-idempotent verbs" beats "good job". Skip this section rather than
padding it with generic praise.
