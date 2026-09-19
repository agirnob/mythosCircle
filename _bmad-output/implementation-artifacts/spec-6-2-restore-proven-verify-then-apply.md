---
title: '6-2 Restore Proven — Verify-Then-Apply'
type: 'feature'
created: '2026-09-20'
status: 'done'
baseline_commit: 'e33396c676209f61c54edb90637273c786d1ea9b'
review_loop_iteration: 0
context:
  - '_bmad-output/implementation-artifacts/epic-6-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `deploy/restore.sh` blind-applies a snapshot — no checksum or integrity verification, a non-atomic media swap, no ownership fix-up, and no restore path for the deployed docker stack. A corrupted snapshot can silently restore a broken world (AR30/NFR5). Story 6-1 already ships the trust chain (`manifest.json.sha256`); 6-2 consumes it.

**Approach:** add a stdlib Python restore core (`backend/app/core/restore.py`, mirroring `backup.py`) that verifies the snapshot trust chain + DB integrity before any apply, then applies DB and media atomically (temp + rename); rework `deploy/restore.sh` (systemd) onto it with stop/apply/start + `chown` back to the service user; add `deploy/restore.docker.sh` for the deployed laptop stack (the beta gate must be provable on the deployed topology). Pin corrupt-vs-clean behavior in pytest.

## Boundaries & Constraints

**Always:**
- Verify strictly before apply: `manifest.json.sha256` anchors `manifest.json`; `db.sha256` sidecar and manifest `db.sha256`/`archive_sha256` re-check their artifacts; SQLite `PRAGMA integrity_check` on the snapshot DB. Any mismatch, missing artifact, or unparseable manifest → hard fail (nonzero exit, message naming the artifact) with zero writes to the destination.
- Apply DB via the sqlite online backup API to a temp file in the destination directory, then atomic `os.replace`; remove stale `-wal`/`-shm` siblings after the swap. Apply media via temp-dir extraction + directory swap; on failure restore the pre-swap tree. Never a torn DB, never a half media tree at the live path.
- Restore core is stdlib-only (`sqlite3`, `tarfile`, `hashlib`, `json`), env/CLI contract mirrors `backup.py` (`MYTHOSCIRCLE_DATA_DIR` etc.); runs on host python and inside the api container.
- Destination DB filename comes from the manifest `db.file` — faithful to the snapshot (dev DB is `mythos.db`, not `mythoscircle.db`).
- Keep the `restore.sh <snapshot-dir>` invocation compatible (systemd topology); the docker wrapper uses the same core.

**Ask First:** none anticipated (docker wrapper is in scope by owner decision at CHECKPOINT 1; confirmation prompt stays deferred from spec-1-1).

**Never:**
- No raw WAL-file restore — SQLite backup API only (AR13).
- No skip-verification flags, no partial apply on verify failure.
- No changes to the 6-1 snapshot format; restore consumes it as-is.
- No remote/networked restore, no restore UI, no interactive confirmation.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| HAPPY_PATH | clean snapshot dir (db + sidecar + manifest + media) | verify ok; apply swaps DB + media; post-apply integrity_check ok with snapshot row count | N/A |
| BIT_ROT_DB | snapshot db bytes differ from sidecar/manifest hashes | verify fails before apply; live DB + media byte-unchanged | nonzero exit naming the artifact |
| TAMPERED_MANIFEST | manifest.json.sha256 mismatch (or sidecar missing) | trust chain broken → fail; nothing applied | nonzero exit naming the sidecar/manifest |
| TORN_ARCHIVE | media.tar.gz hash ≠ manifest archive_sha256 | verify fails; nothing applied | nonzero exit naming the archive |
| CORRUPT_DB_STRUCTURE | hashes ok but integrity_check ≠ ok | fail at verify; nothing applied | nonzero exit reporting integrity_check result |
| NO_MEDIA_SNAPSHOT | manifest media:null, no archive | skip media apply; existing media tree untouched | N/A |
| MISSING_ARTIFACT | snapshot dir missing/empty/partial | fail naming the first missing required file | nonzero exit |
| APPLY_MEDIA_FAILURE | extract or swap fails (injected) | pre-swap media tree restored; no half tree at live path | nonzero exit after rollback |
| VERIFY_ONLY | `verify` subcommand on any snapshot | report ok/error, exit 0/1; no writes whatsoever | N/A |

## Code Map

