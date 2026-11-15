#!/usr/bin/env bash
# Check the locked dependencies against known-vulnerability advisories (handbook 13 §13.10).
# Usage: scripts/dependency-audit.sh          -> Python and JS
#        scripts/dependency-audit.sh python   -> uv.lock only
#        scripts/dependency-audit.sh js       -> pnpm-lock.yaml only
# Run by CI (job "Dependency audit") on every pull request and push to main, and weekly by
# dependency-audit.yml, so advisories published after a merge still surface. Needs uv and pnpm, and
# network access to PyPI and the npm registry; nothing is installed into the repository.
#
# Python: the runtime dependencies of every uv workspace member (no dev group), exported from
#   uv.lock with their hashes and checked by pip-audit against the PyPI advisory database.
#   pip-audit has no severity filter, so any known vulnerability fails.
#   Accepted advisories: .github/pip-audit-ignore.txt (one ID per line, with the reason).
# JS: the production dependencies ("dependencies" and "optionalDependencies") of every pnpm
#   workspace package, read from pnpm-lock.yaml. Fails at audit.level in pnpm-workspace.yaml
#   (high, so high and critical). Accepted advisories: audit.ignore in pnpm-workspace.yaml.
#
# An advisory is accepted only with a reason and a review date next to it, in the same pull
# request that a reviewer approves; the fix is still to upgrade.
set -euo pipefail

cd "$(dirname "$0")/.."

# An exact, released version (the project adopts releases at least two weeks old).
PIP_AUDIT_VERSION="2.10.1"
PYTHON_IGNORE_FILE=".github/pip-audit-ignore.txt"

python_audit() {
  local requirements ignore=() id
  requirements="$(mktemp)"
  # The workspace members themselves (-e ./backend/api ...) are our code, not advisories' subjects.
  uv export --frozen --all-packages --all-extras --no-dev --no-emit-workspace \
    --format requirements.txt --quiet --output-file "$requirements"
  if [[ -f "$PYTHON_IGNORE_FILE" ]]; then
    while read -r id _; do
      ignore+=(--ignore-vuln "$id")
    done < <(sed -e 's/#.*//' -e '/^[[:space:]]*$/d' "$PYTHON_IGNORE_FILE")
  fi
  echo "pip-audit $PIP_AUDIT_VERSION: $(grep -c '==' "$requirements") locked packages from uv.lock"
  # --disable-pip and --require-hashes: audit exactly the hashed pins, with no resolution step.
  # The IDs pip-audit prints (PYSEC-, CVE-, GHSA-) all work in the ignore file; --aliases shows them.
  local status=0
  uvx "pip-audit@$PIP_AUDIT_VERSION" --disable-pip --require-hashes --strict \
    --progress-spinner off --aliases on ${ignore[@]+"${ignore[@]}"} \
    --requirement "$requirements" || status=$?
  rm -f "$requirements"
  return "$status"
}

js_audit() {
  echo "pnpm audit (production dependencies, pnpm-lock.yaml)"
  pnpm audit --prod
}

target="${1:-all}"
status=0
case "$target" in
  python) python_audit || status=1 ;;
  js) js_audit || status=1 ;;
  all)
    python_audit || status=1
    js_audit || status=1
    ;;
  *)
    echo "Usage: $0 [python|js]" >&2
    exit 2
    ;;
esac

if ((status)); then
  echo
  echo "Known vulnerabilities found. Upgrade the package (uv lock --upgrade-package <name>, or the"
  echo "version in package.json), or accept the advisory with a reason (see the top of $0)."
fi
exit "$status"
