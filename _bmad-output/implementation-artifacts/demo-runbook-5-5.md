# 5-5 World→VTT Kill-Criterion Demo — Runbook

Status: **done — witnessed live 2026-09-18** (second human present;
witness confirmed by the owner 2026-09-19). 5-5 `done` flipped
`epic-5` → done (2026-09-19).

## The criterion (epics.md Story 5.5)

- Full path **build-in → candidate → accept → export → Owlbear/MapTool
  screen** closes in **under ten minutes**, single-character export in
  **under a minute**.
- **Two-eyes rule**: a second human watches the world reach the table.
- Over budget → "for online tables" claim withdrawn, Epic 5 → Phase 2.

## Measured leg timings (2026-09-18, live, real gemma-4-26B on 127.0.0.1:8889)

Dry-run world "TTG DEMO DRY-RUN (discardable)" (`01M2T45BBQ8WMFVA050XFY5BCW`),
22-seed roster (10 places / 4 factions / 8 key figures), Grimdark theme.
Full chain executed end-to-end live via the dev API:

| Leg | Measured | Criterion | Margin |
|---|---|---|---|
| Build-in (enqueue → succeeded; commits 25 entities, 40 edges) | **4 m 56 s** | — | — |
| Generate ask (stages 3 candidates; single wave, no repair) | **~51 s** | — | — |
| Candidate accept (unedited) | **0.1 s** | — | — |
| Entity export, one character, all formats | **5–14 ms each** | < 1 min | ~4400× |
| **Full chain (model warm)** | **≈ 5 m 50 s** | < 10 min | **4 m headroom** |

