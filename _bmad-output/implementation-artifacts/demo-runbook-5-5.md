# 5-5 World→VTT Kill-Criterion Demo — Runbook

Status: **draft** (prep measured 2026-09-18 on the live dev box; owner
books the co-driver + date). Epic 5 close gate: 5-5 done flips
`epic-5` → done.

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
| Build-in | | | < 6:30 |
| Generate ask | | | < 2:00 |
| Accept | | | < 0:15 |
| Export downloads | | | < 1:00 |
| Forge paste + portrait | | | < 1:00 |
| MapTool drag-drop | | | < 1:00 |
| **Total** | | | **< 10:00** |

Witness (second human): ________  Signature/OK: ________
World used: ________  Role in the tool: DM ______ / witness ______

## Pre-demo checklist (T-10 min)

- [ ] `mythos-api` ready, `web` (5173) ready; login `dm@example.com`.
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

1. Demo date + co-driver (the two-eyes human). Everything else is
   measured and ready.
2. Which world / live-build-in variant (see checklist).
3. Portrait plan: demo Ilsa Vane as-is, or generate fresh portraits.