#!/usr/bin/env bash
# Move a reviewed ticket on and reassign it: to QA after a PASS, back to the developer
# after a FAIL.
#
#   bash jira_handoff.sh <ISSUE-KEY> [--mode qa|rework] [--assignee <user>]
#                        [--target <transition-or-status>]... [--dry-run]
#
# Modes (defaults from <repo>/.claude/jira-project.json):
#   qa      target(s) jira.qa_status, hop through jira.qa_path (default []), assign
#           jira.default_qa
#   rework  target(s) jira.rework_targets, hop through jira.rework_path (default []),
#           assignee REQUIRED (--assignee)
# All of these come from jira-project.json; there are no built-in status names.
#
# A target matches either a transition NAME or the status it leads TO, case-insensitively;
# targets are tried in order so a workflow that names the move differently per issue type
# (a "rework" transition for one, a plain status move for another) is one config. Jira only
# exposes moves from the CURRENT status, so when no target is reachable the script hops
# through the path statuses first. --target may repeat and overrides the config list.
#
# --dry-run resolves everything and prints the first hop and the assignee, writing nothing.
# Credentials: JIRA_TOKEN (bearer) or JIRA_USER + JIRA_PASS (basic), read from
# $JIRA_ENV_FILE, <repo>/.claude/jira.env, or ~/.claude/jira.env.

set -euo pipefail

KEY="${1:?usage: jira_handoff.sh <ISSUE-KEY> [--mode qa|rework] [--assignee <user>] [--target <t>]... [--dry-run]}"
shift
MODE="qa"; ASSIGNEE=""; TARGETS=(); DRY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --mode)     MODE="${2:?--mode needs qa|rework}"; shift 2 ;;
    --assignee) ASSIGNEE="${2:?--assignee needs a value}"; shift 2 ;;
    --target)   TARGETS+=("${2:?--target needs a value}"); shift 2 ;;
    --dry-run)  DRY=1; shift ;;
    *) echo "error: unknown argument: $1" >&2; exit 2 ;;
  esac
done
case "$MODE" in qa|rework) ;; *) echo "error: --mode must be qa or rework" >&2; exit 2 ;; esac

