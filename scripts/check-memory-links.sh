#!/usr/bin/env bash
# Reject [[memory-name]] / [[memory_name]] patterns.
# Used by some LLM tooling to reference internal notes. Should never
# appear in committed source. Use plain prose or a concrete file path.
set -u

status=0
for f in "$@"; do
  if grep -nHE '\[\[[a-z][a-z_-]+\]\]' "$f"; then
    status=1
  fi
done
if [ "$status" -ne 0 ]; then
  echo "" >&2
  echo "Reject: [[memory-name]] is an LLM memory-link. Replace with prose or a path." >&2
fi
exit "$status"
