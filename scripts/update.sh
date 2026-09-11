#!/usr/bin/env bash
# Full update from a machine that can reach vjudge (your own computer):
# discover new group contests, fetch standings, recompute, commit and push.
# The push then triggers the GitHub Actions workflow, which publishes the page.
#
# One-time setup (see README "Running the update from your computer"):
#   pip install -e .
#   put your vjudge Cookie header in .cookie and set cookie_file / user_agent in jollyrating.toml
# Then run this by hand or from cron/launchd:
#   ./scripts/update.sh
set -euo pipefail
cd "$(dirname "$0")/.."

git pull --rebase --quiet
if grep -Eq '^\s*group\s*=' jollyrating.toml; then
  python3 -m jollyrating discover --write || echo "discover found no new contests"
fi
python3 -m jollyrating run --ignore-missing
git add jollyrating.toml data out
if git diff --cached --quiet; then
  echo "nothing new to commit"
  exit 0
fi
git commit --quiet -m "Update ratings ($(date -u +%Y-%m-%d))"
git push --quiet
echo "pushed; GitHub Actions will publish the page in a minute"