# shellcheck disable=SC1091
. "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
if [ "$MODE" = qa ]; then
  [ -z "$ASSIGNEE" ] && ASSIGNEE=$(cfg '.jira.default_qa // empty')
  [ ${#TARGETS[@]} -eq 0 ] && mapfile -t TARGETS < <(cfg '.jira.qa_status // empty | if type=="array" then .[] else . end')
  PATH_STATUSES=$(cfg '(.jira.qa_path // []) | .[]')
  [ ${#TARGETS[@]} -gt 0 ] || die "jira.qa_status is not set in $PROJECT_JSON (the status a PASS moves to)"
  [ -n "$ASSIGNEE" ] || die "jira.default_qa is not set in $PROJECT_JSON (who a PASS is assigned to); or pass --assignee"
else
  [ ${#TARGETS[@]} -eq 0 ] && mapfile -t TARGETS < <(cfg '(.jira.rework_targets // []) | .[]')
  PATH_STATUSES=$(cfg '(.jira.rework_path // []) | .[]')
  [ ${#TARGETS[@]} -gt 0 ] || die "jira.rework_targets is not set in $PROJECT_JSON (transition names or statuses a FAIL moves to, in order)"
  [ -n "$ASSIGNEE" ] || die "rework mode needs --assignee <developer> (the person who put the branch/PR on the ticket)"
fi

api() { jira_api "$@"; }
lc() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]'; }

current_status() { api GET "/rest/api/2/issue/$KEY?fields=status" | jq -r '.fields.status.name'; }

# Does the current status already satisfy any target (by status name)?
at_target() {
  local cur; cur=$(lc "$1"); local t
  for t in "${TARGETS[@]}"; do [ "$(lc "$t")" = "$cur" ] && return 0; done
  return 1
}

# Print "id<TAB>name<TAB>to" for the first transition whose NAME or destination matches
# $1 (case-insensitive) in the transitions JSON on stdin; nothing if none.
match_transition() {
  jq -r --arg t "$(lc "$1")" \
    '[.transitions[] | select(((.name | ascii_downcase) == $t) or ((.to.name | ascii_downcase) == $t))][0]
     | select(. != null) | "\(.id)\t\(.name)\t\(.to.name)"'
}

# Pick the transition to take from the current status: the first reachable target (in
# order), else the first allowed intermediate (in path order) not yet visited.
pick_transition() {
  local visited="$1" json t hit
  json=$(api GET "/rest/api/2/issue/$KEY/transitions")
  for t in "${TARGETS[@]}"; do
    hit=$(printf '%s' "$json" | match_transition "$t")
    if [ -n "$hit" ]; then printf '%s\n' "$hit"; return 0; fi
  done
  while IFS= read -r t; do
    [ -n "$t" ] || continue
    case "$visited" in *"|$(lc "$t")|"*) continue ;; esac
    hit=$(printf '%s' "$json" | match_transition "$t")
    if [ -n "$hit" ]; then printf '%s\n' "$hit"; return 0; fi
  done <<< "$PATH_STATUSES"
  echo "error: no transition from '$(current_status)' toward '${TARGETS[*]}' on $KEY. Available:" >&2
  printf '%s' "$json" | jq -r '.transitions[] | "  \(.name)  -> \(.to.name)"' >&2
  return 1
}

# Resolve the assignee to the identifier Jira wants: `name` on Server/Data Center,
# `accountId` on Cloud. Exact email, then exact username, else the sole match.
resolve_user() {
  local json q
  q=$(printf '%s' "$ASSIGNEE" | jq -sRr @uri)
  if [ "$JIRA_DEPLOYMENT" = cloud ]; then
    json=$(api GET "/rest/api/2/user/search?query=$q&maxResults=10")
    printf '%s' "$json" | jq -r --arg w "$(lc "$ASSIGNEE")" '
      ([.[] | select((.emailAddress // "" | ascii_downcase) == $w)][0].accountId)
      // ([.[] | select((.displayName // "" | ascii_downcase) == $w)][0].accountId)
      // (if length == 1 then .[0].accountId else empty end) // empty'
  else
    json=$(api GET "/rest/api/2/user/search?username=$q&maxResults=10")
    printf '%s' "$json" | jq -r --arg w "$(lc "$ASSIGNEE")" '
      ([.[] | select((.emailAddress // "" | ascii_downcase) == $w)][0].name)
      // ([.[] | select((.name // "" | ascii_downcase) == $w)][0].name)
      // (if length == 1 then .[0].name else empty end) // empty'
  fi
}
assignee_body() {
  if [ "$JIRA_DEPLOYMENT" = cloud ]; then jq -nc --arg n "$USERNAME" '{accountId: $n}'; else jq -nc --arg n "$USERNAME" '{name: $n}'; fi
}

STATUS=$(current_status)
USERNAME=$(resolve_user)
[ -n "$USERNAME" ] || { echo "error: could not resolve assignee '$ASSIGNEE' to exactly one Jira user" >&2; exit 1; }

if [ "$DRY" = 1 ]; then
  echo "DRY RUN  $KEY is '$STATUS'; mode $MODE; targets: ${TARGETS[*]}; assignee resolves to a single user"
  if ! at_target "$STATUS"; then
    T=$(pick_transition "|$(lc "$STATUS")|")
    echo "DRY RUN  first hop: '$(printf '%s' "$T" | cut -f2)' -> $(printf '%s' "$T" | cut -f3) (id $(printf '%s' "$T" | cut -f1))"
  else
    echo "DRY RUN  already at target; only the assignment would change"
  fi
  exit 0
fi

VISITED="|$(lc "$STATUS")|"
HOPS=0; DONE=0
while [ "$DONE" = 0 ] && ! at_target "$STATUS"; do
  [ "$HOPS" -lt 4 ] || { echo "error: gave up after $HOPS hops; $KEY is '$STATUS'" >&2; exit 1; }
  T=$(pick_transition "$VISITED")
  TID=$(printf '%s' "$T" | cut -f1); TNAME=$(printf '%s' "$T" | cut -f2); TTO=$(printf '%s' "$T" | cut -f3)
  api POST "/rest/api/2/issue/$KEY/transitions" --data "$(jq -nc --arg id "$TID" '{transition: {id: $id}}')" >/dev/null \
    || { echo "error: transition '$TNAME' failed on $KEY (workflow may require fields on this step)" >&2; exit 1; }
  echo "moved $KEY: '$STATUS' -> '$TTO' (via '$TNAME')"
  # a transition whose NAME matched a target is the destination,
  # even though the status it lands on has a different name
  for t in "${TARGETS[@]}"; do [ "$(lc "$t")" = "$(lc "$TNAME")" ] && DONE=1; done
  STATUS=$(current_status); VISITED="$VISITED$(lc "$STATUS")|"; HOPS=$((HOPS+1))
done
[ "$HOPS" = 0 ] && echo "$KEY already '$STATUS'"

api PUT "/rest/api/2/issue/$KEY/assignee" --data "$(assignee_body)" >/dev/null \
  || { echo "error: assign failed on $KEY -- the user may lack 'Assignable User' permission" >&2; exit 1; }
echo "assigned $KEY to: $(api GET "/rest/api/2/issue/$KEY?fields=assignee" | jq -r '.fields.assignee.displayName // "unassigned"')"
