# GRU history storage migration — 2026-10-21

Completed migration from the original platform directories:

- 12 historical export parts: 2,404,278,277 bytes.
- 781 obsolete binary outputs: 6,922,829,590 bytes.
- Total logical file bytes released: **9,327,107,867** (HOME 5,760,131,779; DATA 3,566,976,088).

Raw data panels, all A1 production model runs, the current rank baseline, active training outputs, source code, configuration and logs were retained. Training recipes were unchanged. Storage archival does not validate historical performance.

Verification used source/archive SHA256, preserved scratch copies, and GitHub server asset SHA256 digests. Full HTTP payload readback was not performed. Platform GitHub credentials were removed after each completed upload batch.

## Restore

- Historical export parts: release `gru-history-20261012-16-archive-20261021`; inventory in `archives/gru_results_20261021/ARCHIVE_MANIFEST.json`.
- Obsolete model/prediction outputs: release `gru-obsolete-outputs-archive-20261021`; inventory in `archives/gru_obsolete_outputs_20261021/BULK_MANIFEST.json`.

Each part is an independent tar.gz. Verify the downloaded part SHA256, extract into an empty staging folder, then copy only needed files into their matching HOME/DATA layout. Verify contained file SHA256 and do not overwrite existing experiment files automatically.

The platform also retains temporary recovery copies on its local scratch disk. Those copies are convenient recovery material; GitHub assets and manifests provide the persistent archive. Shared filesystem free-space totals can change due to other users' writes.
