#!/usr/bin/env bash
# Reject AI-attribution trailers in commit messages.
#
# pre-commit framework passes the commit-message file path as $1 when run
# with --hook-type commit-msg.
set -u

msg_file="$1"

# Patterns: "Co-Authored-By: Claude ...", "Generated with Claude Code",
# "🤖 Generated", any "@anthropic" / "Claude (Opus/Sonnet/Haiku)" trailer.
patterns=(
  'Co-Authored-By:.*Claude'
  'Co-Authored-By:.*Anthropic'
  'Generated with.*Claude'
  '🤖 Generated'
  'noreply@anthropic'
  'Claude.*<.*@anthropic'
)

status=0
for p in "${patterns[@]}"; do
  if grep -iE "$p" "$msg_file"; then
    status=1
  fi
done

if [ "$status" -ne 0 ]; then
  echo "" >&2
  echo "Reject: AI-attribution trailer in commit message." >&2
  echo "        Strip the Co-Authored-By / Generated-with line and retry." >&2
fi
exit "$status"
