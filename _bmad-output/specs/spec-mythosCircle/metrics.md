# Metrics — mythosCircle

From the PRD §6 (Success Metrics & Counter-Metrics). Beta cohort is "a few" DMs — percentages are directional, not statistical. Retention is measured per account, gated on the account model (AD-9).

## Success (beta → launch)

1. **Activation** — % of invited DMs who create a world and generate their first NPC in week one.
2. **Core loop** — entities generated per active campaign per week.
3. **Wedge validation (the "living world" feel)** — % of generated entities that receive at least one *manual* relation edit; % of entities with a completed in-tool export (format validated). The two metrics that prove or kill the positioning. VTT-side import success is out of reach — the export-failure counter covers in-tool failures.
4. **Retention** — % of campaigns returning the following week. (Phase 3: % that log session actions after playing.)
5. **Cost health** — generations per entity and per active campaign; free-credit burn rate per week (metered from day one: free metering in beta, no charge).

In-tool counters: export jobs per entity, format-validation pass rate, manual-relation edit rate, credit burn. Retrieval-relevance proxy (NFR 2): % of generated candidates whose top-k retrieved entities appear in the final lore connections.

## Counter-metrics (look good, mean bad)

1. **Regenerate rate** — high full/partial regeneration means generation is missing the mark, even if volume looks healthy.
2. **Free-token burnout** — users who exhaust their weekly free tokens and don't return: the allowance is too small or the value dropped.
3. **Premium-video share** *(post-beta only)* — if almost nobody takes the premium tier, the layering is theater.
4. **Export failure rate** — in-tool export/validation failures on the "take it to the table" path (VTT-side import happens outside the tool; we measure what we can see).
