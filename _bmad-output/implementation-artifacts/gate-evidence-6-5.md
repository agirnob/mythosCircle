# 6-5 Beta Launch Gate — Evidence Runbook

Status: **COMPLETE — ALL LEGS PASS (2026-09-20, witnessed end-to-end);
apply leg run on owner GO with restore-back rollback.** Leg A corrupt-
fail-loud PASS; Leg B apply PASS; post-apply verification 5/5 PASS;
rollback (restore-back, second clean apply) PASS; post-rollback
verification 5/5 PASS. Remaining after this record: only the sprint-row
flip on the owner verdict pass (6-4 precedent: spec done / row review)
+ commit (main session).

## The criterion (epic-6 gate; AR13, AR30, NFR5)

Beta is permitted only once restore has been exercised against a real
campaign snapshot: a **corrupted** snapshot fails loudly with nothing
applied and the api container never stopped, AND a **clean** restore
verifies against its checksum and applies+serves the real world.
Mechanics pinned in pytest (`backend/tests/test_restore.py`,
`backend/tests/test_deploy_script.py`); this runbook records the
deployed-surface execution on the live stack (192.168.1.21). **Both
criterion legs were executed on the real 739-row nightly snapshot —
beta is gated on exactly these results.**

## Conventions

- Timestamps are UTC, captured with `date -u +%Y%m%dT%H%M%SZ` (exact
  format used throughout; plain `date -u` is not the source of any
  recorded stamp).
- Exit codes are captured by the session wrapping each command, e.g.
  `~/restore.docker.sh <snap>; echo EXIT=$?` — the wrapper/CLI prints do
  not include an exit line.
- Row/integrity/canary checks run as one read-only container-python
  probe (see Appendix) — same `immutable=1` read pattern restore.py's
  `_readonly_connection` uses (no locks, no -shm/-wal creation).

## Deployed surface (recon, read-only, 2026-09-20)

| Fact | Value |
|---|---|
| Host / access | `ssh -i ~/.ssh/mythos_laptop homest@192.168.1.21` |
| Container | `mythoscircle-api-1` (`72a374c07198`, image `mythoscircle-api:local`) — Up |
| Volume | `/data` — live DB `/data/mythoscircle.db`, media `/data/media`, snapshots `/data/backups/<stamp>/` |
| Wrapper parity | laptop `~/restore.docker.sh` md5 `1848e764f42772f30a893ac56ed333ca` == repo `deploy/restore.docker.sh` (byte-identical) |
| Queue (job) state | `pending jobs: 0` — no live restore while mid-job (13:36Z and 20:13:01Z) |
| Snapshots on box | `20260919T003657Z` (00:36Z — the manual first run, pre-cron; does not fit the `30 1 * * *` schedule), `20260919T013002Z`, `20260920T013001Z` (all 739 rows, media:null, sidecars verified genuine) + fresh `20260920T133653Z` (this gate) |
| Nightly proof | homest user crontab `30 1 * * * docker exec mythoscircle-api-1 .venv/bin/python -m app.core.backup --data-dir /data >> /home/homest/mythos-backup.log 2>&1`; log shows runs through `20260920T013001Z` |

Doc drift corrected (this record): prior handover claimed `/etc/cron.d/`
install; actual install is the **homest user crontab** (confirmed
above). The 00:36Z snapshot predates the cron — annotated as the manual
first run, not chased.
Known non-facts, documented not chased: stray `-shm`/`-wal` files in
snapshot dir `20260920T013001Z` (from a plain `mode=ro` recon open,
13:03–13:04Z; benign, `immutable=1` reads ignore them) — **not touched**.

## Post-apply verification observables (pinned)

1. `sha256sum /data/mythoscircle.db` == manifest pin
2. row count 739
3. `PRAGMA integrity_check` == ok
4. canary campaigns present: `test2` (`01M2E04TV13JD5DVG898CWFSE8`),
   `Sarvosk` (`01M25825AYSVVP7EE2KYP0W7HZ`)
