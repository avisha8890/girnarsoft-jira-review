#!/usr/bin/env bash
# Collect everything needed to review a change set, into .review/
#
#   bash collect_diff.sh <source-branch> [target-branch] [--out <dir>] [--worktree <dir>]
#
# source-branch  the branch containing the work (the PR's "from")
# target-branch  what it will merge into (default: git.base_branch from
#                <repo>/.claude/jira-project.json, else ~/.claude/jira-project.json,
#                else origin/HEAD, else main/master/develop)
# --out <dir>    where to write (default <repo root>/.review/, whatever the current
#                directory is -- record_run.py reads from the same place).
# --worktree <dir>  also check the source commit out into a detached git worktree at
#                <dir> (replacing one already there), so the review reads and runs the
#                PR's code without touching the user's checkout. Remove it afterwards:
#                git worktree remove --force <dir>
#
# Uses the merge base, so the diff shows only what this branch did -- not everything
# that landed on the target since the branch was cut.
#
# Writes:
#   .review/meta.txt        branches, merge base, how far behind the target, worktree, commit log
#   .review/stat.txt        per-file added/removed counts
#   .review/files.txt       changed file paths with status (A/M/D/R)
#   .review/full.patch      the complete diff
#   .review/context.txt     repo signals: manifests, CI config, convention files

set -euo pipefail

if ! git rev-parse --git-dir >/dev/null 2>&1; then
  echo "error: not inside a git repository" >&2
  exit 1
fi
TOP=$(git rev-parse --show-toplevel)

# Anchor to the repo root: the skill's other scripts read .review/ from there, and the
# shell's working directory is not guaranteed to be the root when this runs.
OUT="$TOP/.review"
WT=""
POS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --out) OUT="${2:?--out needs a directory}"; shift 2 ;;
    --out=*) OUT="${1#--out=}"; shift ;;
    --worktree) WT="${2:?--worktree needs a directory}"; shift 2 ;;
    --worktree=*) WT="${1#--worktree=}"; shift ;;
    *) POS+=("$1"); shift ;;
  esac
done
set -- "${POS[@]+"${POS[@]}"}"
SRC="${1:?usage: collect_diff.sh <source-branch> [target-branch] [--out <dir>]}"

git fetch origin --quiet --prune || echo "warning: fetch failed, using local refs" >&2

resolve() {
  git rev-parse --verify --quiet "$1" \
    || git rev-parse --verify --quiet "origin/$1" \
    || return 1
}

if [ $# -ge 2 ]; then
  TGT="$2"
else
  # The base branch is declared per repo (git.base_branch); the remote HEAD and the
  # usual default names are only fallbacks, since many repos merge work somewhere else.
  TGT=""
  PJ="$TOP/.claude/jira-project.json"
  [ -f "$PJ" ] || PJ="$HOME/.claude/jira-project.json"
  if [ -f "$PJ" ] && command -v jq >/dev/null 2>&1; then
    TGT=$(jq -r '.git.base_branch // ""' "$PJ")
  fi
  [ -z "$TGT" ] && TGT=$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null | sed 's|^origin/||' || true)
  [ -z "$TGT" ] && for c in main master develop; do
    if resolve "$c" >/dev/null 2>&1; then TGT="$c"; break; fi
  done
fi
[ -z "${TGT:-}" ] && { echo "error: could not determine target branch" >&2; exit 1; }

SRC_SHA=$(resolve "$SRC") || { echo "error: unknown branch '$SRC'" >&2; exit 1; }
TGT_SHA=$(resolve "$TGT") || { echo "error: unknown branch '$TGT'" >&2; exit 1; }
BASE=$(git merge-base "$TGT_SHA" "$SRC_SHA")
# Commits on the target that the source branch does not have: the branch is that far
# behind. Zero means the branch already contains the target; the PR's code is then
# exactly what merging would produce.
BEHIND=$(git rev-list --count "$SRC_SHA..$TGT_SHA")

mkdir -p "$OUT"

