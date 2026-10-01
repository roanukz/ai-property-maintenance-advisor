# Synthetic inputs for test_build_demo.py

Every run, record, URL and number here is synthetic (example.com hosts, build 0123456789abcdef).
The test copies `data/` to a temp folder, builds the ledger from `data/ledger_rows.json` and points
the builder's config at the copy. The plate photo hashes are those of the published demo-assets
photos, and the property records cited by the history run are the tracked synthetic seed's.

Every run record carries a synthetic `safety` section, as runs on the build with the safety check do. The vague
question (t-5a00000000000003) has a flag Jev raised that the writer left off, at 0.87; the air conditioner's first
question (t-5a00000000000004) has a flag the writer set; the new symptom (t-5a00000000000005) records a failed
Jev call (status `partial`), so its brief carries the notice line. Their Jev calls are in `ledger_rows.json` as
node `safety_check`, provider `typesafe`, and are counted in each run's `cost_usd`. The other runs record a
skipped check, since their briefs have no steps to try first.
