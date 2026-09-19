# Epic 6 Context: Protect the Table — Backup, Restore & Ownership

<!-- Compiled from planning artifacts. Edit freely. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Make the DM's world safe and exclusively hers before beta ships: world state plus the media manifest are snapshotted nightly to a second location on the machine with integrity checksums, restore is verify-then-apply and is proven against a real snapshot (a corrupted snapshot fails loudly, a clean one verifies and restores a fully working world), and deleting a campaign removes every trace of it. Per-campaign ownership and TLS privacy hold from the Epic 1 auth foundation through the deployed instance. Beta is gated on the restore proof — no beta users until that test passes. Export is ownership, not backup; it never substitutes for snapshots.

## Stories

- Story 6.1: Nightly Snapshot with Integrity (done)
- Story 6.2: Restore Proven — Verify-Then-Apply
- Story 6.3: Total, Clean Campaign Deletion
- Story 6.4: Privacy and Per-Campaign Ownership
- Story 6.5: Beta Launch Gate

## Requirements & Constraints

- Nightly scheduled snapshot of world state + media manifest to a second local location, with a checksum written at snapshot creation; the restore script must restore from that exact snapshot path (NFR5, AR13, AR30, AD-12).
- Snapshot the database via the SQLite backup API only (VACUUM INTO, or wal_checkpoint + copy) — never a raw copy of a WAL-mode database file (AR13, AD-12).
- Restore is verify-then-apply: a corrupted snapshot fails loudly with no partial or corrupt state applied; a clean snapshot verifies against its checksum first, then applies (AR30, NFR5).
- Epic gate criterion: corrupted-snapshot restore fails loudly AND a clean restore verifies — both must hold before beta; failure implicates the backup/restore implementation, not the store (value checkpoint, AR30).
- Campaign deletion is a hard delete through the store with explicit confirmation: revisions, event log, queue entries, media-manifest rows, and the campaign's media directory are all removed — no soft-hide, no partial cleanup; deleted means actually gone (NFR10, AR20, AD-25).
- One owner per campaign; campaigns are private at beta. A non-owner's access is denied as if the campaign does not exist — no user or campaign enumeration (NFR6, AD-9, per the generic-401 auth convention).
- The deployed instance is reached over TLS via Caddy with the API bound to loopback only (AD-21, AR2, AR29).
- Beta launch is permitted only once restore has been exercised against a real campaign snapshot, a corrupted snapshot has failed loudly, and a clean restore has verified against its checksum — otherwise launch is blocked (6.5 gate, NFR5).
- The gate blocks external beta (sharing); it does not block the owner's own dogfooding — the owner has been the first user since the end of Epic 2.

## Technical Decisions

- Single-machine topology: one FastAPI process, one SQLite file, one media directory, Caddy in front — snapshot and restore are local-to-the-box operations; no distributed or remote storage is assumed (AD-8).
- Snapshot mechanics: the SQLite backup API plus the media manifest; media lives under per-campaign directories tracked in the manifest, so a backup of DB + manifest + media directory is self-contained (AR13, AD-12, AD-10).
- Restore is an operator-run script exercised pre-launch against the real snapshot path; integrity is a checksum written at snapshot creation, making restore a verify-then-apply sequence rather than a blind copy (AD-12, AR30).
- Deletion is a store-layer hard delete with explicit confirmation, not a soft-hide; it removes revision tables, event log, queue entries, manifest rows, and the media directory (AD-25).
- Ownership and privacy rest on the Epic 1 auth substrate already shipped: per-campaign owner binding, loopback-only API binding behind Caddy, and no-enumeration access denial; 6.4 verifies that behavior at the deployed surface (AR14, AR29, AD-9, AD-21).

## UX & Interaction Patterns

- Campaign deletion requires explicit confirmation before the irreversible hard delete runs — there is no undo once confirmed (AR20, AD-25).
- No dedicated UX design exists for this epic: the project has no UX design doc and the owner dogfoods the UI (AR22). Snapshot and restore are operator-side ops flows (deploy/ cron + script), not user-facing screens.

## Cross-Story Dependencies

- 6.1 (snapshots) precedes and feeds 6.2 (restore) and the 6.5 gate; 6.5 depends on both snapshot integrity and proven restore. 6.1 is already implemented; 6.2–6.5 remain backlog.
- 6.4 reuses the Epic 1 auth/ownership substrate — it is the deployed-instance verification of per-campaign privacy and TLS, not new account machinery.
- 6.3 depends on the store's commit/delete path (Epic 1) and the media service + manifest (Epic 4).
- Sequencing (owner decision 2026-09-19): the Epic 7 graph spike runs before Epic 6's beta gate; Epic IDs and story numbers are load-bearing keys in sprint-status, retro refs, and spec filenames — do not renumber.
- The beta pipeline is Epic 4 media → Epic 6 gate; gate passage is the prerequisite for any broader launch (sharing is a post-beta experiment).