#!/usr/bin/env bash
# Collect everything needed to review a change set, into .review/
#
#   bash collect_diff.sh <source-branch> [target-branch]
#
# source-branch  the branch containing the work (the PR's "from")
# target-branch  what it will merge into (default: git.base_branch from
#                .claude/jira-project.json, else origin/HEAD, else main/master/develop)
#
# Uses the merge base, so the diff shows only what this branch did -- not everything
# that landed on the target since the branch was cut.
#
# Writes:
#   .review/meta.txt        branches, merge base, commit log
#   .review/stat.txt        per-file added/removed counts
#   .review/files.txt       changed file paths with status (A/M/D/R)
#   .review/full.patch      the complete diff
#   .review/context.txt     repo signals: manifests, CI config, convention files

set -euo pipefail

SRC="${1:?usage: collect_diff.sh <source-branch> [target-branch]}"
OUT=".review"

if ! git rev-parse --git-dir >/dev/null 2>&1; then
  echo "error: not inside a git repository" >&2
  exit 1
fi

git fetch origin --quiet --prune || echo "warning: fetch failed, using local refs" >&2

resolve() {
  git rev-parse --verify --quiet "$1" \
    || git rev-parse --verify --quiet "origin/$1" \
    || return 1
}

TOP=$(git rev-parse --show-toplevel)
if [ $# -ge 2 ]; then
  TGT="$2"
else
  # The base branch is declared per repo (git.base_branch); the remote HEAD and the
  # usual default names are only fallbacks, since many repos merge work somewhere else.
  TGT=""
  if [ -f "$TOP/.claude/jira-project.json" ] && command -v jq >/dev/null 2>&1; then
    TGT=$(jq -r '.git.base_branch // ""' "$TOP/.claude/jira-project.json")
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

mkdir -p "$OUT"

{
  echo "source branch : $SRC ($SRC_SHA)"
  echo "target branch : $TGT ($TGT_SHA)"
  echo "merge base    : $BASE"
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
    [ -e "$f" ] && echo "  $f"
  done
  echo
  echo "== convention and CI files present =="
  for f in CLAUDE.md AGENTS.md CONTRIBUTING.md README.md .claude/skills \
           .editorconfig .eslintrc* .prettierrc* checkstyle.xml .flake8 \
           .github/workflows bitbucket-pipelines.yml .gitlab-ci.yml Jenkinsfile \
           Dockerfile docker-compose.yml; do
    [ -e "$f" ] && echo "  $f"
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
[ "$LINES" -gt 5000 ] && echo "note: large diff -- review in batches by file group, do not skim." || true
