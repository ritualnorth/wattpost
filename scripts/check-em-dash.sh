#!/usr/bin/env bash
# Reject em-dash (—) in source and docs.
# Loud LLM voice tell. Use comma, parenthesis, or colon instead.
#
# Whitelisted: a small set of intentional UI placeholders where the
# character is genuinely the right glyph (loading states like "—h —m").
set -u

# Files passed on argv from pre-commit framework.
status=0
for f in "$@"; do
  # Skip whitelisted paths (UI placeholders, third-party libs).
  case "$f" in
    solar_monitor/web/uPlot.iife.min.js) continue ;;
    solar_monitor/web/index.html)
      # The dashboard uses — as a loading-state placeholder in a few
      # tile labels. Cap a per-file allowance instead of blocking.
      count=$(grep -c '—' "$f" 2>/dev/null || echo 0)
      if [ "$count" -gt 40 ]; then
        echo "$f: $count em-dashes (limit 40 for known UI placeholders)" >&2
        status=1
      fi
      continue
      ;;
  esac
  if grep -nH '—' "$f"; then
    status=1
  fi
done
if [ "$status" -ne 0 ]; then
  echo "" >&2
  echo "Reject: em-dash (—) is an LLM voice tell. Use ',' '(' ')' or ':' instead." >&2
fi
exit "$status"