5. `https://world.miscco.uk/login` 200, `/api/auth/me` 401

## Pre-apply baseline (read-only, 2026-09-20 13:35–13:38Z)

| Check | Exact command | Output | Exit |
|---|---|---|---|
| timestamp | `date -u +%Y%m%dT%H%M%SZ` | `20260920T133535Z` | 0 |
| live DB integrity | probe (Appendix) | `integrity: ok` | 0 |
| live DB total rows | probe (Appendix) | `total_rows: 739` / `campaign_rows: 9` | 0 |
| canaries (live DB) | probe (Appendix) | `[('01M25825AYSVVP7EE2KYP0W7HZ', 'Sarvosk'), ('01M2E04TV13JD5DVG898CWFSE8', 'test2')]` | 0 |
| live raw main-file sha256 | `docker exec mythoscircle-api-1 sh -c "sha256sum /data/mythoscircle.db"` | `7766ced95293801e83db17b44b37e1f09a05032125f75259d2dc1f702f265531  /data/mythoscircle.db` | 0 |
| HTTP (TLS) | `curl -s -o /dev/null -w '%{http_code}' https://world.miscco.uk/login` / `.../api/auth/me` | `login: 200`, `me: 401` | 0 |
| CLEAN_VERIFY (nightly, evidence record) | `docker exec mythoscircle-api-1 .venv/bin/python -m app.core.restore verify /data/backups/20260920T013001Z` | `restore verify OK: /data/backups/20260920T013001Z (mythoscircle.db, 739 rows, no media)` | 0 |

Baseline probe note (record fidelity): the first baseline probe run
queried `SELECT id, name` and failed with `no such column: name`
(campaign column is `title`); corrected to `title` and re-run (exit 0)
— every recorded baseline output above is from the corrected run.

Content-neutrality proof: the fresh pre-apply snapshot
(`20260920T133653Z`, SQLite backup API = WAL-consistent) hashes to
**the same pin** as the 01:30 nightly —
`c314a542d8d751e44412e5467857eeecb66937bd206e73cc26e58e2b270708ce` —
so the live world's logical content is byte-identical to the apply
target: the apply is content-neutral (world idle since 2026-09-13). The
live raw-file sha differing from the pin is expected non-content page
layout (WAL checkpoint state); the post-apply pin check validates the
RESTORED file against the snapshot's own pin.

## Leg A — corrupt snapshot fails loudly, container never stopped (PASS)

Scratch corrupt copy INSIDE the volume (wrapper must see it); the real
nightly dir `20260920T013001Z` untouched (only reads ever touched it).

| Step | Exact command | Output | Exit |
|---|---|---|---|
| 1. container state before | `docker ps` (13:35:35Z) | `72a374c07198  mythoscircle-api:local  ".venv/bin/uvicorn a…"  50 minutes ago` — **Up** | 0 |
| 2. copy snapshot to scratch | `docker exec mythoscircle-api-1 cp -a /data/backups/20260920T013001Z /data/backups/gate-corrupt` | `cp exit: 0` | 0 |
| 3. corrupt the DB copy | `docker exec mythoscircle-api-1 sh -c "truncate -s 4096 /data/backups/gate-corrupt/mythoscircle.db"` | `mythoscircle.db` 4096 bytes (was 1130496); manifest + sidecars intact | 0 |
| 4. run the wrapper on the corrupt copy | `~/restore.docker.sh /data/backups/gate-corrupt; echo EXIT_CODE=$?` | see verbatim below | **1** |
| 5. container state after | `docker inspect -f '{{.State.Status}} {{.State.StartedAt}}' mythoscircle-api-1` (13:36:42Z) | `running 2026-09-20T12:45:28.109076825Z` — **Up, start time unchanged** | 0 |

Verbatim wrapper output (≈13:36Z; `EXIT_CODE=$?` captured by the
session, not printed by the wrapper):

