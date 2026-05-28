#!/usr/bin/env bash
# Reject "Why this matters" / "Why this vendor matters" / "Strategic context"
# style preambles in docstrings + module headers.
#
# These multi-paragraph rationale blocks are a common LLM voice tell.
# State the fact, drop the framing.
set -u

status=0
PATTERN='^[[:space:]]*(#|"""|\*)?[[:space:]]*(Why this (matters|vendor|driver|file|module)|Strategic context|Big picture context|At a high level)[[:space:]:]'

for f in "$@"; do
  if grep -nHE "$PATTERN" "$f"; then
    status=1
  fi
done
if [ "$status" -ne 0 ]; then
  echo "" >&2
  echo "Reject: 'Why this matters' / 'Strategic context' preamble." >&2
  echo "        State the fact, drop the framing." >&2
fi
exit "$status"
