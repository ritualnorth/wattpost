#!/usr/bin/env bash
# Reject AI-attribution trailers in commit messages.
#
# pre-commit framework passes the commit-message file path as $1 when run
# with --hook-type commit-msg.
set -u

msg_file="$1"

# Catch "Co-Authored-By: <any AI tool>", "Generated with <AI>", any
# vendor-bot email domain. Pattern set is broad so this stays useful as
# new tools land.
patterns=(
  'Co-Authored-By:.*\b(AI|LLM|Bot|GPT|Codex|Copilot|Cursor|Claude|Gemini|Llama)\b'
  'Generated (with|by) .*\b(AI|LLM|GPT|Codex|Copilot|Cursor|Claude|Gemini)\b'
  'noreply@(anthropic|openai|github\.copilot)'
  '🤖 Generated'
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
