---
title: '6-5 Beta Launch Gate'
type: 'chore'
created: '2026-09-20'
status: 'done'
review_loop_iteration: 0
baseline_commit: '68754c4'
context:
  - '_bmad-output/implementation-artifacts/epic-6-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Epic 6 states beta is permitted only if restore has been exercised against a real campaign snapshot — a corrupted snapshot fails loudly AND a clean restore verifies (AR13, AR30, NFR5). The mechanics are fully pinned in pytest (6-2: `test_restore.py` corrupt/clean/rollback, `test_deploy_script.py` wrapper lifecycle) and the DEPLOYED verify leg already passed twice against the real nightly (`restore verify OK: /data/backups/20260920T013001Z (mythoscircle.db, 739 rows, no media)`, exit 0 — 6-2 session + reproduced this recon). What is missing is the gate EXECUTION on the deployed stack: a corrupted snapshot proven to fail loudly with the container never stopped, a clean restore proven to apply and serve the real world, and the evidence recorded. No code change is expected — this story proves and records.

**Approach:** prove-and-record on the live deployed stack (192.168.1.21). Leg A — copy the newest nightly snapshot to scratch, corrupt the DB copy (bit-flip/truncate), run `~/restore.docker.sh` on the copy, expect `restore verify FAILED: <artifact>` + exit 1 + container Up throughout (docker ps before/after). Leg B — take a fresh pre-apply snapshot (rollback point) at point of action (owner confirmation), run `~/restore.docker.sh` on the real snapshot, verify post-apply: `sha256sum` of the restored DB == the manifest pin, row count 739, PRAGMA integrity_check ok, canary campaigns present, api serves (login 200, /api/auth/me 401). Rollback-or-document with the owner. Write the gate evidence (mirroring `demo-runbook-5-5.md`'s format), flip the sprint row + epic 6, commit.

## Boundaries & Constraints

**Always:**
- Leg A corrupts a COPY in scratch (`cp -a` of the snapshot dir), never the only good nightly snapshot dir.
- Leg B: fresh same-day snapshot taken at point of action (the rollback point + documented intent); owner confirmation BEFORE the apply leg runs (real production-volume mutation); the apply is the `~/restore.docker.sh <snapshot>` shape exactly as verified.
- Post-apply verification is read-only and observable: `docker exec mythoscircle-api-1 sh -c 'sha256sum /data/mythoscircle.db'` (or `cat | sha256sum`) must equal the manifest `db.sha256` for the restored snapshot; row count 739; `PRAGMA integrity_check` == ok; canary campaigns test2 (`01M2E04TV13JD5DVG898CWFSE8`) and Sarvosk (`01M25825AYSVVP7EE2KYP0W7HZ`) present; `curl https://world.miscco.uk/login` 200; `/api/auth/me` 401.
- Rollback choice is the owner's: restore the fresh pre-apply snapshot back (a second real apply — extra gate evidence) OR document the applied state as intended. Either way the world ends at the same content (live == snapshot today, world idle since 2026-09-13 — apply is content-neutral).
- Evidence is recorded in exact-command + exact-output + exit-code + timestamp form, mirroring `demo-runbook-5-5.md`.
- Scout the deployed facts read-only first (wrapper parity, image parity) — already done this recon: wrapper byte-identical (md5 `1848e764...`), deployed image restore.py byte-identical to HEAD (409/409), verify leg exit 0.

**Ask First:** point-of-action confirmation before the apply leg (production mutation); the rollback-or-document decision after a clean apply; whether 6-5's sprint flip waits for the same owner verdict pass as 6-4 (it does by precedent — spec done / row review, verdict later).

**Never:** no code changes to backup/restore core or wrappers (mechanics are pinned; this is execution + evidence); no corruption of the real snapshot dir; no DB/world edits of any kind (no throwaway campaigns, no sqlite writes); no live restore while the LLM/api stack is mid-job (queue empty today — verify); no touching the stray `-wal`/`-shm` files in the newest snapshot dir (benign `immutable=1` reads ignore them; created by a plain `mode=ro` recon open — noted, not chased); no remote push.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| CORRUPT_COPY_VERIFY | scratch copy of the newest snapshot, DB bit-flipped/truncated | `restore verify FAILED: <artifact>`, exit 1; container Up before AND after (never stopped) | wrapper `die` path (test_deploy_script.py:233-260 pins the same) |
| CLEAN_VERIFY | the real nightly snapshot | `restore verify OK: ... (mythoscircle.db, 739 rows, no media)`, exit 0 | already proven; re-run for the evidence record |
| CLEAN_APPLY | the real snapshot after fresh pre-apply snapshot + owner go | stop < apply < start; post-apply sha256 == manifest pin, 739 rows, integrity ok, canaries present, api 200/401 | apply failure leaves container STOPPED, nothing restarted (pinned) |
| MEDIA_NULL | all real snapshots carry media: `null` (media table 0 rows) | DB-only apply evidence; media-restore path stays pytest-pinned (documented, not a defect — the deployed world has no media yet) | N/A |
| ROLLBACK_RESTORE | owner chooses restore-back | fresh pre-apply snapshot applied the same verified path; world unchanged in content | second clean apply = bonus gate evidence |

</frozen-after-approval>

## Code Map

- `backend/app/core/restore.py` -- verify chain (`_check_sidecar` L120, manifest pins L165-197 inside `verify_snapshot`, immutable=1 read-only open L73-82) + apply (`_apply_db` L223-247, smoke L253-269, media swap+rollback L267-307, `apply_restore` L310-356 with pre-swap aside ~L347-351); CLI `verify|apply <snap> [--data-dir]` L386-408, all failures `restore <cmd> FAILED: <artifact>` exit 1. NOT expected to change — reference for evidence wording.
- `deploy/restore.docker.sh` -- the gate's execution surface: verify via `docker run --rm --volumes-from` (image derived `docker inspect`), stop, apply, start; verify failure → `die` without stop.
- `deploy/backup.docker.cron` + laptop **user crontab** (`30 1 * * * docker exec mythoscircle-api-1 .venv/bin/python -m app.core.backup --data-dir /data >> /home/homest/mythos-backup.log 2>&1`) -- nightly snapshot evidence; log proves runs (`...20260920T013001Z`). Doc drift: prior handover claims `/etc/cron.d/` — actual install is the homest user crontab; corrected in this record, not chased.
- Laptop: `~/restore.docker.sh` (byte-identical to repo), container `mythoscircle-api-1`, volume `/data` (live DB `/data/mythoscircle.db`, media `/data/media`, snapshots `/data/backups/<stamp>/`); snapshots on box: `20260919T003657Z`, `20260919T013002Z`, `20260920T013001Z` (all 739 rows, media:null, sidecars verified genuine).
- `_bmad-output/implementation-artifacts/demo-runbook-5-5.md` -- evidence-format precedent: status line, criterion, per-leg measured table, exact commands/outputs, risks.
- `backend/tests/test_restore.py` + `backend/tests/test_deploy_script.py` -- the already-pinned mechanics the live legs demonstrate (corrupt/fail-loud/verify-before-stop/stop<apply<start) — evidence cross-reference, no new backend tests expected.

## Tasks & Acceptance

**Execution:**
- [x] Laptop Leg A: `cp -a /data/backups/20260920T013001Z /data/backups/gate-corrupt` (via docker exec, scratch INSIDE the volume so the wrapper sees it), bit-flip/truncate the DB copy, run `~/restore.docker.sh /data/backups/gate-corrupt`, capture output+exit; `docker ps` proves Up before/after -- corrupt snapshot fails loudly, container never stopped
- [x] Laptop Leg B prep: fresh pre-apply snapshot at point of action (`docker exec mythoscircle-api-1 .venv/bin/python -m app.core.backup --data-dir /data`), record new stamp -- rollback point + intent
- [x] **OWNER CONFIRMATION** before applying (Ask First) -- then: `~/restore.docker.sh /data/backups/20260920T013001Z`, capture the full lifecycle output
- [x] Post-apply read-only verification: sha256 of `/data/mythoscircle.db` == manifest pin `c314a542d8d751e44412e5467857eeecb66937bd206e73cc26e58e2b270708ce`, row count 739, integrity_check ok, canaries (test2/Sarvosk) present, `curl https://world.miscco.uk/login` 200 + `/api/auth/me` 401 -- restored world served
- [x] ROLLBACK_RESTORE or document-as-intended (owner decision): if restore-back, `~/restore.docker.sh /data/backups/<fresh-stamp>` + re-verify; else record the applied state as intended
- [x] Write `gate-evidence-6-5.md` (mirror demo-runbook-5-5 format: criterion, per-leg table with exact commands/outputs/exit/timestamps, verdict lines, doc-drift corrections) -- the durable gate record
- [x] `_bmad-output/implementation-artifacts/sprint-status.yaml` -- flip 6-5 to review (owner verdict precedent as 6-4) -- applied via step-05 sync-sprint-status (row in-progress → review); the verdict later flips review → done

**Acceptance Criteria:**
- Given a corrupted copy of a real snapshot, when `~/restore.docker.sh` runs, then it fails loudly naming the artifact, exits nonzero, and the api container is never stopped (Leg A evidence recorded).
- Given the real nightly snapshot, when `~/restore.docker.sh` runs with owner go, then the container stops, applies, restarts, and the restored DB matches the manifest sha256 with 739 rows, integrity ok, canaries present, serving over TLS.
- Given the gate record, when 6-5 ships, then the evidence artifact, spec, and sprint row document both legs verbatim — beta is gated on these exact results.

## Spec Change Log

- 2026-09-20 review round 1 (3 parallel layers; 0 intent_gap, 0 bad_spec — a record story with no code; 11 evidence patches + 2 spec patches + 1 defer, 0 loopbacks):
  - **gate-corrupt scratch dir was never self-pruning** (all three layers): `prune_backups` (backup.py:149-158) sorts snapshot-root dirs by name DESCENDING and keeps the newest 14 — `gate-corrupt` sorts above every `20…Z` stamp, so it was kept first forever and would have consumed a keep slot, silently shrinking real retention to 13. KEEP: corrupt a COPY, never the real nightly — but the copy must be REMOVED post-leg (main session: `docker exec mythoscircle-api-1 rm -rf /data/backups/gate-corrupt`; volume now holds the 4 real snapshots). Evidence wording corrected from "pruned first" to "removed post-leg".
  - **Record-fidelity bar enforced** (VerificationGap/EdgeHunter): the 5 post-apply + 5 post-rollback observables now carry exact probe commands (container python, immutable=1 URI) + outputs + exits + timestamps (probe source in the Appendix); Leg A before-state is an explicit `docker ps` row; capture convention (`; echo EXIT=$?`) stated; verify-pass accounting reconciled to one list of 6; date command format string recorded; 00:36Z snapshot annotated as the manual first run; bounded-freshness note (13:36Z snapshot vs 20:13Z apply, world idle + queue empty + pin equality). KEEP: exact-command + exact-output + exit + timestamp everywhere.
  - **Spec task checkbox overclaimed** (all three): the sprint-row flip-to-review was marked [x] but the row is still in-progress (owner verdict pass precedent, 6-4) — unchecked with the pending note; the review flip applies via step-05's sync-sprint-status now (spec done / row review precedent, 6-4), and the owner verdict later flips review → done. KEEP: the review→done flip is a verdict-pass action, never an implementation [x].
  - **Deferred** (ledger): `deploy.sh` installs the systemd-flavor `deploy/backup.cron` to `/etc/cron.d/` on the docker host where it cannot run (no mythoscircle user/systemd), while `deploy/backup.docker.cron` is never installed by deploy.sh — the live box works via the homest user crontab; the install-path drift is a real repo inconsistency (pre-existing, not a gate blocker).
  - **Rejected**: spec Code Map wrapper-pin anchor drift claim (the wrapper pin tests ARE at test_deploy_script.py:233/263/292 — the reviewer's counter-anchor was the drift); "three applies" wording (corrected in evidence as exactly two applies + one verify-failure invocation).

## Design Notes

The gate is an execution chore, not a code story: everything the two legs prove is already pinned in pytest (corrupt → fail loudly + live byte-unchanged; clean → verify-then-apply + post-apply smoke; wrapper → verify-before-stop for BOTH systemd and docker topologies). The live legs add the deployed-surface evidence the gate criterion demands — a real 739-row campaign snapshot on the real stack. The verify leg passing twice already (6-2 session + this recon) means Leg B's risk is in the apply mutation, which is why the fresh pre-apply snapshot (rollback point) and point-of-action confirmation precede it, and why post-apply verification has five independent observables (sha256 pin, rows, integrity, canaries, live HTTP). Because the deployed world is idle since 2026-09-13 and live content == snapshot content today, the apply is content-neutral — a restore-back (second clean apply) or document-as-intended both end at the same world; the choice is recorded, not relitigated.

## Verification

**Commands:**
- `uv run --directory backend pytest tests/test_restore.py tests/test_deploy_script.py tests/test_backup.py -q` -- expected: green (mechanics already pinned; no new tests expected)
- Laptop Leg A/B evidence commands as in Tasks -- each captured with exact output + exit + timestamp in `gate-evidence-6-5.md`

**Manual checks (gate legs, read-only where possible):**
- `docker exec mythoscircle-api-1 .venv/bin/python -m app.core.restore verify /data/backups/<stamp>` -- `restore verify OK: ... 739 rows, no media`, exit 0
- Corrupt copy: expect `restore verify FAILED: <db> does not match its sidecar <db>.sha256` (or manifest pin) + exit 1; `docker ps` Up before/after
- Post-apply: `sha256sum /data/mythoscircle.db` == `c314a542d8d751e44412e5467857eeecb66937bd206e73cc26e58e2b270708ce`; `sqlite3`-free check via container python `PRAGMA integrity_check` == ok; `curl -s -o /dev/null -w '%{http_code}' https://world.miscco.uk/login` == 200

## Suggested Review Order

**The gate record (start here — the story's deliverable)**

- Status line + criterion: what the gate blesses and its ACs
  [`gate-evidence-6-5.md:1`](../../_bmad-output/implementation-artifacts/gate-evidence-6-5.md#L1)

- Leg A verbatim fail-loud + before/after container rows — the corrupt leg, never-stopped proof
  [`gate-evidence-6-5.md:91`](../../_bmad-output/implementation-artifacts/gate-evidence-6-5.md#L91)

- Leg B apply with owner GO + the 5/5 post-apply observables (exact probes)
  [`gate-evidence-6-5.md:154`](../../_bmad-output/implementation-artifacts/gate-evidence-6-5.md#L154)

- Rollback restore-back + post-rollback 5/5 — a second clean apply as bonus evidence
  [`gate-evidence-6-5.md:195`](../../_bmad-output/implementation-artifacts/gate-evidence-6-5.md#L195)

- AC verdict lines — the gate's three criteria, each PASS
  [`gate-evidence-6-5.md:230`](../../_bmad-output/implementation-artifacts/gate-evidence-6-5.md#L230)

**Spec + mechanics cross-reference**

- The pinned mechanics the live legs demonstrate (corrupt/smoke/rollback; wrapper lifecycle)
  [`test_restore.py:70`](../../backend/tests/test_restore.py#L70)

- Verify-before-stop + stop<apply<start pins for the docker wrapper
  [`test_deploy_script.py:233`](../../backend/tests/test_deploy_script.py#L233)

- Spec: gate plan, boundaries (corrupt a copy, owner GO, rollback), and the round-1 change log
  [`spec-6-5-beta-launch-gate.md:20`](../../_bmad-output/implementation-artifacts/spec-6-5-beta-launch-gate.md#L20)
