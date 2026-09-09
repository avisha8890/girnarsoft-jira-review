#!/usr/bin/env bash
# Post a comment on a Bitbucket pull request, using the same jira.env as the plugin.
#
#   bash post_pr_comment.sh <pr-id> <markdown-file>
#
# Credentials: BITBUCKET_ACCESS_TOKEN (bearer) or BITBUCKET_EMAIL + BITBUCKET_API_TOKEN
# (basic), read from $JIRA_ENV_FILE, <repo>/.claude/jira.env, or ~/.claude/jira.env.
# Only run this after the user has explicitly confirmed the post.

set -euo pipefail

PR="${1:?usage: post_pr_comment.sh <pr-id> <markdown-file>}"
FILE="${2:?usage: post_pr_comment.sh <pr-id> <markdown-file>}"
[ -f "$FILE" ] || { echo "error: no such file: $FILE" >&2; exit 1; }

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

if [ -n "${BITBUCKET_ACCESS_TOKEN:-}" ]; then
  AUTH=(--header "Authorization: Bearer ${BITBUCKET_ACCESS_TOKEN}")
elif [ -n "${BITBUCKET_EMAIL:-}" ] && [ -n "${BITBUCKET_API_TOKEN:-}" ]; then
  AUTH=(--user "${BITBUCKET_EMAIL}:${BITBUCKET_API_TOKEN}")
else
  echo "error: no Bitbucket credentials in ${ENV_FILE:-the environment} (BITBUCKET_EMAIL + BITBUCKET_API_TOKEN)" >&2
  exit 1
fi

SLUG=$(git remote get-url origin | sed -E 's#^.*bitbucket\.org[:/]##; s#\.git$##')
[ -n "$SLUG" ] || { echo "error: origin is not a bitbucket.org remote" >&2; exit 1; }

BODY=$(jq -Rs '{content: {raw: .}}' "$FILE")
RESP=$(curl --silent --show-error --fail-with-body --max-time 30 "${AUTH[@]}" \
  --header "Content-Type: application/json" --data "$BODY" \
  "https://api.bitbucket.org/2.0/repositories/${SLUG}/pullrequests/${PR}/comments")

echo "$RESP" | jq -r '"posted comment \(.id) on PR #'"$PR"': \(.links.html.href)"'