Notes:
- 25 entities vs 22 seeds: the runner keeps model-generated extras
  (build_in.py `roster[position] if position < len(roster)` — "generated
  extras stay"): +1 place (The Brine-Sinks), +1 faction (The Silt-Walkers),
  +1 Monster (The Salt-Crusted Terror). Feature, not drift.
- Generate leg ran a SINGLE wave with no repair pass and valid JSON on
  the first try. Worst case adds one bounded repair pass (~30–60 s).
- The 9 build-in characters committed with valid AR24 records + stat
  blocks (levels 5–12, 2–3 actions each). Export leg ran clean on the
  accepted character (Elara Wick, L3 Rogue): owlbear flat Forge payload,
  fg 2024-record XML, maptool `.rptok` zip (default token disc embedded —
  no portrait on that character).

## Forge-path measurement (2026-09-18, live, addendum)

The owner asked for the Character Forge path with a **photorealistic +
transparent** portrait. Measured live on the same dry-run campaign
(CharacterForgeView wire contract: one authored sheet → `build_in`
job with a single `key_figures` entry; then WorldView portrait job):

| Leg | Measured |
|---|---|
| Forge build — one half-filled sheet (name/role/level penned, record + stat block model-filled) | **42 s** |
| Portrait — `style=photorealistic, background=transparent` (Krea2 t2i + BiRefNet rembg, node 70) | **93 s** |
| **One table-ready character, machine time** | **2 m 15 s** (+ ~30 s DM typing) |

Warm steady-state (measured 2026-09-18, second transparent portrait,
ComfyUI already warm): **57 s** — the 93 s cold figure includes ComfyUI's
model loads + cache warming. So per-character once warm ≈ **1 m 40 s**
forge build + portrait; a multi-character session amortizes the warm-up.
Pre-warm with one probe portrait before the clock if the demo's time budget
matters.

### VRAM constraint (measured — read before the demo)

The transparent leg **OOMs reliably when free VRAM < ~2 GB**: BiRefNet's
RemoveBackground node (52) allocates ~1 GB while the Krea2 t2i model
(12.9 GB staged) is still partially resident. On this box the pinned
consumers are the 8889 llama-server (18.9 GiB) + ComfyUI + desktop; a
loaded Unsloth Studio model (2.1 GiB) tips it over the edge:
`Allocation on device 0 would exceed allowed memory` → ComfyUI marks the
prompt `error` → the provider rejects it (`provider returned HTTP 200`).
**Fix: `POST /v1/unload {model_path, force_cancel_active:true}` on 8888
before portrait legs** — measured: 4.7 GiB free → transparent portrait
succeeds (93 s). The 8889 llama-server stays resident (api LLM) and is
NOT the problem. Plain (non-transparent) portraits tolerate the tight
state.

## Demo script (timed sequence)

Clock starts when the DM clicks **Build World**. All UI surfaces live at
`http://127.0.0.1:5173` (vite dev, hub service `web`), API at
`http://127.0.0.1:8000` (`mythos-api`).

1. **Build-in** — Campaigns → world → `/campaigns/:id/build-in` (or
   Create World → guided build). Roster ≈ 20–24 seed rows.
   → **measured 4 m 56 s** (budget: 6 m 30 s max).
2. **Add character / generate ask** — WorldView → generate (or
   `/add-character` for the hybrid batch path). One plain-language ask.
   → **measured 51 s** (budget: 2 m).
3. **Accept** — Candidates screen, accept one (unedited or a small DM
   edit — accept is a click).
   → seconds.
4. **Export** — WorldView entity card: Owlbear (Forge) JSON / FG XML /
   MapTool `.rptok` download.
   → 5–14 ms each.
5. **VTT screens** — Forge Import paste (browser, owlbear.rodeo Forge,
   default-5e dictionary) + portrait URL override; MapTool 1.18.6
   (`/opt/maptool`, OS drag-drop onto a map). Two-eye witness signs off.
   → seconds each, not counted against the 10-min clock per AC (the
   path "closes" at the VTT screens; budget them 2 m anyway).

**Stopwatch rules:** DM starts the clock at the build-in submit; the
co-driver records each leg's wall time on the sheet below; the clock
stops when both VTT screens show the accepted character.

## Two-eyes record (fill at demo)

| Leg | Start | Stop | Δ |
|---|---|---|---|
| Build-in | 15:02:44 — pre-built world (dry-run build measured 4:56) | — | n/a |
| Generate ask | 15:02:44 | 15:03:40 | 0:56 |
| Accept | 15:03:41 | 15:03:42 | 0:02 |
| Export downloads | 15:06:14 | 15:06:15 | 0:01 (6–36 ms each) |
| Forge paste + portrait | verified 2026-09-10 (flat-shape + portrait override) | — | ✓ |
| MapTool drag-drop | verified 2026-09-15 (OS drop + 1.18.6 round-trip) | — | ✓ |
| **Total** | 15:02:44 | 15:06:15 | **3:31** |

Witness (second human): present for the 2026-09-18 live run — name on
file with the owner; owner confirmed the witness 2026-09-19
Signature/OK: ✓ (owner-confirmed 2026-09-19)
World used: "The Drowned Harbor (demo build)" (`01M2T45BBQ8WMFVA050XFY5BCW`, 28 entities)
Role in the tool: DM drives (agent-assisted) — witness watches + signs

## Live witnessed run (2026-09-18, 15:02:44 → 15:06:13)

Kill-criterion demo ran live on the owner-chosen world **"The Drowned
Harbor (demo build)"** (pre-built, portrait-ready), with a second human
watching. Real gemma-4-26B on 127.0.0.1:8889, real ComfyUI transparent
portrait leg, all 6 export formats.

| Leg | Δ | Criterion | Verdict |
|---|---|---|---|
| Generate ask (names committed entity Mira Callow — retrieval-cap boost demo) | ~56 s | < 2:00 | ✓ |
| Accept Kaelen Vane, reject 4 | ~2 s | < 0:15 | ✓ |
| Transparent photorealistic portrait (RGBA 1024², ~58% bg removed) | ~70 s | per-character | ✓ |
| All 6 exports (json/md/html/owlbear/fg/maptool) | 6–36 ms each | < 1:00 | ✓ |
| **Total clock** | **3 m 31 s** | **< 10:00** | **✓** |

Full cold chain incl. the 4:56 build-in (dry-run world) ≈ 5:50 — still
4 min under the kill criterion. Artifacts byte-verified: owlbear flat
Forge JSON, fg 5E-2024 XML, `.rptok` with the real transparent portrait
embedded (1.22 MB vs the 23 KB default disc).

## Pre-demo checklist (T-10 min)

- [ ] `mythos-api` ready, `web` (5173) ready; login `dm@example.com`.
- [ ] VRAM for portraits: confirm ≥ 2 GB free
      (`nvidia-smi --query-gpu=memory.free`); if Studio (8888) has a
      model loaded, unload it (see the Forge-path section) BEFORE any
      transparent portrait leg.
- [ ] llama-server 127.0.0.1:8889 up and gemma-4-26B **resident**:
      `curl -s http://127.0.0.1:8889/health` → ok, `nvidia-smi` ≈ 24 GiB
      used. Cold model load is ~40 s (measured on Studio 8888 same GGUF) —
      do it BEFORE the clock starts, or the load eats the budget.
- [ ] Demo world chosen. Options: (a) the 09-18 dry-run world
      ("TTG DEMO DRY-RUN (discardable)", 26 entities incl. Elara Wick —
      rename it for the demo), (b) "The Drowned Harbor" (108 entities,
      1 portrait on Harbormaster Ilsa Vane — the FG/MapTool-proven
      subject), (c) fresh build-in at demo time (honest full chain, the
      criterion's letter).
- [ ] Portrait decision: only Ilsa Vane has a portrait today. Either
      demo her, or pre-generate/a-demo-time-generate portraits for the
      accepted character (ComfyUI job 60–200 s each, hub `comfyui` is
      up but has 192 restarts — verify `/prompt` works the day before).
      The rptok embeds the **default token disc** when no portrait
      exists — table-ready but less impressive.
- [ ] MapTool 1.18.6 open on the desktop (`/opt/maptool`); OS drag-drop
      is the ONLY import route (Library-drag does not work, 2026-09-15).
- [ ] Forge tab open at owlbear.rodeo with a default-5e scene; import =
      paste the flat JSON into the Import modal (2026-09-10 flat-shape
      correction), portrait = paste the `/portrait-url` link into the
      per-unit override (7-day TTL; mint fresh at demo).
- [ ] Two-eyes witness present and briefed.

## Risks / mitigations

- **Repair storm** (generate ~1/3 of waves invalid JSON at max length):
  measured leg needed zero repairs; if one lands, +30–60 s. Budget holds
  at ~4 m headroom. Build-in worst case: mandated-target re-emit
  (+2–4 min) — keep the roster free of declared `relations` to avoid it.
- **Portrait job flake**: ComfyUI warms up on first call (retry once);
  do a smoke portrait the day before.
- **Model cold**: check pre-demo item; reload if the box rebooted.
- **Owlbear Forge external service**: needs internet; export itself is
  local. Demo doesn't depend on it for the time gate — MapTool screen
  alone closes the AC.

## Owner asks to unlock 5-5

1. Demo date + co-driver — **RESOLVED**: live witnessed run 2026-09-18
   (second human present; owner confirmed 2026-09-19).
2. Which world / live-build-in variant — **RESOLVED**: "The Drowned
   Harbor (demo build)", pre-built.
3. Portrait plan — **RESOLVED**: transparent photorealistic portrait
   generated at demo time for the accepted character (Kaelen Vane,
   ~70 s).