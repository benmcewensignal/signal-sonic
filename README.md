# Signal — sonic momentum layer (pilot build)

Weekly batch pipeline: fingerprint what each scene sounds like, compute
drift / convergence / novelty / divergence, emit every call as a claim
with a resolution rule, resolve claims as they mature, publish the record.

## Layout
    sonic/analyser.py     the seam. StubAnalyser (offline, deterministic),
                          CyaniteAnalyser (needs CYANITE_TOKEN; query shape
                          to be verified against current Cyanite docs)
    sonic/ingest.py       FixtureSource (json), BeatportSource (skeleton —
                          needs a fetch method decision + network)
    sonic/store.py        SQLite: tracks, track_scenes, scene_weeks, claims
    sonic/aggregate.py    weekly fingerprints, chart-weighted AND flat
    sonic/derivatives.py  the four detectors, claims out
    sonic/run_week.py     the batch orchestrator
    tests/run_tests.py    synthetic 30-week pilot with planted truths; 12 checks
    scene_map.json        beatport genre -> signal scene (verify slugs)

## Run the offline demo
    python3 tests/run_tests.py
Builds a 30-week 4-scene fixture with one planted drifter and one planted
convergence pair; asserts the system finds exactly those and nothing else,
stays silent while the baseline seasons, filters analyser vintages, and
keeps claims immutable.

## Weekly run (fixture)
    python3 -m sonic.run_week --week 2026-W35 \
        --scenes uk-garage-speed-garage,uk-funky-gqom,afro-house \
        --fixture path/to/sightings.json --analyser stub --db sonic.db

## Running it on GitHub Actions (no laptop required)
The workflow in .github/workflows/sonic.yml does everything:
1. Phone: repo Settings → Secrets and variables → Actions → add
   BEATPORT_USERNAME and BEATPORT_PASSWORD (or BEATPORT_TOKEN).
2. Actions tab → sonic → Run workflow → mode: genres. Read the genre
   list in the log; note the ids for garage / funky-adjacent / afro.
3. Edit scene_map.json in the GitHub mobile editor: replace the three
   PLACEHOLDER ids with real ones. Commit.
4. Run workflow → mode: chart with one genre id — confirm tracks and
   preview urls in the log.
5. Run workflow → mode: week for the first real run. sonic.db commits
   itself back to the repo when it finishes.
6. Do nothing. Sundays 03:00 UTC it runs itself; each run's report is
   in the Actions log and the db history is the git history.
Caveat: Beatport may treat datacenter IPs differently from home IPs.
If auth or fetch fails ONLY in Actions, fall back to a Railway worker
or a machine of your own — the code is identical either way.

## Wiring by hand (the Mac path, same steps, local)
1. Credentials: export BEATPORT_USERNAME + BEATPORT_PASSWORD (ordinary
   account; auth uses the docs-frontend client_id, the beets-beatport4
   route — grey, read-only, your ToS call) or BEATPORT_TOKEN directly.
2. Verify taxonomy: `python -m sonic.beatport genres`, then replace the
   PLACEHOLDER ids in scene_map.json. This is mandatory: the ids there
   are guesses.
3. One chart by hand: `python -m sonic.beatport chart --genre-id <id>` —
   confirm tracks + preview urls come back.
4. First real week:
   `python -m sonic.run_week --week 2026-W36 \
      --scenes uk-garage-speed-garage,uk-funky-gqom,afro-house \
      --source beatport --analyser local --db sonic.db`
   Previews are downloaded, analysed with the local librosa analyser
   (tempo/key/energy + spectral embedding), and deleted. Vectors only.
5. Cron (Sunday 03:00): 
   `0 3 * * 0 cd ~/signal-sonic && python3 -m sonic.run_week --week $(date +\%G-W\%V) --scenes ... --source beatport --analyser local --db sonic.db >> runs.log 2>&1`
6. Bookings: pass --bookings momentum.json ({scene: z}) exported from
   the existing Signal weekly job to switch on divergence detection.
   Can trail everything else by weeks.
7. Cyanite (impl A) is now the LATER upgrade, not the start: 290€/mo
   API tier. Only worth testing if local embeddings prove too noisy —
   which the silent quarter will tell you.

## Calibration warning (the one that matters)
DRIFT_THRESHOLD (0.05) and friends are calibrated against the STUB's
measured noise floor (0.010). Real audio embeddings have a different
noise floor. Before any claim ships on real data: run 8+ weeks silent,
measure week-to-week fingerprint distance per scene on clean history,
set thresholds ~5x that floor. The harness pattern in tests/ is how.

## Pilot plan
Three scenes, one quarter, claims written from week one, nothing
published. The question the quarter answers: does sound lead bookings
by enough weeks to be worth paying for.