```
Verifying snapshot /data/backups/gate-corrupt...
restore verify FAILED: mythoscircle.db does not match its sidecar mythoscircle.db.sha256
restore verify FAILED — nothing applied; mythoscircle-api-1 still running
```

exit: **1**.

**Verdict: PASS.** Corrupt snapshot → verify fails loudly naming the
artifact (`mythoscircle.db does not match its sidecar
mythoscircle.db.sha256`), exit 1, `docker ps` Up before (steps 1, 13:35:35Z)
and after (step 5, 13:36:42Z, same StartedAt) — the api container was
never stopped and nothing was applied (wrapper `die` path; pinned by
`test_deploy_script.py:233-260`).
Scratch cleanup (corrected record): the `gate-corrupt` dir was **removed
post-leg by the main session** (`rm -rf /data/backups/gate-corrupt`);
`/data/backups` now holds the 4 real snapshots. Early draft claimed
prune would eventually reclaim `gate-corrupt` — that was WRONG:
`prune_backups` (backup.py:149-158) sorts snapshot-root dirs by **name
descending** and keeps the newest 14, and `gate-corrupt` sorts ABOVE
every `20…Z` stamp lexically, so it would never be pruned and would
permanently consume a keep slot. Removal was correct.

## Leg B prep — fresh pre-apply snapshot at point of action (DONE)

| Step | Exact command | Output | Exit |
|---|---|---|---|
| 1. queue verified empty | probe-style immutable read | `pending jobs: 0` | 0 |
| 2. fresh pre-apply snapshot | `docker exec mythoscircle-api-1 .venv/bin/python -m app.core.backup --data-dir /data` | `mythoscircle backup complete: /data/backups/20260920T133653Z` | 0 |
| 3. fresh stamp verified clean | `docker exec mythoscircle-api-1 .venv/bin/python -m app.core.restore verify /data/backups/20260920T133653Z` | `restore verify OK: /data/backups/20260920T133653Z (mythoscircle.db, 739 rows, no media)` | 0 |

Fresh stamp manifest (`/data/backups/20260920T133653Z/manifest.json`):
`created_at 2026-09-20T13:36:53Z`, `db.file mythoscircle.db`,
`db.sha256 c314a542d8d751e44412e5467857eeecb66937bd206e73cc26e58e2b270708ce`,
`db.size 1130496`, `media null`, `retention 14`, `schema_version 1`.
Fresh dir sidecars verified present + genuine (no stray -wal/-shm; the
13:36 backup cleaned clean).

**The rollback point is `/data/backups/20260920T133653Z` — itself
verified clean, same pin as the apply target.**
Freshness (bounded): the pre-apply snapshot was taken 13:36Z and the
apply ran 20:13Z (~6.5 h gap); the rollback point still fully covered
the applied state — world idle since 2026-09-13, queue re-verified
empty at 20:13:01Z, and the pre/post/rollback pins are identical
(`c314a542...`), so the restored-then-rolled-back world is
content-identical to the pre-apply snapshot by pin.

## Leg B — apply (PASS, owner GO 2026-09-20 20:13Z, restore-back chosen)

Point-of-action owner confirmation received; queue re-verified empty
(`pending jobs: 0`, 20:13:01Z). Command:
`~/restore.docker.sh /data/backups/20260920T013001Z; echo EXIT=$?`

Verbatim output (20:13:07Z → 20:13:09Z, wall ~2 s; `EXIT=$?` captured by
the session, not printed by the wrapper):

```
Verifying snapshot /data/backups/20260920T013001Z...
restore verify OK: /data/backups/20260920T013001Z (mythoscircle.db, 739 rows, no media)
mythoscircle-api-1
restore complete from /data/backups/20260920T013001Z
mythoscircle-api-1
restore complete from /data/backups/20260920T013001Z
```

exit: **0**.

