#!/usr/bin/env bash
# Save a database into the 'deepened' release without erasing anyone else's save.
#   release_save.sh <repo> <run-id> <base.db> <base-asset-id-file> <this-run.db>
# This run's changes (this-run minus base) are merged into whatever the release holds now, row by
# row (sonic.db_merge); the result is uploaded under a name only this run uses and swapped in only
# if the release's file has not changed since this run looked. Prints the outcome; exits 1 if the
# save did not land, so callers can fail loudly rather than report success over lost work.
set -u
R="$1"; RID="$2"; BASE="$3"; BASEID="$4"; DB="$5"
if cmp -s "$DB" "$BASE"; then echo "database: unchanged by this run"; exit 0; fi
for j in 1 2 3 4 5; do
  CUR=$(gh api "repos/$R/releases/tags/deepened" --jq '.assets[] | select(.name=="sonic.db") | .id' 2>/dev/null)
  rm -rf /tmp/rs_now /tmp/rs_up; mkdir -p /tmp/rs_now /tmp/rs_up
  if [ -n "$CUR" ] && [ "$CUR" = "$(cat "$BASEID" 2>/dev/null)" ]; then
    cp "$DB" "/tmp/rs_up/sonic.db.$RID"; echo "database: nothing else landed since this run began"
  else
    gh release download deepened --repo "$R" --pattern sonic.db --dir /tmp/rs_now --clobber || { sleep 15; continue; }
    python -m sonic.db_merge --base "$BASE" --ours "$DB" --theirs /tmp/rs_now/sonic.db --out "/tmp/rs_up/sonic.db.$RID" || { sleep 15; continue; }
  fi
  gh release upload deepened "/tmp/rs_up/sonic.db.$RID" --repo "$R" --clobber || { sleep 15; continue; }
  NEW=$(gh api "repos/$R/releases/tags/deepened" --jq ".assets[] | select(.name==\"sonic.db.$RID\") | .id")
  NOW=$(gh api "repos/$R/releases/tags/deepened" --jq '.assets[] | select(.name=="sonic.db") | .id')
  if [ "$NOW" != "$CUR" ]; then
    echo "another save landed during this one; merging again"
    gh api -X DELETE "repos/$R/releases/assets/$NEW" >/dev/null; sleep 5; continue
  fi
  [ -n "$NOW" ] && gh api -X DELETE "repos/$R/releases/assets/$NOW" >/dev/null
  if gh api -X PATCH "repos/$R/releases/assets/$NEW" -f name=sonic.db >/dev/null; then
    echo "database: saved to the release on attempt $j"; exit 0
  fi
done
echo "::error::this run's database changes were not saved"; exit 1
