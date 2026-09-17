#!/usr/bin/env bash
# Post a comment on the pull request for this repository's git host (Bitbucket Cloud or
# GitHub), using the same credentials file as the rest of the skill.
#
#   bash post_pr_comment.sh <pr-id> <markdown-file> [--repo <owner/repo>]
#
# --repo  the repository the PR belongs to, when the ticket's work spans several
#         repositories (repos[].slug). Default: this checkout's remote. A PR number is
#         only unique within its repository, so always pass it for multi-repository tickets.
#
# Only run this after the user has explicitly confirmed the post.

set -euo pipefail
PR="${1:?usage: post_pr_comment.sh <pr-id> <markdown-file> [--repo <owner/repo>]}"
FILE="${2:?usage: post_pr_comment.sh <pr-id> <markdown-file> [--repo <owner/repo>]}"
shift 2
while [ $# -gt 0 ]; do
  case "$1" in
    --repo) export REVIEW_REPO_SLUG="${2:?--repo needs owner/repo}"; shift 2 ;;
    --repo=*) export REVIEW_REPO_SLUG="${1#--repo=}"; shift ;;
    *) echo "error: unknown argument: $1" >&2; exit 2 ;;
  esac
done
[ -f "$FILE" ] || { echo "error: no such file: $FILE" >&2; exit 1; }
# shellcheck disable=SC1091
. "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
[ -n "$GIT_HOST" ] || die "cannot tell the git host from remote '$GIT_REMOTE' ($REMOTE_URL) -- set git.host in $PROJECT_JSON"
[ -n "$GIT_SLUG" ] || die "cannot read owner/repo from remote '$GIT_REMOTE'"

case "$GIT_HOST" in
  bitbucket)
    RESP=$(host_api POST "/repositories/${GIT_SLUG}/pullrequests/${PR}/comments" --data "$(jq -Rs '{content: {raw: .}}' "$FILE")")
    echo "$RESP" | jq -r '"posted comment \(.id) on PR #'"$PR"' ('"$GIT_SLUG"'): \(.links.html.href)"' ;;
  github)
    RESP=$(host_api POST "/repos/${GIT_SLUG}/issues/${PR}/comments" --data "$(jq -Rs '{body: .}' "$FILE")")
    echo "$RESP" | jq -r '"posted comment \(.id) on PR #'"$PR"' ('"$GIT_SLUG"'): \(.html_url)"' ;;
  *) die "unsupported git host '$GIT_HOST' (supported: bitbucket, github)" ;;
esac
