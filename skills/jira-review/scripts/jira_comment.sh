#!/usr/bin/env bash
# Post a comment on a Jira issue.
#
#   bash jira_comment.sh <ISSUE-KEY> <text | @file> [--dry-run]
#
# Text starting with "@" is read from that file. --dry-run prints the payload and exits.
# Credentials and the Jira URL come from lib/common.sh (jira.env + jira-project.json).

set -euo pipefail
KEY="${1:?usage: jira_comment.sh <ISSUE-KEY> <text | @file> [--dry-run]}"
TEXT="${2:?usage: jira_comment.sh <ISSUE-KEY> <text | @file> [--dry-run]}"
DRY=0; [ "${3:-}" = "--dry-run" ] && DRY=1
if [ "${TEXT#@}" != "$TEXT" ]; then
  FILE="${TEXT#@}"; [ -f "$FILE" ] || { echo "error: no such file: $FILE" >&2; exit 1; }
  TEXT=$(cat "$FILE")
fi
# shellcheck disable=SC1091
. "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

BODY=$(jq -n --arg b "$TEXT" '{body: $b}')
if [ "$DRY" = 1 ]; then echo "DRY RUN -> POST ${JIRA_BASE_URL}/rest/api/2/issue/${KEY}/comment"; echo "$BODY" | jq .; exit 0; fi
RESP=$(jira_api POST "/rest/api/2/issue/${KEY}/comment" --data "$BODY")
echo "$RESP" | jq -r '"posted comment \(.id) on '"$KEY"' at \(.created)"'
