#!/usr/bin/env bash
# Post a comment on the pull request for this repository's git host (Bitbucket Cloud or
# GitHub), using the same credentials file as the rest of the skill.
#
#   bash post_pr_comment.sh <pr-id> <markdown-file>
#
# Only run this after the user has explicitly confirmed the post.

set -euo pipefail
PR="${1:?usage: post_pr_comment.sh <pr-id> <markdown-file>}"
FILE="${2:?usage: post_pr_comment.sh <pr-id> <markdown-file>}"
[ -f "$FILE" ] || { echo "error: no such file: $FILE" >&2; exit 1; }
# shellcheck disable=SC1091
. "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
[ -n "$GIT_HOST" ] || die "cannot tell the git host from remote '$GIT_REMOTE' ($REMOTE_URL) -- set git.host in $PROJECT_JSON"
[ -n "$GIT_SLUG" ] || die "cannot read owner/repo from remote '$GIT_REMOTE'"

case "$GIT_HOST" in
  bitbucket)
    RESP=$(host_api POST "/repositories/${GIT_SLUG}/pullrequests/${PR}/comments" --data "$(jq -Rs '{content: {raw: .}}' "$FILE")")
    echo "$RESP" | jq -r '"posted comment \(.id) on PR #'"$PR"': \(.links.html.href)"' ;;
  github)
    RESP=$(host_api POST "/repos/${GIT_SLUG}/issues/${PR}/comments" --data "$(jq -Rs '{body: .}' "$FILE")")
    echo "$RESP" | jq -r '"posted comment \(.id) on PR #'"$PR"': \(.html_url)"' ;;
  *) die "unsupported git host '$GIT_HOST' (supported: bitbucket, github)" ;;
esac