# The review folder must never reach the repository: make sure .gitignore covers it
# (appending once, with a comment, when the repo has no rule yet) and warn if an earlier
# commit already tracks review files -- untracking is a git change the reviewer makes.
ensure_review_ignored() {
  local gi="$TOP/.gitignore"
  if ! grep -qsE '^/?\.review/?$' "$gi"; then
    { [ -s "$gi" ] && [ "$(tail -c1 "$gi" | wc -l)" -eq 0 ] && echo; \
      echo "# jira-review skill working files: diffs, plans, reports, run records, review worktree"; \
      echo ".review/"; } >> "$gi"
    echo "note: added .review/ to .gitignore -- commit that change." >&2
  fi
  if [ -n "$(git -C "$TOP" ls-files .review | head -1)" ]; then
    echo "warning: files under .review/ are tracked by git; run: git rm -r --cached .review  (keeps them on disk)" >&2
  fi
}
[ "$OUT" = "$TOP/.review" ] && ensure_review_ignored

if [ -n "$WT" ]; then
  case "$WT" in /*) ;; *) WT="$TOP/$WT" ;; esac
  if [ -e "$WT" ]; then
    git worktree remove --force "$WT" 2>/dev/null || rm -rf "$WT"
  fi
  git worktree prune
  git worktree add --detach "$WT" "$SRC_SHA" >/dev/null
fi

{
  echo "source branch : $SRC ($SRC_SHA)"
  echo "target branch : $TGT ($TGT_SHA)"
  echo "merge base    : $BASE"
  echo "behind target : $BEHIND commit(s) on $TGT not in $SRC"
  echo "worktree      : ${WT:-none}"
  echo "generated     : $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo
  echo "== commits on this branch =="
  git log --no-merges --pretty=format:'%h  %an  %ad  %s' --date=short "$BASE..$SRC_SHA"
  echo
  echo
  echo "== authors =="
  git shortlog -sne "$BASE..$SRC_SHA"
} > "$OUT/meta.txt"

git diff --stat "$BASE" "$SRC_SHA" > "$OUT/stat.txt"
git diff --name-status -M "$BASE" "$SRC_SHA" > "$OUT/files.txt"
git diff -M --find-copies --patch "$BASE" "$SRC_SHA" > "$OUT/full.patch"

{
  echo "== languages by changed file extension =="
  awk '{print $NF}' "$OUT/files.txt" | sed 's/.*\.//' | sort | uniq -c | sort -rn
  echo
  echo "== build / dependency manifests present =="
  for f in pom.xml build.gradle build.gradle.kts package.json requirements.txt \
           pyproject.toml Pipfile go.mod Cargo.toml Gemfile composer.json \
           *.csproj *.sln Makefile CMakeLists.txt; do
    [ -e "$TOP/$f" ] && echo "  $f"
  done
  echo
  echo "== convention and CI files present =="
  for f in CLAUDE.md AGENTS.md CONTRIBUTING.md README.md .claude/skills \
           .editorconfig .eslintrc* .prettierrc* checkstyle.xml .flake8 \
           .github/workflows bitbucket-pipelines.yml .gitlab-ci.yml Jenkinsfile \
           Dockerfile docker-compose.yml; do
    [ -e "$TOP/$f" ] && echo "  $f"
  done
  echo
  echo "== manifests touched by this change =="
  grep -Ei '(pom\.xml|build\.gradle|package(-lock)?\.json|requirements\.txt|pyproject\.toml|go\.(mod|sum)|Cargo\.(toml|lock)|Gemfile(\.lock)?|composer\.(json|lock))$' \
    "$OUT/files.txt" || echo "  none"
  echo
  echo "== migration / schema files touched =="
  grep -Ei '(migration|migrate|schema|liquibase|flyway|alembic|\.sql$)' \
    "$OUT/files.txt" || echo "  none"
  echo
  echo "== test files touched =="
  grep -Ei '(test|spec|__tests__|_test\.|\.test\.|\.spec\.)' \
    "$OUT/files.txt" || echo "  none  <-- check CR-24 carefully"
} > "$OUT/context.txt"

FILES=$(wc -l < "$OUT/files.txt" | tr -d ' ')
LINES=$(wc -l < "$OUT/full.patch" | tr -d ' ')

echo "Collected into $OUT/  --  $FILES file(s) changed, $LINES patch lines."
echo "  meta.txt stat.txt files.txt full.patch context.txt"
[ "$BEHIND" -gt 0 ] && echo "note: source is $BEHIND commit(s) behind $TGT -- the PR's code is not what merging would produce; say so under Not verified." || true
[ -n "$WT" ] && echo "worktree: $WT (source commit checked out; read and run the PR's code there; remove with: git worktree remove --force $WT)" || true
[ "$LINES" -gt 5000 ] && echo "note: large diff -- review in batches by file group, do not skim." || true
