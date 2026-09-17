# Corpus restore

The deepened release was overwritten on 2026-09-17 at 09:21 UTC with a database built from
the repository copy: 22,227 records where the release held 50,482. The 28,255 records lost were
all at analyser version 2, the old measurement, but they are the corpus record of what exists.

This branch carries the full database in five parts, reassembled and verified by the
restore-corpus workflow, which publishes it back to the release. sha256 of the whole file is in
sonic.db.sha256.