- `backend/app/core/backup.py` — snapshot contract the restore consumes: `run_snapshot` (L~171), manifest built with `db.file`/`db.sha256`/`archive_sha256` (L195–208), `_write_sidecars` (L141, GNU `<64-hex>  <name>\n` format), `_snapshot_db` (L91, backup API from read-only URI), constants (L44–56). Read-only.
- `backend/tests/test_backup.py` — `_make_world()` WAL-mode fixture (L22–37), `_sha256()` (L39–43), sidecar/manifest assertion style (L57–83); mirror in `test_restore.py`.
- `backend/tests/test_deploy_script.py` — `test_deploy_scripts_syntax` (L12–18, bash -n over deploy.sh/backup.sh/restore.sh), stage-tree pins for restore.sh (L50–51), flock behavior pins (L59–87). Extend for the new script.
- `deploy/restore.sh` — current blind apply: guard → `systemctl stop` → rm DB+WAL → sqlite `.backup` → tar extract → start; no verify, no chown, systemd-only. Rework onto the Python core.
- `deploy/backup.sh` — wrapper pattern to mirror: venv-or-python3 discovery, flock, `MYTHOSCIRCLE_DATA_DIR` wiring.
- `deploy/backup.docker.cron` — docker topology invocation pattern (`docker exec mythoscircle-api-1 .venv/bin/python -m app.core.backup --data-dir /data`); volume `mythos-data:/data` holds DB, media, and `/data/backups/<stamp>/`.
- `deploy/deploy.sh` — stage installer (spec-1.7) provisioning scripts under `<stage>/opt/mythoscircle/deploy/`; ship the new wrapper there too.

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/core/restore.py` -- new verify+apply core mirroring backup.py stdlib/env conventions (subcommands `verify`/`apply`) -- trust chain + atomic apply must be pytest-testable
- [x] `backend/tests/test_restore.py` -- corrupt/clean/atomicity tests reusing `_make_world`-style fixtures -- pins the AR30 gate contract
- [x] `deploy/restore.sh` -- rework onto the core: verify → stop → apply → chown mythoscircle → start -- systemd verify-then-apply
- [x] `deploy/restore.docker.sh` -- thin docker wrapper: verify via `docker run --rm --volumes-from` on the api image, stop/start api container -- deployed-stack restore path exists
- [x] `backend/tests/test_deploy_script.py` -- add restore.docker.sh to syntax + stage-tree pins -- scripts stay linted/installed

**Acceptance Criteria:**
- Given a corrupted snapshot (bit-flipped DB, tampered manifest, or torn archive), when restore runs, then it fails loudly with a nonzero exit and a message naming the artifact, and the live DB + media tree are byte-unchanged.
- Given a clean 6-1 snapshot, when restore runs, then the trust chain verifies first, DB + media apply, and the restored DB passes `PRAGMA integrity_check` with the snapshot's row count.
- Given the deployed docker stack, when `restore.docker.sh` runs, then the api container stops, the volume is restored, and the container restarts serving the restored world.

## Spec Change Log

- 2026-09-20 review round 1 (3 parallel layers, 10 patch findings, 0 loopbacks): the spec survived as written; review findings were implementation gaps, not spec defects. Amended to close them:
  - **Half-restore risk** (BlindHunter/EdgeHunter): media failure after the DB swap left new-DB + old-media at rest. `apply_restore` now moves the pre-swap DB + `-wal`/`-shm` siblings aside and rolls them back byte-identical on ANY apply-stage failure. KEEP: PID-unique temp/pre names (`*.restore-tmp.*`, `*.restore-pre.*`) so concurrent restores cannot clobber each other.
  - **Loud-failure contract holes** (all three): `db.sha256` presence/type now validated in `_manifest_db` (never a KeyError traceback, `-O`-safe — the old bare `assert` is gone); `_row_count` guards sqlite errors; `main()` catches `(RestoreError, OSError, sqlite3.DatabaseError, tarfile.TarError)`. KEEP: `target.close()` is explicit in `_apply_db` — the sqlite3 context manager commits but does not close.
  - **Unpinned shell orchestration** (VerificationGap): the verify-before-stop / stop-apply-start / chown-before-restart lifecycle is now behavior-tested with PATH-stubbed `systemctl`/`chown`/`id` and `docker` (records invocation order; corrupt → no stop; clean → stop < apply < start). KEEP: wrapper tests reuse `run_snapshot`-built real snapshots.
  - **Entrypoint swallow** (BlindHunter): `--entrypoint .venv/bin/python` is explicit in `restore.docker.sh`; `--volumes-from` works on a stopped container; image-freshness (an older image lacks `app.core.restore`) is documented in the wrapper header.
  - **Ownership scope** (BlindHunter/EdgeHunter): `restore.sh` chowns only the live DB (`*.db` glob, name comes from the manifest) + `media/` — the `backups/` snapshot input stays operator-owned; chown/stop/start each fail loudly instead of aborting silently.
  - **Provisioning** (BlindHunter): `deploy.sh` installs `restore.docker.sh` into the stage tree; stage-tree + syntax pins extended. Known residual limitation: a SIGKILL'd restore leaves `.restore-*` litter; the next run fails loudly naming the collision (deterministic-clobber via PID-suffixed names is fixed).
  - **Fixture defect** (self): live-state maps excluded `backups/` by checking `p.parts[0]` on an absolute path — fixed to relative-parts exclusion; the media swap failure test now diverges the live DB first so the DB rollback is provable.

## Design Notes

- **Trust chain order:** manifest.json.sha256 → manifest bytes → db sidecar + manifest db.sha256 (two independent pins on the DB) → archive_sha256 → `PRAGMA integrity_check` on the snapshot DB (catches structural tears hashing cannot). Every step gates the next; failure exits before any write; the `verify` subcommand is also the 6-5 gate instrument.
- **Atomic apply (final, review round 1):** pre-swap — the live DB and its `-wal`/`-shm` siblings move aside (`*.restore-pre.<pid>`); DB — backup API into `*.restore-tmp.<pid>` in the destination dir, `os.replace` in; media — extract to `media.restore-tmp.<pid>`, dir-swap with rollback. ANY apply-stage failure (smoke, media, I/O) rolls the pre-swap world back byte-identical — the DB swap is never left in place over a failed media apply. Post-apply smoke: integrity_check + row count on the live DB before the wrapper restarts the service.
- **Topology split:** systemd `restore.sh` runs as root with the host python via backup.sh's venv discovery, chowns DB + media to `mythoscircle`, restarts via systemctl. Docker wrapper derives the image via `docker inspect -f '{{.Config.Image}}' mythoscircle-api-1` and runs the core in a `--rm --volumes-from` container so it sees `/data` exactly like the cron; snapshots live at `/data/backups/<stamp>` in the same volume.
- **media:null snapshots** leave the existing media tree untouched — the restored DB's manifest rows are the old state; newer orphan files are unreferenced and harmless (full reclaim is 6-3's concern, not restore's).

## Verification

**Commands:**
- `uv run --directory backend pytest tests/test_restore.py tests/test_backup.py tests/test_deploy_script.py tests/test_deploy_contract.py -q` -- expected: all pass
- `bash -n deploy/restore.sh deploy/restore.docker.sh` -- expected: silent success
- `uv run --directory backend pytest -q && make lint && uv run --directory backend mypy app` -- expected: full suite green, lint clean, app typecheck clean
- Note: full `make typecheck` carries PRE-EXISTING test-file drift (58 mypy errors across 9 sprint-A-era test files, untouched by this story, verified by `mypy app` being clean); fixing that drift is a separate story, not 6-2 scope.

## Suggested Review Order

**Trust chain & verify (the 6-5 gate instrument)**

- Two independent DB pins + structural guards, immutable reads — start here
  [`restore.py:165`](../../backend/app/core/restore.py#L165)

- GNU-format sidecar check pins hash AND name
  [`restore.py:120`](../../backend/app/core/restore.py#L120)

**Atomic apply & rollback**

- Pre-swap + byte-identical rollback on any apply-stage failure
  [`restore.py:310`](../../backend/app/core/restore.py#L310)

- Backup-API copy to PID-unique temp + atomic replace
  [`restore.py:223`](../../backend/app/core/restore.py#L223)

- Media temp-extract + dir swap with rollback
  [`restore.py:267`](../../backend/app/core/restore.py#L267)

- CLI verify/apply + widened catch set
  [`restore.py:386`](../../backend/app/core/restore.py#L386)

**Wrapper orchestration (the story's headline ACs)**

- Systemd: verify gate before stop; apply gate leaves STOPPED
  [`restore.sh:40`](../../deploy/restore.sh#L40)

- Systemd: scoped chown (never backups/) + guarded start
  [`restore.sh:50`](../../deploy/restore.sh#L50)

- Docker: verify-before-stop, entrypoint-explicit apply run
  [`restore.docker.sh:36`](../../deploy/restore.docker.sh#L36)

- Docker: stop/start guards + failure messaging
  [`restore.docker.sh:43`](../../deploy/restore.docker.sh#L43)

**Provisioning & contract**

- Stage tree ships the docker wrapper (Code Map promise)
  [`deploy.sh:86`](../../deploy/deploy.sh#L86)

- Restore targets the manifest's db.file, never a hardcoded name
  [`test_deploy_contract.py:31`](../../backend/tests/test_deploy_contract.py#L31)

**Behavior tests (peripherals)**

- Stub-systemctl orchestration: corrupt → never stops; clean → stop < start
  [`test_deploy_script.py:165`](../../backend/tests/test_deploy_script.py#L165)

- Stub-docker lifecycle: verify-before-stop, apply-failure leaves stopped
  [`test_deploy_script.py:233`](../../backend/tests/test_deploy_script.py#L233)

- DB rollback provable on media-swap failure (diverged live DB)
  [`test_restore.py:362`](../../backend/tests/test_restore.py#L362)

- Malformed-manifest matrix + CLI verify failure path
  [`test_restore.py:420`](../../backend/tests/test_restore.py#L420)