#!/usr/bin/env bash
# Post a comment on a Jira issue, using the same jira.env as the girnarsoft-jira plugin.
#
#   bash jira_comment.sh <ISSUE-KEY> <text | @file> [--dry-run]
#
# Text starting with "@" is read from that file. --dry-run prints the payload and exits.
# Credentials: JIRA_TOKEN (bearer) or JIRA_USER + JIRA_PASS (basic), read from
# $JIRA_ENV_FILE, <repo>/.claude/jira.env, or ~/.claude/jira.env.

set -euo pipefail

KEY="${1:?usage: jira_comment.sh <ISSUE-KEY> <text | @file> [--dry-run]}"
TEXT="${2:?usage: jira_comment.sh <ISSUE-KEY> <text | @file> [--dry-run]}"
DRY=0; [ "${3:-}" = "--dry-run" ] && DRY=1

if [ "${TEXT#@}" != "$TEXT" ]; then
  FILE="${TEXT#@}"
  [ -f "$FILE" ] || { echo "error: no such file: $FILE" >&2; exit 1; }
  TEXT=$(cat "$FILE")
fi

TOP=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
ENV_FILE="${JIRA_ENV_FILE:-}"
[ -z "$ENV_FILE" ] && [ -f "$TOP/.claude/jira.env" ] && ENV_FILE="$TOP/.claude/jira.env"
[ -z "$ENV_FILE" ] && [ -f "$HOME/.claude/jira.env" ] && ENV_FILE="$HOME/.claude/jira.env"
if [ -n "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi
: "${JIRA_BASE_URL:=https://jira.girnarsoft.com}"
JIRA_BASE_URL="${JIRA_BASE_URL%/}"

if [ -n "${JIRA_TOKEN:-}" ]; then
  AUTH=(--header "Authorization: Bearer ${JIRA_TOKEN}")
elif [ -n "${JIRA_USER:-}" ] && [ -n "${JIRA_PASS:-}" ]; then
  AUTH=(--user "${JIRA_USER}:${JIRA_PASS}")
else
  echo "error: no Jira credentials in ${ENV_FILE:-the environment} (JIRA_USER + JIRA_PASS)" >&2
  exit 1
fi

BODY=$(jq -n --arg b "$TEXT" '{body: $b}')
URL="${JIRA_BASE_URL}/rest/api/2/issue/${KEY}/comment"

if [ "$DRY" = 1 ]; then
  echo "DRY RUN -> POST $URL"
  echo "$BODY" | jq .
  exit 0
fi

RESP=$(curl --silent --show-error --fail-with-body -4 --connect-timeout 8 --max-time 20 \
  "${AUTH[@]}" --header "Content-Type: application/json" --data "$BODY" "$URL")
echo "$RESP" | jq -r '"posted comment \(.id) on '"$KEY"' at \(.created)"'
