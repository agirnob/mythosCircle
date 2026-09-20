---
title: '6-4 Privacy and Per-Campaign Ownership'
type: 'chore'
created: '2026-09-20'
status: 'done'
review_loop_iteration: 0
baseline_commit: '4e4cceb'
context:
  - '_bmad-output/implementation-artifacts/epic-6-context.md'
---

<!-- Target: 900-1300 tokens. Above 1600 = high risk of context rot. -->

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** AR14/NFR6 (campaigns private to one invited DM, non-owner access denied as-if-doesn't-exist, TLS at the deployed surface) are implemented across most of the API but two gaps and one drift survive. Recon: 8 of 9 campaign-scoped route modules enforce ownership-404 and are pinned, but the jobs REST surface (`/api/jobs`, `/api/jobs/{id}`, `/api/jobs?campaign_id=`, `/api/jobs/{id}/cancel`) has NO session dependency and NO ownership check — anonymous users can list any campaign's jobs (payloads and results leak generated world content), enqueue work on any campaign, and cancel anyone's queue. Separately, the session cookie's Secure flag derives from the request scheme, but every deployed topology serves plain HTTP at the origin (TLS terminates at the Cloudflare edge) — so the deployed cookie is never marked Secure (AR14). Two surfaces lack second-account API-level foreign pins (characters POST, campaigns move).

**Approach:** prove-and-pin (6-3 pattern) plus close the jobs hole and the Secure-cookie drift. Gate the four jobs routes on `get_current_account` + the ownership-404-first pattern (`characters.py:27-32` precedent) — foreign campaign/job 404 byte-identical to unknown, cancel is a no-op for foreigners. Make the cookie Secure flag environment-overridable (`MYTHOSCIRCLE_COOKIE_SECURE`, AD-22 env-only) so deployed stacks opt in without disturbing the plain-http dev flow; pin tri-state behavior. Add the two missing second-account API pins. Verify the deployed surface (TLS 200; anon jobs flips to 401) and record the already-pinned evidence (generic 401, owner binding, WS 4401, loopback bind).

## Boundaries & Constraints

**Always:**
- The store stays owner-blind — workers claim jobs by id with no account concept (AD-3). Ownership enforcement lives at the API router layer, exactly like `characters.py`/`edges.py`.
- Ownership 404s are indistinguishable from "doesn't exist": foreign campaign on create/list → `404 Campaign not found.` (same body as unknown); foreign job on get/cancel → the same `JobNotFoundError` 404 body as an unknown job. No message distinguishes them.
- Cancel checks ownership BEFORE mutation; a foreign cancel is a byte-identical 404 and the job's state/row are untouched.
- Cookie Secure: tri-state env `MYTHOSCIRCLE_COOKIE_SECURE` (AD-22) — unset → current behavior (`request.url.scheme=="https"`), `true` → always Secure, `false` → never Secure. The existing plain-http dev pin (http → not Secure) stays green with the env unset.
- Deployed stacks set `MYTHOSCIRCLE_COOKIE_SECURE: "true"` (both repo compose variants); the contract test pins it.
- New tests follow file conventions: autouse `_reset_limiters` fixture (register quota), the `_register_login`/`_authed_owned_campaign` helpers, `_assert_envelope`.

**Ask First:** changing the deployed laptop stack env (`~/mythos-redeploy.yml` or Portainer env) to add `MYTHOSCIRCLE_COOKIE_SECURE: "true"` — part of the standing update-laptop flow, but confirm the env-edit surface with the owner before touching the live stack; the repo compose files are edited regardless.

**Never:** no new auth machinery, no per-campaign invitations/multi-owner (beta one-owner AD-9); no HSTS/edge/Cloudflare changes (outside repo scope); no store-signature changes for ownership; no touching the WS route (already gated); no changing the generic-401 login contract; no live-DB throwaway campaigns on the deployed instance to "prove" enumeration (repo pins + read-only deployed curls instead).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| JOBS_ANON | no session cookie on any of the 4 jobs routes | 401 `Authentication required.` on all 4 | N/A |
| JOBS_FOREIGN_CREATE | valid body, another owner's campaign_id | 404 `Campaign not found.` (byte-identical to unknown-id body), zero rows | N/A |
| JOBS_FOREIGN_LIST | `?campaign_id=` another owner's campaign | 404 (same body), no job rows leaked | N/A |
| JOBS_FOREIGN_GET | another owner's job_id on `/api/jobs/{id}` | 404 body byte-identical to unknown-job 404 | N/A |
| JOBS_FOREIGN_CANCEL | POST cancel on another owner's job | 404 (identical to unknown); job state and row untouched | N/A |
| JOBS_OWN | own campaign / own job | unchanged 201/200/200-list/200-cancel | unchanged |
| COOKIE_UNSET_HTTP | no env, plain-http request | cookie NOT Secure (existing pin) | N/A |
| COOKIE_UNSET_HTTPS | no env, https request | cookie Secure (existing pin) | N/A |
| COOKIE_TRUE_HTTP | env=true, plain-http request | cookie Secure | N/A |
| COOKIE_FALSE_HTTPS | env=false, https request | cookie NOT Secure | N/A |
| CHARACTERS_FOREIGN | second account POSTs /api/characters to first account's campaign | 404 single body | N/A |
| MOVE_FOREIGN | second account POSTs /api/campaigns/{id}/move (foreign target or foreign library) | 404 single body | N/A |

</frozen-after-approval>

## Code Map

- `backend/app/api/jobs.py` -- the four gap routes: `create_job` (L70), `get_job` (L77), `list_campaign_jobs` (L84), `cancel_job_route` (L97); no auth import today. Store called: `enqueue_job`, `job_status`, `list_jobs`, `cancel_job` — all owner-blind by design.
- `backend/app/api/characters.py` -- the gate precedent: `_require_campaign` L27-32 (get_campaign is None -> HTTPException 404 "Campaign not found."), applied at L67 before enqueue; `get_current_account` dependency pattern.
- `backend/app/store/campaigns.py` -- `get_campaign(owner_id, campaign_id)` L139-145 (None for unknown OR foreign = the as-if-doesn't-exist primitive).
- `backend/app/api/auth.py` -- `_set_session_cookie` L101-116 (secure param), `get_current_account` L118-127; login L173 + register L147 pass `secure=request.url.scheme=="https"`.
- `backend/app/core/settings.py` -- `SESSION_TTL_DAYS` L480-488 is the env-setting pattern; `env_bool_optional` (config.py) is the tri-state reader.
- `backend/app/api/campaigns.py` -- move route L332-351 (target gate L345-346; store checks both campaigns commit.py L1179-1184).
- `backend/tests/test_jobs_api.py` -- fixture `job_api` L63-85 (store-level `_owner_id()`, NO client cookie — the enshrined gap), `_authed_owned_campaign` L43 (API register + cookie + owned campaign), `_post_job` L89, `_assert_envelope` L100. REST tests at L115/L139/L150/L167/L186/L201/L251/L284/L290/L300/L304/L315/L324/L346/L363/L394/L401.
- `backend/tests/test_auth_api.py` -- `_reset_rate_limiter` autouse L23-34; plain-http not-Secure pin L255-281; https Secure pin L79-84.
- `backend/tests/test_edges_api.py` -- `_reset_limiters` autouse L33-41 (the copy-paste source for test_jobs_api).
- `backend/tests/test_direct_character.py` -- `test_gate_requires_ownership` L409-417 (fabricated ULID only — extend with a real second account/campaign).
- `backend/tests/test_generic_library.py` -- move store-level foreign pin L333-347; the API-level move test is NEW (no API move tests exist).
- `backend/tests/test_deploy_contract.py` -- `test_api_binds_loopback_only` L48-63 (extend: assert both compose variants carry MYTHOSCIRCLE_COOKIE_SECURE=true).
- `deploy/docker-compose.yml` + `deploy/docker-compose.portainer.yml` -- api service environment block; add the Secure env (AD-22, not a secret).

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/api/jobs.py` -- add `get_current_account` dependency + ownership gate to all four routes (create/list gate on campaign_id via get_campaign; get/cancel resolve `job_status` first, then gate the job's campaign) -- closes the anonymous/foreign jobs hole
- [x] `backend/tests/test_jobs_api.py` -- rework the REST block so every request rides an owned session (fixture registers via API + creates the campaign for that account); add JOBS_ANON 401 x4, JOBS_FOREIGN create/list/get/cancel 404 pins (foreign cancel asserts row+state untouched) -- replaces the gap-enshrining tests
- [x] `backend/app/core/settings.py` + `backend/app/api/auth.py` -- tri-state `MYTHOSCIRCLE_COOKIE_SECURE`; `_set_session_cookie` resolves override-else-scheme -- AR14 Secure flag on the deployed surface
- [x] `backend/tests/test_auth_api.py` -- COOKIE_TRUE_HTTP + COOKIE_FALSE_HTTPS pins (existing unset pins stay green) -- tri-state pinned
- [x] `deploy/docker-compose.yml` + `deploy/docker-compose.portainer.yml` + `backend/tests/test_deploy_contract.py` -- add `MYTHOSCIRCLE_COOKIE_SECURE: "true"` to both api env blocks; contract test asserts both -- deployed stacks opt in
- [x] `backend/tests/test_direct_character.py` + NEW move API tests -- second-account foreign 404 for POST /api/characters and POST /api/campaigns/{id}/move -- closes the two partial pins
- [x] `_bmad-output/implementation-artifacts/sprint-status.yaml` -- flip 6-4 to review/ready state per flow -- tracked

**Acceptance Criteria:**
- Given a logged-in DM and a second account, when the second account requests any of the four jobs routes against the DM's campaign/job, then the response is 401 without any session or 404 byte-identical to an unknown campaign/job, and no job row, payload, result, or state is ever observable or mutated.
- Given the session cookie being set, when the deployed instance runs with `MYTHOSCIRCLE_COOKIE_SECURE=true`, then the cookie carries Secure over the plain-HTTP origin behind the TLS edge (AR14); when unset, the dev plain-http flow keeps a non-Secure cookie.
- Given the deployed instance, when it is reached, then it is served over TLS (edge) with the generic-401 auth envelope, one owner per campaign holds (owner_id immutable, pinned), and the loopback-only bind is pinned (AD-8, AD-21, AR29).

## Spec Change Log

- 2026-09-20 review round 1 (3 parallel layers: BlindHunter, EdgeHunter, VerificationGap; 0 intent_gap, 0 bad_spec — no loopback; 5 patches applied, 2 deferred, 5 rejected):
  - **Patches**: (P1) `test_both_compose_variants_set_cookie_secure` block-isolation via `text.split("services:")[1].split("web:")[0]` was string surgery that a future pre-api service block (or a "web:" comment) would false-pass/false-fail — now anchors on the exact `\n  api:` → `\n  web:` block. (P2) garbage `MYTHOSCIRCLE_COOKIE_SECURE` raised ValueError per-request in `_set_session_cookie` (every login/register 500s a typo'd deploy); `create_app` now calls `cookie_secure_override()` at boot so a bad value fails loudly at startup, plus `test_cookie_secure_garbage_env_fails_boot`. (P3) list-route "ownership 404 fires before cursor decoding" was position-guarded only — new pin: foreign campaign + fabricated cursor → 404 byte-identical to the no-cursor 404. (P4) move-library test comment misstated its own mechanism (the shared 404 names the TARGET in all branches, commit.py:1184) — corrected with a KEEP note. (P5) spec Verification gained a deployed cookie-attribute observation leg (container-env printenv + owner-curated cookie inspection) — the prior plan never observed AR14's actual deployed outcome. KEEP: the byte-identity pins, the tri-state pins, the no-live-DB-write deployed check, the store-owner-blind boundary.
  - **Deferred** (ledger): store move-404 names the target campaign for any failed ownership condition (misattributes a typo'd/foreign SOURCE as the DM's own target — byte-identity is correct and must survive a message fix); global job-id idempotency key leaves a cross-campaign 409-vs-201 existence oracle.
  - **Rejected**: get/cancel timing side channel (foreign = 2 lookups vs unknown = 1; sub-ms, LAN, body identity is the enforced contract); WS isolation test now same-owner (per-CAMPAIGN isolation is the property; cross-account is the handshake's 4401 gate, still pinned in test_ws_api); I/O matrix JOBS_ANON row already covers the bare-list 401 (same route); "8 of 9 modules" prose is frozen recon narrative (Code Map is the precise contract); env var docs match the SESSION_TTL_DAYS convention (settings.py docstring + compose comments + spec).

## Design Notes

The jobs hole is the mirror of the WS route: `ws.py` gates 4401 + ownership before hub registration; the REST sibling never did. The store must stay owner-blind — the worker's `claim_next_job`/`complete_job`/`fail_job` run without an account (AD-3 single FIFO across all campaigns), so ownership cannot live below the router. `characters.py`'s `_require_campaign` is the exact precedent: check `get_campaign(current.id, campaign_id)` FIRST, raise 404 before the store ever runs, so foreign and unknown produce one identical body.

Get/cancel resolve `job_status(job_id)` first because the route has no campaign_id to gate on; when the resolved job's campaign is not the caller's, raise `JobNotFoundError(job_id)` so the 404 body is byte-identical to a genuinely unknown job — the no-oracle rule one level down (the same rule exports.py:352-354 applies to entities).

Cookie Secure could not be derived from the proxy chain: Caddy's listener is plain `http://world.miscco.uk` (the tunnel speaks HTTP inside), and uvicorn does not honor `X-Forwarded-Proto` from the docker sidecar by default — the origin never learns the public scheme. The env override is the AD-22-correct lever: the operator declares "this origin is behind a TLS edge" explicitly, dev stays untouched, and the tri-state is unit-pinnable.

## Verification

**Commands:**
- `uv run --directory backend pytest tests/test_jobs_api.py tests/test_auth_api.py tests/test_direct_character.py tests/test_campaigns_api.py tests/test_deploy_contract.py -q` -- expected: green incl. the new pins
- `uv run --directory backend pytest -q && make lint && uv run --directory backend mypy app` -- expected: full green (NOTE: `make typecheck` carries pre-existing test drift, see spec-6-2 change log)

**Deployed surface (read-only, no live-DB writes):**
- `curl -s -o /dev/null -w "%{http_code}" https://world.miscco.uk/login` -- 200 over TLS; same for `GET /api/auth/me` WITHOUT cookie -> 401 (pre and post fix).
- Before redeploy: `curl -s -o /dev/null -w "%{http_code}" https://world.miscco.uk/api/jobs` (anon) -> 422 today (query validation first — the gap), after the fix + deploy -> 401. Record the flip as the deployed-surface evidence.
- Post-deploy, the ACTUAL AR14 outcome and the running container env (the curls above observe TLS + the auth envelope, not the cookie flag itself):
  - `ssh homest@192.168.1.21 'docker compose -p mythoscircle -f ~/mythos-redeploy.yml exec api printenv MYTHOSCIRCLE_COOKIE_SECURE'` -> `true` (container-env observation; the env EDIT itself stays Ask-First).
  - Owner-performed cookie-attribute inspection: log in on the deployed origin and confirm the `Set-Cookie` carries `Secure` (browser DevTools, or `curl -i` with the owner's OWN credentials). NEVER a register-curl that writes an account to the live DB.
## Suggested Review Order

**The jobs ownership gate (the hole this story closes)**

- Entry point: the ownership-404-first helpers — foreign and unknown are one byte-identical body (characters/edges precedent)
  [`jobs.py:98`](../../backend/app/api/jobs.py#L98)

- Get/cancel resolve-then-gate: a foreign job 404s as a genuinely unknown job's — the no-oracle rule one level down
  [`jobs.py:107`](../../backend/app/api/jobs.py#L107)

- Create gates the campaign before the store ever runs — no worker is asked to touch a campaign the caller does not own
  [`jobs.py:121`](../../backend/app/api/jobs.py#L121)

- The list route keeps the gate above cursor decoding, so a stranger learns nothing even via the cursor error surface
  [`jobs.py:166`](../../backend/app/api/jobs.py#L166)

- Cancel checks ownership before mutation — a foreign cancel is a 404 and the row/state are untouched
  [`jobs.py:194`](../../backend/app/api/jobs.py#L194)

**The cookie Secure tri-state (AR14 on the deployed surface)**

- The env reader — unset = scheme-derived dev flow, true/false = operator-declared override (AD-22)
  [`settings.py:500`](../../backend/app/core/settings.py#L500)

- The override resolves inside `_set_session_cookie` — override-else-scheme, both login/register ride it
  [`auth.py:112`](../../backend/app/api/auth.py#L112)

- Boot-time validation: a garbage env fails `create_app`, not every login/register (review round 1 patch)
  [`main.py:73`](../../backend/app/main.py#L73)

- Both repo compose variants declare the flag for the deployed stacks (contract-pinned)
  [`docker-compose.yml:37`](../../deploy/docker-compose.yml#L37)

**Pins proving the contract**

- Anonymous 401 on all four routes incl. the bare list (the deployed 422->401 flip)
  [`test_jobs_api.py:333`](../../backend/tests/test_jobs_api.py#L333)

- Foreign campaign create/list 404 — byte-identical to unknown, gate beats cursor validation, zero rows
  [`test_jobs_api.py:350`](../../backend/tests/test_jobs_api.py#L350)

- Foreign job get/cancel 404 — exact-body assertion plus cancel's row/state untouched
  [`test_jobs_api.py:378`](../../backend/tests/test_jobs_api.py#L378)

- Tri-state cookie pins: true-over-http, false-over-https, garbage-fails-boot
  [`test_auth_api.py:284`](../../backend/tests/test_auth_api.py#L284)

- The two missing second-account pins: characters POST + campaigns move (target and library branches)
  [`test_direct_character.py:420`](../../backend/tests/test_direct_character.py#L420)
  [`test_campaigns_api.py:381`](../../backend/tests/test_campaigns_api.py#L381)

- Compose env contract test (anchored to the api service block, review round 1 patch)
  [`test_deploy_contract.py:68`](../../backend/tests/test_deploy_contract.py#L68)
