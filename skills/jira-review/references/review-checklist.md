# Code Review Checklist (technology-independent)

28 checks in 9 groups. Every check is phrased as a question about *behaviour*, not about
syntax, so it applies to any language. Each entry states what to look for, what makes it
a FAIL, and when it is legitimately N/A.

**Contents**
- [A. Intent and scope](#a-intent-and-scope) — CR-01 to CR-03
- [B. Correctness](#b-correctness) — CR-04 to CR-08
- [C. Design](#c-design) — CR-09 to CR-12
- [D. Data](#d-data) — CR-13
- [E. Security](#e-security) — CR-14 to CR-17
- [F. Performance and resilience](#f-performance-and-resilience) — CR-18 to CR-20
- [G. Operability](#g-operability) — CR-21 to CR-23
- [H. Verification](#h-verification) — CR-24 to CR-26
- [I. Conformance](#i-conformance) — CR-27 to CR-28

---

## A. Intent and scope

### CR-01 — Requirement fidelity
Does the change actually do what the ticket asked?

Map every acceptance criterion to the code that satisfies it. Then map in reverse: every
behavioural change in the diff back to a criterion.

**FAIL when:** a criterion is unimplemented; the implementation solves a different problem;
behaviour changed in a way the ticket never asked for; an edge case named in the ticket is
not handled.
**Severity:** unimplemented criterion = MAJOR. Wrong interpretation of the requirement =
BLOCKER (it will ship wrong).
**Never N/A.**

### CR-02 — Scope discipline
Is everything in this diff related to this ticket?

**FAIL when:** unrelated refactors, opportunistic renames across untouched modules,
dependency bumps nobody asked for, or a second feature smuggled in. These make the change
hard to review, hard to revert, and hard to bisect.
**Severity:** MINOR normally, MAJOR when the unrelated change is itself risky.

### CR-03 — Diff hygiene
Is the diff clean of development residue?

Look for: debug print/log statements, commented-out code, leftover TODO/FIXME without a
ticket reference, `.only`/`skip` on tests, hardcoded local paths or personal identifiers,
stray files (IDE config, build output, `.env`, lock files changed without reason),
merge-conflict markers, mass reformatting that hides the real change.
**Severity:** committed secret = BLOCKER (see CR-16). Skipped test = MAJOR. Rest = MINOR.

---

## B. Correctness

### CR-04 — Logic correctness and edge cases
Trace the code by hand. Does it produce the right result for every input class?

Systematically consider: empty input, single element, maximum size, null/absent/undefined,
zero, negative, boundary values (off-by-one on every loop and slice), duplicates,
unsorted input, unicode and multi-byte text, very long strings, timezone and DST for any
date maths, floating-point equality and monetary rounding, integer overflow, and the
"nothing found" branch of every lookup.

**FAIL when:** any input class produces a wrong result, a crash, or silent corruption.
**Severity:** silent wrong data = BLOCKER. Crash on a reachable input = MAJOR.
**Never N/A.**

### CR-05 — Error handling and failure paths
What happens when each operation fails?

For every call that can fail: is the failure detected, handled at the right level, and
surfaced with enough context to debug? Language-specific forms of the same question —
exceptions caught (and not swallowed by an empty handler), error return values checked,
promise rejections handled, result types unwrapped safely, callback error arguments used.

**FAIL when:** failures are swallowed; a generic catch-all hides distinct failure types;
errors are caught and logged but execution continues into an invalid state; the error
message loses the original cause; the caller cannot distinguish "not found" from "backend
down"; partial failure leaves the system in a half-updated state.
**Severity:** swallowed failure = MAJOR. Half-updated state = BLOCKER.
**Never N/A.**

### CR-06 — Input validation and trust boundaries
Is untrusted data validated at the point it enters the system?

Identify each trust boundary crossed: HTTP request, message queue, file upload, CLI
argument, config file, third-party API response, database read of user-supplied data.
Validate type, range, length, format, and required-ness — at the boundary, not deep inside
the business logic.

**FAIL when:** a boundary accepts unvalidated data; validation exists on the client only;
size/length limits are absent (unbounded upload, unbounded list, unbounded pagination);
the validation and the actual use disagree.
**N/A when:** the change touches no trust boundary — but check carefully before claiming this.

### CR-07 — Concurrency and shared state
Can two things run at once here, and what breaks if they do?

Look for shared mutable state, non-atomic read-modify-write, check-then-act races
(`if not exists: create`), lock ordering, unbounded thread/goroutine/task creation,
blocking calls inside async contexts, state held in a supposedly stateless component,
and caches mutated without synchronisation.

**FAIL when:** a race can corrupt data or double-process a request; a deadlock is possible;
a shared object is mutated without protection.
**Severity:** data corruption race = BLOCKER.
**N/A when:** genuinely single-threaded, no shared state, and no concurrent invocation
possible — rare for anything server-side.

### CR-08 — Idempotency and retry safety
If this runs twice, is the outcome the same as running it once?

Anything reachable by a retrying client, a message consumer, a cron job, or a webhook must
be safe to repeat. Look for missing idempotency keys, duplicate inserts, double charges,
double emails, counters incremented per-attempt.

**FAIL when:** a retry causes duplicate side effects.
**Severity:** duplicate financial or external side effect = BLOCKER.
**N/A when:** the operation is a pure read.

---

## C. Design

### CR-09 — Architecture and placement
Is this code in the right place?

Does it respect the project's layering (does a controller now contain business logic, does
a domain object now know about HTTP or SQL)? Does one unit have one reason to change? Is
coupling introduced in the right direction? Is a new abstraction earning its keep, or is it
indirection over a single implementation?

**FAIL when:** layering is violated; a module gains a dependency that inverts the intended
direction; logic is duplicated into a layer that should not own it.
**Severity:** MAJOR when it will be expensive to unwind, MINOR when it is local.

### CR-10 — Duplication and reuse
Does this reimplement something that already exists?

Search the codebase for existing helpers before accepting a new one. Also flag the reverse:
premature extraction of a shared abstraction from two coincidentally similar cases.

**FAIL when:** an existing, tested utility was ignored and reimplemented (especially for
security-sensitive things — never a hand-rolled sanitiser, token generator, or date parser).
**Severity:** hand-rolled security primitive = BLOCKER. Otherwise MINOR.

### CR-11 — Naming and readability
Can the next engineer understand this without asking the author?

Names describe intent, not implementation. No unexplained abbreviations or single letters
outside tiny scopes. Booleans read as assertions. Functions do what their name says and
nothing more. Nesting is shallow. Magic numbers and strings are named constants. Comments
explain *why*, not *what* — and no comment contradicts the code beneath it.

**FAIL when:** a name actively misleads (a `get` that mutates, a `validate` that also saves,
a plural name holding a single value). That is MAJOR — misleading names cause bugs.
Merely terse-but-clear naming is at most MINOR.

### CR-12 — Contract and backward compatibility
Who else depends on what just changed?

For any public interface — REST/RPC endpoint, event or message schema, library API, shared
DB view, config key, CLI flag — is the change backward compatible? Removed or renamed
fields, narrowed types, new required parameters, changed status codes, changed default
values, and changed semantics under an unchanged name are all breaking. Check every caller
in the repo, and remember callers outside it. Rolling deploys mean old and new run together.

**FAIL when:** a breaking change ships without versioning, a deprecation window, or a
coordinated consumer update.
**Severity:** BLOCKER.
**N/A when:** the change is entirely internal to one module with no external surface.

---

## D. Data

### CR-13 — Data and persistence
Every data concern in one check, because they fail together.

- **Transactions:** is the unit of work atomic? Does a failure mid-way roll back? Is the
  boundary at the right level, or is it holding a transaction open across a network call?
- **Migrations:** reversible? Safe on a large table (does it lock, does it rewrite)?
  Deployable *before* the code that needs it, and compatible with the currently running
  version? A migration that requires simultaneous code deploy is a broken deploy.
- **Query efficiency:** N+1 queries in a loop, missing index for a new query predicate,
  `SELECT *` pulling large columns, unbounded result sets, missing pagination.
- **Integrity:** constraints, nullability, defaults, cascade behaviour, unique keys.
- **Lifecycle:** does new personal data have a retention/deletion path?

**FAIL when:** any of the above. Irreversible destructive migration = BLOCKER.
Missing index on a hot query = MAJOR.
**N/A when:** no persistence layer is touched.

---

## E. Security

### CR-14 — Authentication and authorisation
Is every new entry point protected, and protected correctly?

Authentication answers "who are you", authorisation answers "may you do this to *this
object*". The second is the one that gets missed. For every new endpoint, action, or
resource accessor: is the caller's permission checked against the specific record being
touched, not just against a role? Can a user change an identifier in the request and reach
another tenant's data?

**FAIL when:** a new endpoint has no auth check; ownership is not verified on an
object-level operation; the check happens after the side effect; permission is checked in
the UI only.
**Severity:** BLOCKER, always.
**N/A when:** no new externally reachable surface — verify, do not assume.

### CR-15 — Injection and untrusted data handling
Is untrusted data ever interpreted as code or structure?

Concatenation into SQL/NoSQL queries, shell commands, file paths (`../` traversal), LDAP,
XPath, template engines, regex, deserialisation of untrusted payloads, HTML/JS output
without contextual escaping, redirect targets taken from input, and server-side requests to
user-supplied URLs (SSRF). The universal answer: parameterise or escape at the point of use;
never sanitise by blocklist.

**FAIL when:** any untrusted value reaches an interpreter unparameterised.
**Severity:** BLOCKER.

### CR-16 — Secrets and sensitive data
Is anything sensitive exposed?

Credentials, tokens, keys, or connection strings in source, config, tests, or fixtures.
Sensitive values in log lines, error messages, stack traces returned to clients, analytics
events, or URLs (which land in access logs). Weak or hand-rolled crypto; passwords not
hashed with a modern KDF; sensitive fields not encrypted at rest where policy requires it.

**FAIL when:** any secret is committed (BLOCKER — and it must be rotated, not just deleted,
since it is in git history). PII in logs = MAJOR.

### CR-17 — Dependencies and supply chain
What did this change pull in?

Is a new dependency justified against what already exists? Is it maintained, widely used,
and appropriately licensed for your product? Are versions pinned or ranged per project
convention? Does the lockfile change match the manifest change? Does the new version carry
known vulnerabilities or a breaking change?

**FAIL when:** a large dependency is added for a trivial function; a version has a known
critical CVE; the licence is incompatible; the lockfile was regenerated wholesale, burying
unrelated upgrades in the diff.
**N/A when:** no dependency manifest changed.

---

## F. Performance and resilience

### CR-18 — Algorithmic and resource efficiency
Will this hold up at production scale, not test scale?

Nested iteration over collections that grow with data; work inside a loop that could be
hoisted; repeated recomputation instead of caching; loading a whole collection into memory
when streaming would do; synchronous work on a latency-sensitive path; a new call in a
hot path.

Judge against realistic volume — ask what the input size actually is in production.
**FAIL when:** complexity is superlinear on data that grows unbounded, or a hot path gains
a blocking call.
**Severity:** MAJOR. BLOCKER if it will take the system down at current volume.

### CR-19 — Resource lifecycle
Is everything that is opened, closed?

Connections, file handles, streams, sockets, locks, threads, timers, subscriptions,
listeners. Are they released on the error path as well as the happy path (the
language's `finally` / `defer` / `using` / RAII / context-manager equivalent)? Are pooled
resources returned rather than leaked? Are unbounded caches and in-memory collections given
a size limit and eviction policy?

**FAIL when:** a resource leaks on any path, or an unbounded structure grows with traffic.
**Severity:** MAJOR — leaks are the classic "fine for a week, dies on Friday night" defect.

### CR-20 — External call resilience
How does this behave when the thing it calls is slow or down?

Every network call needs an explicit timeout — a missing timeout is the single most common
cause of cascading failure. Then: are retries bounded, backed off, and jittered? Are they
applied only to safe (idempotent) operations? Is there a fallback or circuit breaker? Does
the failure degrade gracefully or take the whole request down?

**FAIL when:** a network call has no timeout (MAJOR, BLOCKER on a user-facing path);
retries without backoff (thundering herd); retrying a non-idempotent write.
**N/A when:** no external I/O.

---

## G. Operability

### CR-21 — Observability
If this breaks at 3am, can someone diagnose it from the outside?

Are the meaningful events logged at appropriate levels (not everything at INFO, not errors
at DEBUG)? Do logs carry correlation/trace identifiers and enough context to be actionable
(ids, not just "operation failed")? Are logs structured per project convention? Are new
metrics emitted for anything worth alerting on? Is logging inside a hot loop going to flood
the pipeline? And per CR-16, no sensitive values.

**FAIL when:** a new failure path is silent; an error is logged with no context; a hot loop
logs per iteration.
**Severity:** MAJOR for a silent failure path.

### CR-22 — Configuration and feature control
Is anything environment-specific hardcoded?

URLs, hostnames, ports, timeouts, limits, credentials, feature toggles, region names.
Are new config keys documented and given sane defaults? Does the app fail fast and clearly
on missing required config rather than starting up broken? Is risky new behaviour behind a
flag that can be turned off without a deploy?

**FAIL when:** environment-specific values are hardcoded; a new required config key has no
default and no documentation.

### CR-23 — Deployability and rollback
Can this be released safely, and undone?

Does it deploy in one step, or does it require ordered steps nobody wrote down? Is it
compatible with the previous version running alongside it during a rolling deploy? Can it
be rolled back — and would a rollback corrupt data written by the new version? Are new
infrastructure or permission requirements accounted for?

**FAIL when:** rollback is impossible or unsafe; the change breaks mixed-version operation.
**Severity:** BLOCKER.

---

## H. Verification

### CR-24 — Test coverage of new behaviour
Is every new behaviour covered by a test that would fail without the change?

Not a coverage percentage — a behavioural question. Each new branch, each error path, each
boundary from CR-04, and each bug fix (a regression test that reproduces the original bug).
Integration points covered at the integration level, not only mocked out.

**FAIL when:** new logic ships with no test; a bug fix has no regression test; only the
happy path is tested.
**Severity:** MAJOR.
**N/A when:** the change is genuinely untestable config or documentation — a high bar.

### CR-25 — Test quality
Are the tests worth having?

Do they assert on outcomes rather than implementation detail (so a refactor does not break
them)? Are assertions specific (not just "did not throw")? Are they deterministic — no
sleeps, no real clock, no real network, no dependence on test execution order or on data
left behind by another test? Are they readable enough to serve as documentation? Is the
mocking proportionate, or is the test now just asserting that mocks were called?

**FAIL when:** a test cannot fail; sleeps or real time/network create flakiness; tests
depend on ordering.
**Severity:** MAJOR for a test that cannot fail — it is worse than no test, because it
signals safety that does not exist.

### CR-26 — Documentation
Is the knowledge in someone's head written down?

API documentation for changed contracts, README or runbook updates for new operational
steps, comments explaining non-obvious decisions and trade-offs, changelog entries per
convention, and an updated architecture note if the design shifted. Any deprecation clearly
marked with its replacement and removal timeline.

**FAIL when:** a public contract changed without documentation; a new operational
requirement is undocumented.
**Severity:** MINOR to MAJOR depending on who is blocked without it.

---

## I. Conformance

### CR-27 — Project convention conformance
Does this look like the rest of the codebase?

Check against `CLAUDE.md` (including nested ones in the touched subdirectories), `AGENTS.md`,
`CONTRIBUTING.md`, linter and formatter config, and — most reliably — the neighbouring
untouched files. Project rules beat generic
best practice every time: if this repo does something unusual on purpose, follow it.
Also check commit message and branch naming convention if the project has one.

**FAIL when:** an explicit documented project rule is broken.
**Severity:** MINOR unless the rule exists for a safety reason.

### CR-28 — Accessibility, internationalisation, and UX
Applies to any user-facing surface, web or not.

Keyboard reachability and focus handling, semantic structure and labels for assistive
technology, colour contrast, text alternatives for non-text content, no meaning conveyed by
colour alone. User-visible strings externalised rather than hardcoded, with no assumptions
about text length, direction, name/address format, or date/number/currency formatting.
Error messages that tell the user what to do next.

**FAIL when:** a new interactive control is keyboard-inaccessible or unlabelled; user-facing
strings are hardcoded in a localised product.
**N/A when:** no user-facing surface is touched.

---

## Judging severity — quick rule

Ask: *what happens if this ships unchanged?*

| Consequence | Severity |
|---|---|
| Data loss, corruption, security breach, outage, broken contract, unrollbackable | BLOCKER |
| Wrong behaviour in a real scenario, untested new logic, unhandled failure, leak | MAJOR |
| Slower to read, harder to maintain, inconsistent, undocumented internals | MINOR |

When genuinely torn between two levels, choose the higher one and say why in the reason.