Output shape (applies to this block and the identical rollback block):
the bare `mythoscircle-api-1` lines are `docker stop` / `docker start`
echoing the container name; `restore complete from ...` appears TWICE by
design — once from the core CLI (`-m app.core.restore apply`, restore.py
`main`) and once from the wrapper's final echo. Readable lifecycle:
verify OK → stop → apply → start → complete. **EXIT=0** — stop < apply
< start all succeeded; container restarted by the wrapper (expected).

Post-apply verification — all five observables PASS (20:13:51–52Z):

| Observable | Exact command | Output | Exit |
|---|---|---|---|
| pin | `docker exec mythoscircle-api-1 sh -c "sha256sum /data/mythoscircle.db"` | `c314a542d8d751e44412e5467857eeecb66937bd206e73cc26e58e2b270708ce  /data/mythoscircle.db` | 0 |
| rows | probe (Appendix) | `total_rows: 739` / `campaign_rows: 9` | 0 |
| integrity | probe (Appendix) | `integrity: ok` | 0 |
| canaries | probe (Appendix) | `[('01M25825AYSVVP7EE2KYP0W7HZ', 'Sarvosk'), ('01M2E04TV13JD5DVG898CWFSE8', 'test2')]` | 0 |
| HTTP | `curl -s -o /dev/null -w '%{http_code}' https://world.miscco.uk/login` / `.../api/auth/me` | `login: 200`, `me: 401` | 0 |

**Verdict: PASS — restored world matches the manifest pin byte-for-byte
and serves over TLS.**

## Rollback — restore-back, second clean apply (PASS, owner decision)

Command: `~/restore.docker.sh /data/backups/20260920T133653Z; echo EXIT=$?`

Verbatim output (20:13:59Z → 20:14:02Z, wall ~3 s; exit captured by the
session — output shape as annotated on the apply block):

```
Verifying snapshot /data/backups/20260920T133653Z...
restore verify OK: /data/backups/20260920T133653Z (mythoscircle.db, 739 rows, no media)
mythoscircle-api-1
restore complete from /data/backups/20260920T133653Z
mythoscircle-api-1
restore complete from /data/backups/20260920T133653Z
```

exit: **0**.

Post-rollback verification — all five observables PASS (20:14:15–16Z):

| Observable | Exact command | Output | Exit |
|---|---|---|---|
| pin | `docker exec mythoscircle-api-1 sh -c "sha256sum /data/mythoscircle.db"` | `c314a542d8d751e44412e5467857eeecb66937bd206e73cc26e58e2b270708ce  /data/mythoscircle.db` | 0 |
| rows | probe (Appendix) | `total_rows: 739` / `campaign_rows: 9` | 0 |
| integrity | probe (Appendix) | `integrity: ok` | 0 |
| canaries | probe (Appendix) | `[('01M25825AYSVVP7EE2KYP0W7HZ', 'Sarvosk'), ('01M2E04TV13JD5DVG898CWFSE8', 'test2')]` | 0 |
| HTTP | `curl -s -o /dev/null -w '%{http_code}' https://world.miscco.uk/login` / `.../api/auth/me` | `login: 200`, `me: 401` | 0 |
| container state | `docker ps --filter "name=mythoscircle" --format "{{.Names}}\t{{.Status}}\t{{.ID}}"`; `docker inspect -f '{{.State.Status}} (started {{.State.StartedAt}})' mythoscircle-api-1` (20:14:25Z) | `mythoscircle-api-1  Up 23 seconds  72a374c07198`; `running (started 2026-09-20T20:14:02.111457343Z)`; `mythoscircle-web-1` Up untouched | 0 |

**Verdict: PASS — the rollback point restored cleanly; the world ends
content-identical to where it started (same pin `c314a542...` at
pre-apply, post-apply, and post-rollback), exactly the content-neutral
prediction. Bonus gate evidence: two full clean applies on the real
stack.**

## Verdict lines (per acceptance criteria)

