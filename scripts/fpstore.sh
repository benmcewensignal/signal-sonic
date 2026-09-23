#!/usr/bin/env bash
# The fingerprint store, kept in the fp-store release as compressed parts under GitHub's 2 GiB
# per-file cap, named by the run that wrote them, with a small pointer file (fpstore.current:
# "<set> <parts>") naming the current set. A new set is uploaded whole before the pointer moves,
# and the old set is deleted only after, so a reader never sees half a store.
#   fpstore.sh fetch <repo> <out.db>     reassemble the current store (or the old single file)
#   fpstore.sh save  <repo> <in.db> <set-id>   exits 0 only if the new set is in place
set -u
cmd="$1"; R="$2"; F="$3"
pre(){ gh api "repos/$R/releases/tags/fp-store" --jq ".assets[] | select(.name|startswith(\"$1\")) | .id" 2>/dev/null; }
exact(){ gh api "repos/$R/releases/tags/fp-store" --jq ".assets[] | select(.name==\"$1\") | .id" 2>/dev/null; }
if [ "$cmd" = fetch ]; then
  rm -rf /tmp/fpf; mkdir -p /tmp/fpf
  if gh release download fp-store --repo "$R" --pattern fpstore.current --dir /tmp/fpf --clobber 2>/dev/null && [ -s /tmp/fpf/fpstore.current ]; then
    SET=$(cut -d' ' -f1 /tmp/fpf/fpstore.current); N=$(cut -d' ' -f2 /tmp/fpf/fpstore.current)
    for i in 1 2 3; do gh release download fp-store --repo "$R" --pattern "fpstore-$SET.gz.*" --dir /tmp/fpf --clobber && break; sleep 20; done
    if [ "$(ls /tmp/fpf/fpstore-$SET.gz.* 2>/dev/null | wc -l)" != "$N" ]; then echo "fpstore: set $SET incomplete"; exit 1; fi
    cat /tmp/fpf/fpstore-$SET.gz.* | gunzip > "$F" && echo "fpstore: fetched set $SET ($N part(s))"; exit $?
  fi
  gh release download fp-store --repo "$R" --pattern fingerprints.db --dir /tmp/fpf --clobber 2>/dev/null && mv /tmp/fpf/fingerprints.db "$F" && echo "fpstore: fetched the single-file store" && exit 0
  exit 1
fi
if [ "$cmd" = save ]; then
  SET="$4"; rm -rf /tmp/fps; mkdir -p /tmp/fps
  # fold any write-ahead log into the file and check it: a store saved without its log was found
  # malformed on 23 September, and a damaged store must never replace a sound one
  CHK=$(python3 -c "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute('pragma wal_checkpoint(truncate)'); c.execute('pragma journal_mode=delete'); print(c.execute('pragma quick_check').fetchone()[0])" "$F" 2>&1)
  if [ "$CHK" != "ok" ]; then echo "fpstore: the store fails its integrity check ($CHK); not saved"; exit 1; fi
  gzip -1 -c "$F" | split -b 1900m -d -a 2 - "/tmp/fps/fpstore-$SET.gz."
  N=$(ls /tmp/fps | wc -l); echo "fpstore: $(du -m "$F" | cut -f1) MB as $N compressed part(s), $(du -cm /tmp/fps/* | tail -1 | cut -f1) MB"
  for p in /tmp/fps/*; do
    ok=0; for i in 1 2 3; do gh release upload fp-store "$p" --repo "$R" --clobber && { ok=1; break; }; sleep 20; done
    if [ $ok = 0 ]; then echo "fpstore: part $(basename "$p") failed; removing this set"; for id in $(pre "fpstore-$SET."); do gh api -X DELETE "repos/$R/releases/assets/$id" >/dev/null; done; exit 1; fi
  done
  OLDSET=$(gh release download fp-store --repo "$R" --pattern fpstore.current --output - 2>/dev/null | cut -d' ' -f1)
  echo "$SET $N" > "/tmp/fps/fpstore.current.$SET"
  gh release upload fp-store "/tmp/fps/fpstore.current.$SET" --repo "$R" --clobber || exit 1
  for id in $(exact fpstore.current); do gh api -X DELETE "repos/$R/releases/assets/$id" >/dev/null; done
  NEW=$(exact "fpstore.current.$SET"); [ -n "$NEW" ] || { echo "fpstore: new pointer not found"; exit 1; }
  gh api -X PATCH "repos/$R/releases/assets/$NEW" -f name=fpstore.current >/dev/null || exit 1
  if [ -n "$OLDSET" ] && [ "$OLDSET" != "$SET" ]; then for id in $(pre "fpstore-$OLDSET."); do gh api -X DELETE "repos/$R/releases/assets/$id" >/dev/null; done; fi
  for id in $(exact fingerprints.db); do gh api -X DELETE "repos/$R/releases/assets/$id" >/dev/null; done
  for id in $(pre "fpstore.current."); do gh api -X DELETE "repos/$R/releases/assets/$id" >/dev/null; done
  echo "fpstore: set $SET is current"; exit 0
fi
exit 2
