#!/usr/bin/env bash
# Shared setup for the jira-review shell scripts. Source it; never run it.
#
# Loads credentials from $JIRA_ENV_FILE, <repo>/.claude/jira.env or ~/.claude/jira.env
# (shell variables already set win), reads <repo>/.claude/jira-project.json, and resolves:
#   TOP, PROJECT_JSON, ENV_FILE
#   JIRA_BASE_URL   from the environment or jira.base_url        (required)
#   JIRA_DEPLOYMENT jira.deployment, else "cloud" for *.atlassian.net, else "server"
#   JIRA_AUTH[]     curl args for Jira (JIRA_TOKEN bearer, or JIRA_USER + JIRA_PASS basic)
#   GIT_HOST        git.host, else inferred from the git.remote URL: bitbucket | github
#   GIT_SLUG        owner/repo (workspace/repo) from that remote, credentials stripped
#   GIT_API_BASE    git.api_base, else the host's public API
#   HOST_AUTH[]     curl args for the git host (BITBUCKET_* or GITHUB_TOKEN / GH_TOKEN)
# Nothing organisation-specific lives here: every value comes from config or the environment.

TOP=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
PROJECT_JSON="$TOP/.claude/jira-project.json"

cfg() { [ -f "$PROJECT_JSON" ] && jq -r "$1" "$PROJECT_JSON" 2>/dev/null | sed '/^null$/d' || true; }

ENV_FILE="${JIRA_ENV_FILE:-}"
[ -z "$ENV_FILE" ] && [ -f "$TOP/.claude/jira.env" ] && ENV_FILE="$TOP/.claude/jira.env"
[ -z "$ENV_FILE" ] && [ -f "$HOME/.claude/jira.env" ] && ENV_FILE="$HOME/.claude/jira.env"
if [ -n "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

die() { echo "error: $*" >&2; exit 1; }

# ---- Jira
JIRA_BASE_URL="${JIRA_BASE_URL:-$(cfg '.jira.base_url // empty')}"
[ -n "$JIRA_BASE_URL" ] || die "Jira URL not set -- add JIRA_BASE_URL to ${ENV_FILE:-~/.claude/jira.env} or jira.base_url to $PROJECT_JSON"
JIRA_BASE_URL="${JIRA_BASE_URL%/}"
JIRA_DEPLOYMENT="$(cfg '.jira.deployment // empty')"
if [ -z "$JIRA_DEPLOYMENT" ]; then
  case "$JIRA_BASE_URL" in *.atlassian.net*) JIRA_DEPLOYMENT=cloud ;; *) JIRA_DEPLOYMENT=server ;; esac
fi
if [ -n "${JIRA_TOKEN:-}" ]; then
  JIRA_AUTH=(--header "Authorization: Bearer ${JIRA_TOKEN}")
elif [ -n "${JIRA_USER:-}" ] && [ -n "${JIRA_PASS:-}" ]; then
  JIRA_AUTH=(--user "${JIRA_USER}:${JIRA_PASS}")
elif [ -n "${JIRA_EMAIL:-}" ] && [ -n "${JIRA_API_TOKEN:-}" ]; then
  JIRA_AUTH=(--user "${JIRA_EMAIL}:${JIRA_API_TOKEN}")
else
  die "no Jira credentials in ${ENV_FILE:-the environment} (JIRA_USER + JIRA_PASS, JIRA_TOKEN, or JIRA_EMAIL + JIRA_API_TOKEN)"
fi
jira_api() {  # METHOD PATH [curl args...]
  local method="$1" path="$2"; shift 2
  curl --silent --show-error --fail-with-body -4 --connect-timeout 8 --max-time 25 \
    -X "$method" "${JIRA_AUTH[@]}" --header "Content-Type: application/json" --header "Accept: application/json" \
    "$@" "${JIRA_BASE_URL}${path}"
}

# ---- git host
GIT_REMOTE="$(cfg '.git.remote // empty')"; GIT_REMOTE="${GIT_REMOTE:-origin}"
REMOTE_URL=$(git remote get-url "$GIT_REMOTE" 2>/dev/null || true)
GIT_HOST="$(cfg '.git.host // empty')"
if [ -z "$GIT_HOST" ]; then
  case "$REMOTE_URL" in
    *bitbucket.org*) GIT_HOST=bitbucket ;;
    *github.com*)    GIT_HOST=github ;;
    *)               GIT_HOST="" ;;
  esac
fi
# owner/repo: strip scheme, userinfo, host, and .git -- works for https and ssh forms
GIT_SLUG=$(printf '%s' "$REMOTE_URL" | sed -E 's#^[a-z]+://##; s#^[^@/]+@##; s#^[^:/]+[:/]##; s#\.git$##; s#/$##')
GIT_API_BASE="$(cfg '.git.api_base // empty')"
if [ -z "$GIT_API_BASE" ]; then
  case "$GIT_HOST" in
    bitbucket) GIT_API_BASE="https://api.bitbucket.org/2.0" ;;
    github)    GIT_API_BASE="https://api.github.com" ;;
  esac
fi
HOST_AUTH=()
case "$GIT_HOST" in
  bitbucket)
    if [ -n "${BITBUCKET_ACCESS_TOKEN:-}" ]; then HOST_AUTH=(--header "Authorization: Bearer ${BITBUCKET_ACCESS_TOKEN}")
    elif [ -n "${BITBUCKET_EMAIL:-}" ] && [ -n "${BITBUCKET_API_TOKEN:-}" ]; then HOST_AUTH=(--user "${BITBUCKET_EMAIL}:${BITBUCKET_API_TOKEN}"); fi ;;
  github)
    T="${GITHUB_TOKEN:-${GH_TOKEN:-}}"
    [ -n "$T" ] && HOST_AUTH=(--header "Authorization: Bearer ${T}" --header "X-GitHub-Api-Version: 2022-11-28") ;;
esac
host_api() {  # METHOD PATH [curl args...]
  local method="$1" path="$2"; shift 2
  [ ${#HOST_AUTH[@]} -gt 0 ] || die "no credentials for $GIT_HOST in ${ENV_FILE:-the environment} (BITBUCKET_EMAIL + BITBUCKET_API_TOKEN, or GITHUB_TOKEN)"
  curl --silent --show-error --fail-with-body -4 --connect-timeout 8 --max-time 30 \
    -X "$method" "${HOST_AUTH[@]}" --header "Content-Type: application/json" --header "Accept: application/json" \
    "$@" "${GIT_API_BASE}${path}"
}