- **AC1 — corrupt fails loudly, container never stopped: PASS** (Leg A).
- **AC2 — clean restore with owner go: stops, applies, restarts, DB
  matches sha256 with 739 rows, integrity ok, canaries present, serving
  over TLS: PASS** (Leg B + post-apply, 5/5).
- **AC3 — gate record documents both legs verbatim: MET** (this file;
  spec + sprint row flips on the owner verdict pass, main session).

## Verify-pass accounting (2026-09-20 — one consistent list, 6 passes, all exit 0)

1. 6-2 session verify — ~12:31Z (story record; first deployed pass)
2. recon verify — ~13:04–13:08Z (today's recon; stray `-shm`/`-wal` in
   the nightly dir timestamped 13:03–13:04Z)
3. Leg A baseline CLEAN_VERIFY evidence record — 13:35Z (command +
   output in the baseline table)
4. fresh-stamp verify (`20260920T133653Z`) — 13:37Z (Leg B prep step 3)
5. apply-leg pre-verify — 20:13:07Z (inside the wrapper, apply block)
6. rollback-leg pre-verify — 20:13:59Z (inside the wrapper, rollback
   block)

## Remaining (main session, owner verdict pass)

1. `sprint-status.yaml` — flip `6-5-beta-launch-gate` `in-progress` →
   `review` (6-4 precedent: spec done / row review, verdict later).
   NOT touched by this execution.
2. Spec status flip (`spec-6-5` → done on verdict) + commit (evidence
   file + spec log + sprint row). NOT committed by this execution.

## Risks / notes

- Verify stability: 6 clean passes today (accounting above), all exit
  0, sub-second; the apply legs' risk was the stop/apply/start
  mutation, which passed twice with the fresh rollback point in place.
- `gate-corrupt` scratch: created by Leg A, **removed post-leg by the
  main session** (`rm -rf /data/backups/gate-corrupt`) — retention
  corrected (see Leg A); `/data/backups` holds the 4 real snapshots.
- Media: all real snapshots carry `media: null` (media table 0 rows);
  the apply is DB-only evidence; the media-restore path stays
  pytest-pinned — documented, not a defect (deployed world has no media).
- The `-shm`/`-wal` strays inside `20260920T013001Z` are untouched by
  every leg (immutable=1 reads).
- No DB/world edits of any kind were made by the gate: the only
  mutations were the **TWO wrapper applies** (Leg B + rollback); Leg A
  was a verify-failure invocation and mutated nothing.

## Appendix — read-only probe (rows / integrity / canaries)

Source, piped verbatim to `docker exec -i mythoscircle-api-1
.venv/bin/python -` (immutable=1 read-only URI — the same read pattern
restore.py's `_readonly_connection` uses; no locks, no -shm/-wal
creation):

```python
import sqlite3

c = sqlite3.connect("file:/data/mythoscircle.db?mode=ro&immutable=1", uri=True)
print("integrity:", c.execute("PRAGMA integrity_check").fetchone()[0])
tables = [
    r[0]
    for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    )
]
total = 0
for t in tables:
    statement = 'SELECT count(*) FROM "{}"'.format(t)
    total += int(c.execute(statement).fetchone()[0])
print("total_rows:", total)
print("campaign_rows:", c.execute("SELECT count(*) FROM campaign").fetchone()[0])
print(
    "canaries:",
    c.execute(
        "SELECT id, title FROM campaign WHERE id IN "
        "('01M2E04TV13JD5DVG898CWFSE8','01M25825AYSVVP7EE2KYP0W7HZ')"
    ).fetchall(),
)
```

Invocation: `docker exec -i mythoscircle-api-1 .venv/bin/python - < probe.py`
Output identical across pre-apply baseline (13:38Z), post-apply
(20:13:51Z), and post-rollback (20:14:15Z):

```
integrity: ok
total_rows: 739
campaign_rows: 9
canaries: [('01M25825AYSVVP7EE2KYP0W7HZ', 'Sarvosk'), ('01M2E04TV13JD5DVG898CWFSE8', 'test2')]
```