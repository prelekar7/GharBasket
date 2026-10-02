#!/usr/bin/env bash
# Copy AGENTS.md (the single source of truth) into every AI-tool instruction file.
# Usage: scripts/sync-agent-docs.sh          -> write copies
#        scripts/sync-agent-docs.sh --check  -> exit 1 if any copy differs (CI / pre-commit)
set -euo pipefail

cd "$(dirname "$0")/.."
source_file="AGENTS.md"
targets=(CLAUDE.md GEMINI.md CODEBUDDY.md QODER.md .cursorrules .windsurfrules)

if [[ "${1:-}" == "--check" ]]; then
  stale=()
  for t in "${targets[@]}"; do
    cmp -s "$source_file" "$t" || stale+=("$t")
  done
  if (( ${#stale[@]} )); then
    echo "These files differ from AGENTS.md: ${stale[*]}"
    echo "Edit AGENTS.md only, then run: just sync-agent-docs"
    exit 1
  fi
  echo "AI-tool instruction files are in sync with AGENTS.md"
  exit 0
fi

for t in "${targets[@]}"; do
  cp "$source_file" "$t"
done
echo "Synced AGENTS.md -> ${targets[*]}"
