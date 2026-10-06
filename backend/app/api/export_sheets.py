"""Styled export renderers: pure functions of the export snapshot (spec-5.1).

Everything here renders FROM an already-fetched ``WorldExport`` — no
session, no store read, no wall clock. HTML documents are self-contained
(embedded CSS, optional portrait data-URI, zero external assets, no
``<script>``) so the DM can open them offline and print to PDF; the
Markdown twins stay pure (frontmatter, fences, no inline ``<style>``):
Obsidian sanitizes note HTML and does not render Markdown inside HTML
elements, so the .md is the portable twin and the .html the beautiful one.

Determinism (the byte-identical guarantee, spec-2.6): every renderer is a
pure function of the snapshot plus file bytes on disk; ``exported_at``
stays the snapshot timestamp, never a clock read. Unknown committed keys
are never dropped: entity HTML carries a verbatim JSON appendix and the
Markdown carries the full ``data`` fence (AR24 forward-compat, FR5/NFR10).
"""

from __future__ import annotations

import base64
import hashlib
import html as _html
import io
import json
import math
import re
import uuid
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.core.settings import configured_media_dir
from app.media.service import PNG_SIGNATURE
from app.pipeline.statblocks import damage_parts_sentence
from app.store.commit import edge_counter_semantic

if TYPE_CHECKING:
    from app.api.exports import EdgeExport, EntityExport, WorldExport

#: A portrait larger than this is captioned, never embedded (the sheet
#: must stay a light document; the spec-5.1 matrix pins the boundary).
MAX_INLINE_BYTES = 4 * 1024 * 1024

#: Extension -> MIME for data-URI embedding. Anything else is not embedded.
_IMAGE_MIME: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

#: Top-level ``data`` keys rendered into the sheet header's identity line
#: (the AR24 anchor) instead of the body sections.
_IDENTITY_KEYS = ("role", "level_cr", "race_type", "class_profession", "alignment")

#: The committed stat block renders as the dedicated panel, not generically.
_STAT_BLOCK_KEY = "stat_block"

SHEET_CSS = """\
:root {
  color-scheme: light;
  --ink: #28211c;
  --muted: #74645a;
  --red: #8e2926;
  --rule: #b78261;
  --paper: #fffaf0;
  --wash: #f2e6d3;
}
@page {
  size: auto;
  margin: 15mm 14mm;
}
* {
  box-sizing: border-box;
  -webkit-print-color-adjust: exact;
  print-color-adjust: exact;
}
body {
  font-family: "Iowan Old Style", "Palatino Linotype", Palatino, Georgia, serif;
  color: var(--ink);
  background: #e9e2d8;
  margin: 0 auto;
  max-width: 1100px;
  padding: clamp(1rem, 4vw, 3.5rem);
  line-height: 1.5;
  font-size: 11.5pt;
}
h1,
  h2,
  h3,
  h4,
  p {
  margin-top: 0;
}
h1 {
  font-size: clamp(2rem, 5vw, 3.4rem);
  line-height: 1.02;
  margin: 0 0 .55rem;
  color: #241e1a;
  letter-spacing: -.025em;
}
h2 {
  color: var(--ink);
  font-size: 1.4rem;
  margin: 0 0 .3rem;
  break-after: avoid;
}
h3 {
  color: var(--red);
  font-size: .84rem;
  letter-spacing: .13em;
  text-transform: uppercase;
  margin: 1rem 0 .45rem;
  break-after: avoid;
}
h4 {
  color: var(--red);
  font-size: .91rem;
  margin: .8rem 0 .25rem;
  break-after: avoid;
}
.meta {
  color: var(--muted);
  font-size: .9rem;
  margin: .1rem 0;
}
.small {
  font-size: .8rem;
}
.mono {
  font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
}
.doc-header {
  position: relative;
  background: var(--paper);
  border: 1px solid #d6c6ad;
  border-top: 5px solid var(--red);
  box-shadow: 0 10px 32px #49362612;
  margin-bottom: 1.4rem;
  padding: clamp(1.25rem, 4vw, 2.5rem);
}
.doc-kicker {
  color: var(--red);
  font: 700 .68rem/1.2 ui-sans-serif, system-ui, sans-serif;
  letter-spacing: .2em;
  text-transform: uppercase;
  margin: 0 0 .65rem;
}
.entity-card {
  position: relative;
  background: var(--paper);
  border: 1px solid #d6c6ad;
  border-top: 4px solid var(--rule);
  box-shadow: 0 5px 20px #4936260d;
  padding: clamp(1rem, 3vw, 2rem);
  margin: 0 0 1.3rem;
  break-inside: auto;
}
.entity-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
  padding-bottom: .75rem;
  border-bottom: 1px solid #dfd1bb;
  margin-bottom: .9rem;
}
.entity-kind {
  display: inline-block;
  color: var(--red);
  font: 700 .68rem/1.2 ui-sans-serif, system-ui, sans-serif;
  letter-spacing: .14em;
  text-transform: uppercase;
  border: 1px solid #d2ae91;
  padding: .32rem .5rem;
  white-space: nowrap;
}
.hero {
  float: right;
  width: min(34%, 250px);
  margin: 0 0 1rem 1.25rem;
  break-inside: avoid;
}
.hero img {
  display: block;
  width: 100%;
  max-height: 11cm;
  object-fit: cover;
  object-position: top;
  border: 5px solid #fff;
  outline: 1px solid #cdbb9f;
  box-shadow: 0 5px 18px #33251b24;
}
.prose {
  white-space: pre-line;
  margin: .35rem 0 .7rem;
}
.entity-lead {
  font-size: 1.04rem;
  line-height: 1.58;
  max-width: 70ch;
}
.character-notes {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: .65rem;
  clear: both;
}
.character-notes section {
  border-top: 1px solid #dfd1bb;
  padding: .7rem .15rem .25rem;
  break-inside: avoid;
}
.character-notes h3 {
  margin: 0 0 .35rem;
  font-size: .68rem;
}
.character-notes .note-appearance,
  .character-notes .note-background,
  .character-notes .note-catchphrases {
  grid-column: 1 / -1;
}
.setting-facts {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: .65rem;
  margin: 1rem 0;
}
.setting-facts section {
  padding: .8rem 1rem;
  background: #f6eddf;
  border-left: 3px solid #bc8968;
  break-inside: avoid;
}
.setting-facts h3 {
  margin: 0 0 .35rem;
  font-size: .67rem;
}
.stat-block {
  --sb-ink: #29231e;
  --sb-red: #8e2926;
  background: #fff9ed;
  color: var(--sb-ink);
  border: 1px solid #c9a987;
  border-top: 5px solid var(--sb-red);
  border-bottom: 5px solid var(--sb-red);
  padding: .85rem 1.05rem;
  margin: 1rem 0 1.2rem;
  box-shadow: 0 5px 18px #46301a12;
  break-inside: auto;
}
.stat-title {
  color: var(--sb-red);
  font-size: 1.65rem;
  line-height: 1.1;
  margin: 0 0 .18rem;
  text-transform: none;
  letter-spacing: -.02em;
}
.stat-subtitle {
  font-style: italic;
  margin: 0 0 .6rem;
}
.stat-divider {
  height: 4px;
  border: 0;
  background: linear-gradient(90deg, var(--sb-red), #c99570 65%, transparent);
  margin: .55rem 0;
}
.stat-facts {
  display: flex;
  flex-wrap: wrap;
  gap: .25rem 1.1rem;
  padding: .15rem 0;
}
.stat-fact {
  min-width: 7rem;
}
.stat-fact strong,
  .stat-term {
  color: var(--sb-red);
}
.stat-abilities {
  width: 100%;
  border-collapse: collapse;
  text-align: center;
  margin: .45rem 0;
}
.stat-abilities th {
  color: var(--sb-red);
  font: 700 .72rem ui-sans-serif, system-ui, sans-serif;
  letter-spacing: .08em;
  padding: .25rem;
  border-bottom: 1px solid #d8bea1;
}
.stat-abilities td {
  padding: .3rem .2rem;
  border-bottom: 1px solid #eadcc8;
  font-weight: 700;
}
.stat-abilities small {
  display: block;
  color: var(--muted);
  font-weight: 400;
}
.stat-line {
  margin: .42rem 0;
}
.stat-entry {
  margin: .5rem 0;
  break-inside: avoid;
}
.stat-entry strong {
  font-style: italic;
}
.stat-label {
  color: var(--sb-red);
  font-weight: 700;
}
.stat-foot {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: .3rem 1rem;
  margin-top: .6rem;
}
.abilities {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: .4rem;
  text-align: center;
  margin: .5rem 0;
}
.ab-k {
  display: block;
  font-weight: 700;
  font-size: .8rem;
  letter-spacing: .06em;
  color: var(--red);
}
.ab-v {
  display: block;
  font-size: 1.05rem;
}
dl.fields {
  display: grid;
  grid-template-columns: minmax(8rem, 1fr) 3fr;
  gap: .15rem .75rem;
  margin: .4rem 0;
}
dl.fields dt {
  font-weight: 700;
  color: #59463a;
  font-size: .88rem;
}
dl.fields dd {
  margin: 0;
  overflow-wrap: anywhere;
}
ul.entries {
  margin: .3rem 0;
  padding-left: 1.2rem;
}
ul.entries li {
  margin: .18rem 0;
  break-inside: avoid;
}
.relations,
  .media {
  font-size: .94rem;
}
.badge {
  color: var(--red);
  font-weight: 700;
}
.world-section {
  margin: 1.8rem 0;
}
.world-section-title {
  display: flex;
  align-items: baseline;
  gap: .75rem;
  padding-bottom: .5rem;
  border-bottom: 2px solid var(--red);
  margin-bottom: .8rem;
}
.world-section-title h2 {
  margin: 0;
}
.world-section-title span {
  color: var(--muted);
  font: 700 .7rem ui-sans-serif, system-ui, sans-serif;
  letter-spacing: .13em;
  text-transform: uppercase;
}
.world-section .entity-card {
  break-inside: auto;
}
.world-section .character-notes {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}
.world-section .character-notes .note-catchphrases {
  display: none;
}
.world-index {
  display: flex;
  flex-wrap: wrap;
  gap: .45rem;
  list-style: none;
  padding: 0;
}
.world-index a {
  display: inline-block;
  padding: .35rem .65rem;
  background: var(--paper);
  border: 1px solid #d6c6ad;
  text-decoration: none;
}
table.edges {
  border-collapse: collapse;
  width: 100%;
  margin: .6rem 0;
  background: var(--paper);
}
table.edges th,
  table.edges td {
  border: 1px solid #d6c6ad;
  padding: .45rem .6rem;
  text-align: left;
  font-size: .9rem;
}
table.edges th {
  background: #3b302a;
  color: #fff9ed;
  font: 700 .7rem ui-sans-serif, system-ui, sans-serif;
  letter-spacing: .1em;
  text-transform: uppercase;
}
table.edges tr {
  break-inside: avoid;
}
details.appendix {
  margin-top: 1.2rem;
  padding-top: .7rem;
  border-top: 1px solid #d6c6ad;
  font-size: .82rem;
}
details.appendix pre {
  white-space: pre-wrap;
  word-break: break-word;
  background: #f2e6d3;
  padding: .8rem;
  overflow-wrap: anywhere;
}
a {
  color: inherit;
}
p {
  orphans: 3;
  widows: 3;
}
@media (max-width: 640px) {
  body { padding: .75rem; }
  .hero { float: none; width: min(100%, 300px); margin: 0 auto 1rem; }
  .character-notes, .setting-facts, .stat-foot { grid-template-columns: 1fr; }
  .character-notes .note-appearance, .character-notes .note-background,
  .character-notes .note-catchphrases { grid-column: auto; }
  dl.fields { grid-template-columns: 1fr; gap: 0; }
  dl.fields dd { margin: 0 0 .35rem; }
}
"""


def _yaml_scalar(value: str) -> str:
    """A double-quoted YAML scalar — deterministic and frontmatter-safe.

    Campaign titles/descriptions are free text (colons, quotes, newlines,
    carriage returns, any control character). JSON string escaping IS a
    valid YAML double-quoted scalar and round-trips every character, so
    the frontmatter always parses in Obsidian.
    """
    return json.dumps(value, ensure_ascii=False)


def _edge_label(edge: EdgeExport) -> str:
    """The Relations-line edge label (spec-2.6 Design Notes): debt shows
    the amount, grudge/loyalty the score, ally/enemy the intensity; the
    neutral types' informational counter renders bare (AD-23 semantics)."""
    if edge_counter_semantic(edge.type) == "neutral":
        return str(edge.type)
    return f"{edge.type}({edge.counter})"


_RESERVED_HEADINGS = frozenset({"Edges", "Relations"})
_WIKI_UNSAFE = re.compile(r"[\[\]|#^\n\r]")


def _unique_label(base: str, entity_id: str, used: set[str]) -> str:
    """A label unique against ``used``, grown from the short-id
    discriminator. Full ULIDs are unique, so the loop terminates."""
    for width in range(4, len(entity_id) + 1):
        candidate = f"{base} ({entity_id[-width:]})"
        if candidate not in used:
            return candidate
    raise AssertionError("unreachable: full ULIDs are unique")


def name_labels(export: WorldExport) -> dict[str, str]:
    """Display label per entity id. Entity names are neither unique (the
    store constrains staged ULIDs, not names) nor wikilink-safe. Uniqueness
    is enforced *after* sanitization — raw-name dedup alone lets distinct
    names ("A[B", "A]B") collapse into one label or an all-unsafe name go
    blank — by growing the short-id discriminator; structural headings
    ("Edges", "Relations") are reserved and get a discriminator too.
    Applied consistently to headings, wikilinks, and table cells."""
    counts = Counter(entity.name for entity in export.entities)
    labels: dict[str, str] = {}
    used: set[str] = set()
    for entity in export.entities:
        base = _WIKI_UNSAFE.sub(" ", entity.name).strip() or entity.id
        if counts[entity.name] == 1 and base not in used and base not in _RESERVED_HEADINGS:
            labels[entity.id] = base
        else:
            labels[entity.id] = _unique_label(base, entity.id, used)
        used.add(labels[entity.id])
    return labels


def _longest_backtick_run(text: str) -> int:
    runs = (match.group() for match in re.finditer(r"`+", text))
    return max((len(run) for run in runs), default=0)


def exported_at(export: WorldExport) -> str:
    """The snapshot's own timestamp — the latest revision's ``created_at``,
    or the campaign's creation for an empty world. Deliberately NOT a
    wall-clock read: repeated exports must be byte-identical."""
    if export.revision is not None:
        return str(export.revision.created_at)
    return str(export.campaign.created_at)


def _data_fence(entity: EntityExport) -> list[str]:
    """The entity's full ``data`` verbatim in a yaml fence (JSON is a
    valid YAML flow subset). The fence outgrows any backtick run inside
    the payload, so data containing ``` can never terminate it early."""
    data_json = json.dumps(entity.data, indent=2, ensure_ascii=False)
    fence = "`" * max(3, _longest_backtick_run(data_json) + 1)
    return ["", f"{fence}yaml", data_json, fence]


def _media_lines(entity: EntityExport) -> list[str]:
    """The Markdown Media block (spec-4.3 honesty rule): every row,
    broken references flagged, never dropped."""
    if not entity.media:
        return []
    lines = ["", "### Media", ""]
    for ref in entity.media:
        broken = "" if ref.available else " (broken: file missing on disk)"
        lines.append(f"- {ref.kind}: {ref.filename}{broken}")
    return lines


def _relations_lines(export: WorldExport, entity_id: str, names: dict[str, str]) -> list[str]:
    """The entity's Relations block with ``[[wikilinks]]`` (the world
    document — neighbors are sections of the same file)."""
    outbound = [edge for edge in export.edges if edge.src == entity_id]
    inbound = [edge for edge in export.edges if edge.dst == entity_id]
    if not (outbound or inbound):
        return []
    lines = ["", "### Relations", ""]
    for edge in outbound:
        lines.append(
            f"- [[{names.get(edge.src, edge.src)}]]"
            f" --{_edge_label(edge)}--> [[{names.get(edge.dst, edge.dst)}]]"
        )
    for edge in inbound:
        lines.append(
            f"- [[{names.get(edge.dst, edge.dst)}]]"
            f" <--{_edge_label(edge)}-- [[{names.get(edge.src, edge.src)}]]"
        )
    return lines


def render_world_markdown(export: WorldExport) -> str:
    """The Obsidian document for an already-fetched export snapshot.

    A pure function of the rows — session assembly (the store reads) is
    separated from string building; this never touches a session.
    """
    names = name_labels(export)
    lines = [
        "---",
        f"campaign_id: {export.campaign.id}",
        f"title: {_yaml_scalar(export.campaign.title)}",
        f"theme: {_yaml_scalar(export.campaign.theme)}",
        f"description: {_yaml_scalar(export.campaign.description)}",
        f"custom_lore: {_yaml_scalar(export.campaign.custom_lore)}",
        f"revision: {export.revision.id if export.revision is not None else 'null'}",
        f"exported_at: {_yaml_scalar(exported_at(export))}",
        "---",
        "",
    ]
    for entity in export.entities:
        lines += [f"## {names[entity.id]}", "", f"kind: {entity.kind}"]
        if entity.text is not None:
            lines += ["", str(entity.text)]
        lines += _data_fence(entity)
        lines += _relations_lines(export, entity.id, names)
        lines += _media_lines(entity)
        lines.append("")
    if export.edges:
        lines += [
            "## Edges",
            "",
            "| source | type | counter | target |",
            "| --- | --- | --- | --- |",
        ]
        for edge in export.edges:
            src = names.get(edge.src, edge.src)
            dst = names.get(edge.dst, edge.dst)
            lines.append(f"| [[{src}]] | {edge.type} | {edge.counter} | [[{dst}]] |")
        lines.append("")
    return "\n".join(lines)


def render_entity_markdown(export: WorldExport, entity_id: str) -> str:
    """The single-entity Markdown twin: same completeness contract (full
    ``data`` fence, relations, media) but neighbor names render as plain
    text — a standalone file has no wikilink targets inside it. A format
    assertion failure means the entity is not in the snapshot, which is
    the route's 404, raised before this renderer is ever called."""
    names = name_labels(export)
    entity = next(e for e in export.entities if e.id == entity_id)
    lines = [
        "---",
        f"entity_id: {entity.id}",
        f"campaign_id: {export.campaign.id}",
        f"campaign: {_yaml_scalar(export.campaign.title)}",
        f"name: {_yaml_scalar(entity.name)}",
        f"kind: {entity.kind}",
        f"revision: {export.revision.id if export.revision is not None else 'null'}",
        f"exported_at: {_yaml_scalar(exported_at(export))}",
        "---",
        "",
        f"# {names[entity.id]}",
        "",
        f"kind: {entity.kind}",
    ]
    if entity.text is not None:
        lines += ["", str(entity.text)]
    lines += _data_fence(entity)
    outbound = [edge for edge in export.edges if edge.src == entity.id]
    inbound = [edge for edge in export.edges if edge.dst == entity.id]
    if outbound or inbound:
        lines += ["", "### Relations", ""]
    subject = names.get(entity.id, entity.id)
    for edge in outbound:
        neighbor = names.get(edge.dst, edge.dst)
        lines.append(f"- {subject} --{_edge_label(edge)}--> {neighbor}")
    for edge in inbound:
        neighbor = names.get(edge.src, edge.src)
        lines.append(f"- {neighbor} --{_edge_label(edge)}--> {subject}")
    lines += _media_lines(entity)
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# HTML sheets
# ---------------------------------------------------------------------------

#: The six ability scores in canonical order (SRD 5.1 field set).
_ABILITY_ORDER: tuple[str, ...] = ("str", "dex", "con", "int", "wis", "cha")

#: Display order for stat-block parts — PRESENTATION ONLY: committed data is
#: never reordered (the appendix keeps the exact shape). Models and the
#: repair pass commit keys in varying order; the sheet must not look
#: different because of it (dogfood fix 2026-09-09). The optional aspects
#: (spec 2026-09-11) slot into that order without disturbing it: saves /
#: initiative / passive perception / proficiency follow combat, spellcasting
#: follows spells, and resources close the panel.
_STAT_BLOCK_ORDER: tuple[str, ...] = (
    "identity",
    "attributes",
    "combat",
    "saves",
    "initiative",
    "passive_perception",
    "proficiency_bonus",
    "skills",
    "actions",
    "traits",
    "spells",
    "spellcasting",
    "features",
    "resources",
)

#: Field order for the known section dicts; unknown keys follow in
#: committed order.
_KEY_ORDER: dict[str, tuple[str, ...]] = {
    "attributes": _ABILITY_ORDER,
    # Saves are an ability-score map: canonical order, never commit order.
    "saves": _ABILITY_ORDER,
    "identity": ("role", "level", "cr", "race", "class", "alignment"),
    # ``hit_dice`` rides with hp — the 5e idiom "hp (24d10 + 192)".
    "combat": ("ac", "armor_class", "hp", "hit_points", "hit_dice", "speed", "initiative"),
    "spellcasting": ("dc", "attack_bonus", "slots"),
    "world_integration": (
        "reputation",
        "factions",
        "current_location",
        "reaction_matrix",
        "on_defeat",
    ),
    "boss": ("lair_actions", "legendary_actions", "immunities", "vulnerabilities"),
}


def _ordered(value: dict[str, Any], order: tuple[str, ...]) -> list[tuple[str, Any]]:
    """Known keys first in ``order``, the rest in committed order."""
    picked = [(key, value[key]) for key in order if key in value]
    seen = {key for key, _ in picked}
    return picked + [(key, item) for key, item in value.items() if key not in seen]


def _esc(value: Any) -> str:
    return _html.escape(str(value), quote=False)


def _label(key: str) -> str:
    """Humanized section label ('on_defeat' -> 'On defeat')."""
    labels = {
        "whats_hidden": "What's hidden",
        "cr": "Challenge rating",
        "hp": "Hit points",
        "ac": "Armor class",
    }
    return labels.get(str(key), str(key).replace("_", " ").capitalize())


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _inline(value: Any) -> str:
    """One-line rendering of any value: scalars verbatim, composites as
    compact deterministic JSON (never re-ordered, never dropped)."""
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return _esc(value)
    return _esc(json.dumps(value, ensure_ascii=False, separators=(", ", ": ")))


def _render_list(value: list[Any]) -> str:
    items = []
    for item in value:
        if isinstance(item, dict):
            name = item.get("name")
            # A string name leads the entry in bold; any other name value
            # stays in the field list — nothing committed is dropped.
            if isinstance(name, str):
                head = f"<strong>{_esc(name)}.</strong>"
                rest = {k: v for k, v in item.items() if k != "name"}
            else:
                head = ""
                rest = dict(item)
            parts = "; ".join(f"{_esc(_label(str(k)))}: {_inline(v)}" for k, v in rest.items())
            joined = " ".join(part for part in (head, parts) if part)
            items.append(f"<li>{joined or _inline(item)}</li>")
        else:
            items.append(f"<li>{_inline(item)}</li>")
    return f'<ul class="entries">{"".join(items)}</ul>'


def _render_mapping(value: dict[str, Any], order: tuple[str, ...] = ()) -> str:
    parts = []
    for key, item in _ordered(value, order):
        if _is_scalar(item):
            parts.append(f"<dt>{_esc(_label(str(key)))}</dt><dd>{_inline(item)}</dd>")
        else:
            parts.append(f"<dt>{_esc(_label(str(key)))}</dt><dd>{_render_value(item)}</dd>")
    return f'<dl class="fields">{"".join(parts)}</dl>'


def _render_value(value: Any, key: str = "") -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return f'<p class="prose">{_esc(value)}</p>'
    if isinstance(value, list):
        return _render_list(value)
    if isinstance(value, dict):
        return _render_mapping(value, _KEY_ORDER.get(str(key), ()))
    return f"<p>{_inline(value)}</p>"


def _abilities_grid(value: dict[str, Any]) -> str:
    cells = [
        f'<div class="ab"><span class="ab-k">{_esc(str(k).upper())}</span>'
        f'<span class="ab-v">{_inline(v)}</span></div>'
        for k, v in _ordered(value, _ABILITY_ORDER)
    ]
    return f'<div class="abilities">{"".join(cells)}</div>'


def _stat_modifier(value: Any) -> str:
    number = _forge_number(value)
    if number is None:
        return ""
    modifier = math.floor((number - 10) / 2)
    return f" ({modifier:+d})"


def _signed_number(value: Any) -> str:
    number = _forge_number(value)
    if number is None:
        return ""
    return f"{number:+g}"


def _stat_entries(value: Any) -> str:
    if not isinstance(value, list):
        return _render_value(value)
    entries: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            entries.append(f'<p class="stat-entry">{_inline(item)}</p>')
            continue
        name = item.get("name")
        description = item.get("description")
        sentence = damage_parts_sentence(item.get("damage"))
        if (
            sentence is not None
            and isinstance(description, str)
            and not _states_damage_dice(description, sentence)
        ):
            description = f"{description} {sentence}" if description else sentence
        if sentence is not None and not isinstance(description, str):
            description = sentence
        detail = _inline(description) if description is not None else ""
        attack = _forge_number(item.get("to_hit"))
        attack_text = (
            f" <span class='stat-label'>{_signed_number(attack)} to hit.</span>"
            if attack is not None
            else ""
        )
        title = f"<strong>{_esc(name)}.</strong>" if isinstance(name, str) and name else ""
        entries.append(f'<p class="stat-entry">{title}{attack_text} {detail}</p>')
    return "".join(entries)


def _stat_block_panel(stat_block: Any, data: dict[str, Any] | None = None) -> str:
    """A familiar 5e-inspired stat block, rendered from the committed
    values while leaving the original record untouched in the appendix."""
    if not isinstance(stat_block, dict):
        return f"<section>{_render_value(stat_block)}</section>"
    data = data or {}
    identity_value = stat_block.get("identity")
    identity: dict[str, Any] = identity_value if isinstance(identity_value, dict) else {}
    attributes_value = stat_block.get("attributes")
    attributes: dict[str, Any] = attributes_value if isinstance(attributes_value, dict) else {}
    combat_value = stat_block.get("combat")
    combat: dict[str, Any] = combat_value if isinstance(combat_value, dict) else {}
    saves_value = stat_block.get("saves")
    saves: dict[str, Any] = saves_value if isinstance(saves_value, dict) else {}
    role = identity.get("role") or data.get("role")
    race = identity.get("race") or data.get("race_type")
    size = stat_block.get("size") or data.get("size")
    cls = identity.get("class") or data.get("class_profession")
    alignment = identity.get("alignment") or data.get("alignment")
    level_or_cr = identity.get("level") if identity.get("level") is not None else identity.get("cr")
    if role == "Monster":
        label = f"Challenge {level_or_cr}" if level_or_cr is not None else "Creature"
        creature_type = ", ".join(str(part) for part in (size, race, alignment) if part)
        subtitle = " · ".join(part for part in (creature_type, label) if part)
    else:
        label = (
            f"{role or 'Character'}{f' · Level {level_or_cr}' if level_or_cr is not None else ''}"
        )
        subtitle = " · ".join(
            str(part)
            for part in (
                label,
                " ".join(str(part) for part in (race, cls) if part),
                str(alignment) if alignment else "",
            )
            if part
        )

    parts = [
        f'<h3 class="stat-title">{_esc(str(data.get("name") or "Stat Block"))}</h3>',
        f'<p class="stat-subtitle">{_esc(subtitle or label)}</p>',
        '<hr class="stat-divider"/>',
    ]
    facts: list[tuple[str, Any]] = []
    for label_text, candidates in (
        ("Armor Class", ("ac", "armor_class")),
        ("Hit Points", ("hp", "hit_points")),
        ("Hit Dice", ("hit_dice",)),
        ("Speed", ("speed",)),
        ("Initiative", ("initiative",)),
    ):
        value = next((combat[key] for key in candidates if combat.get(key) is not None), None)
        if value is None and label_text == "Speed":
            value = stat_block.get("speed")
        if value is None and label_text == "Initiative":
            value = stat_block.get("initiative")
        if value is not None:
            facts.append((label_text, value))
    if facts:
        parts.append(
            '<div class="stat-facts">'
            + "".join(
                f'<div class="stat-fact"><strong>{_esc(label_text)}</strong> {_inline(value)}</div>'
                for label_text, value in facts
            )
            + "</div>"
        )
    if attributes:
        parts.append(
            '<table class="stat-abilities"><thead><tr>'
            + "".join(
                f"<th>{_esc(key.upper())}</th>" for key in _ABILITY_ORDER if key in attributes
            )
            + "</tr></thead><tbody><tr>"
            + "".join(
                f"<td>{_inline(attributes[key])}<small>{_esc(_stat_modifier(attributes[key]))}</small></td>"
                for key in _ABILITY_ORDER
                if key in attributes
            )
            + "</tr></tbody></table>"
        )

    compact_fields = (
        ("Saving Throws", saves),
        ("Skills", stat_block.get("skills")),
        ("Damage Vulnerabilities", stat_block.get("damage_vulnerabilities")),
        ("Damage Resistances", stat_block.get("damage_resistances")),
        ("Damage Immunities", stat_block.get("damage_immunities")),
        ("Condition Immunities", stat_block.get("condition_immunities")),
        ("Senses", stat_block.get("senses")),
        ("Languages", stat_block.get("languages")),
        ("Passive Perception", stat_block.get("passive_perception")),
        ("Proficiency Bonus", stat_block.get("proficiency_bonus")),
    )
    for label_text, value in compact_fields:
        if value is None or value == [] or value == {} or value == "":
            continue
        if label_text == "Saving Throws" and isinstance(value, dict):
            rendered = ", ".join(
                f"{_esc(str(key).upper())} {_signed_number(bonus)}"
                for key, bonus in _ordered(value, _ABILITY_ORDER)
            )
        elif label_text == "Skills" and isinstance(value, list):
            rendered = ", ".join(
                f"{_esc(item.get('name', ''))} {_signed_number(item.get('bonus'))}"
                if isinstance(item, dict) and _forge_number(item.get("bonus")) is not None
                else _inline(item.get("name", item) if isinstance(item, dict) else item)
                for item in value
            )
        elif isinstance(value, list):
            rendered = ", ".join(_inline(item) for item in value)
        elif isinstance(value, dict):
            rendered = ", ".join(f"{_esc(_label(str(k)))} {_inline(v)}" for k, v in value.items())
        else:
            rendered = _inline(value)
        parts.append(
            '<p class="stat-line"><strong class="stat-term">'
            f"{_esc(label_text)}</strong> {rendered}</p>"
        )

    known = {
        "identity",
        "attributes",
        "combat",
        "saves",
        "skills",
        "damage_vulnerabilities",
        "damage_resistances",
        "damage_immunities",
        "condition_immunities",
        "senses",
        "languages",
        "passive_perception",
        "proficiency_bonus",
        "speed",
        "initiative",
    }
    for key in ("traits", "actions", "spells", "features", "resources"):
        known.add(key)
        value = stat_block.get(key)
        if not value:
            continue
        parts.append(f'<hr class="stat-divider"/><h4>{_esc(_label(key))}</h4>')
        parts.append(
            _stat_entries(value)
            if key in {"traits", "actions", "features"}
            else _render_value(value)
        )

    spellcasting = stat_block.get("spellcasting")
    known.add("spellcasting")
    if isinstance(spellcasting, dict) and spellcasting:
        parts.append('<hr class="stat-divider"/><h4>Spellcasting</h4>')
        parts.append(_render_mapping(spellcasting, _KEY_ORDER["spellcasting"]))
    boss = data.get("boss")
    if isinstance(boss, dict) and boss:
        parts.append('<hr class="stat-divider"/><h4>Legendary Actions &amp; Lair</h4>')
        parts.append(_render_mapping(boss, _KEY_ORDER["boss"]))
    extra = [
        (key, value) for key, value in _ordered(stat_block, _STAT_BLOCK_ORDER) if key not in known
    ]
    for key, value in extra:
        parts.append(
            f'<hr class="stat-divider"/><h4>{_esc(_label(key))}</h4>{_render_value(value, key)}'
        )
    return (
        '<section class="stat-block" data-entity-part="stat_block">' + "".join(parts) + "</section>"
    )


def _hero_portrait_src(export: WorldExport, entity: EntityExport) -> str | None:
    """Data-URI for the newest AVAILABLE image row (rowid order — the UI's
    portraitFor() picks by created_at and ignores availability; the two
    rules converge while media is singly-kept, and the divergence is
    deferred with the retention ruling). Size-checked via stat() BEFORE
    reading: an oversized file is captioned, never slurped into memory.
    A read error is a broken reference, not a crash — degrade to
    caption; the Media block flags it honestly."""
    candidates = [row for row in entity.media if row.kind == "image" and row.available]
    if not candidates:
        return None
    row = candidates[-1]
    mime = _IMAGE_MIME.get(Path(row.filename).suffix.lower())
    if mime is None:
        return None
    path = Path(configured_media_dir()) / export.campaign.id / entity.id / row.filename
    try:
        if path.stat().st_size > MAX_INLINE_BYTES:
            return None
        raw = path.read_bytes()
    except OSError:
        return None
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def _media_block(export: WorldExport, entity: EntityExport) -> str:
    """Every manifest row, listed by its on-disk path (the portable
    reference) with the availability flag — never dropped, never hidden."""
    if not entity.media:
        return ""
    items = []
    for row in entity.media:
        state = ""
        if not row.available:
            state = ' <span class="badge">(broken: file missing on disk)</span>'
        path_note = f"media/{export.campaign.id}/{entity.id}/{row.filename}"
        items.append(
            f"<li>{_esc(row.kind)}: <span class='mono'>{_esc(path_note)}</span>{state}</li>"
        )
    return f"<section><h2>Media</h2><ul class='media'>{''.join(items)}</ul></section>"


def _hero_figure(export: WorldExport, entity: EntityExport) -> str:
    """The embedded portrait, or nothing (broken / oversized / unreadable
    files stay captioned in the Media block — never faked). The alt text
    is attribute-escaped: names carry quotes, and an unescaped " breaks
    out of alt= into a live event-handler slot."""
    src = _hero_portrait_src(export, entity)
    if src is None:
        return ""
    alt = _html.escape(str(entity.name))
    return f'<figure class="hero"><img src="{src}" alt="{alt}"/></figure>'


def _entity_sections(
    export: WorldExport,
    entity: EntityExport,
    names: dict[str, str],
    embed: bool,
    appendix: bool,
) -> str:
    data = entity.data if isinstance(entity.data, dict) else {}
    identity_consumed = {
        key for key in _IDENTITY_KEYS if isinstance(data.get(key), str) and data[key]
    }
    identity_bits = [str(data[key]) for key in _IDENTITY_KEYS if key in identity_consumed]
    kind = str(entity.kind).lower()
    article_id = f' id="entity-{_html.escape(entity.id, quote=True)}"' if not appendix else ""
    heading = (
        f'<header class="entity-heading"><div><p class="doc-kicker">{_esc(kind)} dossier</p>'
        f"<h2>{_esc(names[entity.id])}</h2>"
        + (f'<p class="meta">{_esc(" · ".join(identity_bits))}</p>' if identity_bits else "")
        + f'</div><span class="entity-kind">{_esc(kind)}</span></header>'
    )
    body: list[str] = []
    if embed:
        body.append(_hero_figure(export, entity))
    if entity.text is not None:
        body.append(f'<p class="prose entity-lead">{_esc(entity.text)}</p>')

    if kind == "character" and _STAT_BLOCK_KEY in data:
        body.append(_stat_block_panel(data[_STAT_BLOCK_KEY], data))

    profile_keys = (
        "appearance",
        "personality",
        "background",
        "goals",
        "relationships",
        "secret",
        "rumor",
        "party_hook",
        "voice_style",
        "catchphrases",
    )
    profile: list[str] = []
    used = set(identity_consumed) | {"name"}
    if kind == "character":
        used.add(_STAT_BLOCK_KEY)
        for key in profile_keys:
            value = data.get(key)
            if value is None or value == "":
                continue
            rendered = _render_value(value, key)
            if rendered:
                profile.append(
                    f'<section class="note-{_html.escape(key, quote=True)}">'
                    f"<h3>{_esc(_label(key))}</h3>{rendered}</section>"
                )
            used.add(key)
        if profile:
            body.append(f'<div class="character-notes">{"".join(profile)}</div>')
    else:
        setting: list[str] = []
        for key in (
            "description",
            "inhabitants",
            "whats_hidden",
            "doctrine",
            "assets",
            "archetype",
            "dial",
        ):
            value = data.get(key)
            if value is None or value == "":
                continue
            rendered = _render_value(value, key)
            if rendered:
                setting.append(f"<section><h3>{_esc(_label(key))}</h3>{rendered}</section>")
            used.add(key)
        if setting:
            body.append(f'<div class="setting-facts">{"".join(setting)}</div>')

    for key, value in data.items():
        if key in used or value is None or value == "":
            continue
        if key == _STAT_BLOCK_KEY:
            body.append(_stat_block_panel(value, data))
            continue
        rendered = _render_value(value, str(key))
        if rendered:
            body.append(f"<section><h3>{_esc(_label(str(key)))}</h3>{rendered}</section>")
    relations = []
    subject = _esc(names.get(entity.id, entity.id))
    for edge in export.edges:
        label = _esc(_edge_label(edge))
        if edge.src == entity.id:
            neighbor = _esc(names.get(edge.dst, edge.dst))
            relations.append(f"<li>{subject} --{label}--> {neighbor}</li>")
        elif edge.dst == entity.id:
            neighbor = _esc(names.get(edge.src, edge.src))
            relations.append(f"<li>{neighbor} --{label}--&gt; {subject}</li>")
    if relations:
        body.append(
            f"<section><h2>Relations</h2><ul class='relations'>{''.join(relations)}</ul></section>"
        )
    body.append(_media_block(export, entity))
    if appendix:
        appendix_json = json.dumps(data, indent=2, ensure_ascii=False)
        body.append(
            "<details class='appendix'><summary>Complete committed record (JSON)</summary>"
            f"<pre>{_esc(appendix_json)}</pre></details>"
        )
    return (
        f'<article{article_id} class="entity-card kind-{_html.escape(kind, quote=True)}">'
        f"{heading}{''.join(body)}</article>"
    )


def _document(
    title: str,
    subtitle: str,
    body: str,
    *,
    body_class: str = "entity-document",
    kicker: str = "",
) -> str:
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8"/>\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1"/>\n'
        f"<title>{_esc(title)}</title>\n"
        f"<style>{SHEET_CSS}</style>\n</head>\n"
        f'<body class="{_html.escape(body_class, quote=True)}">\n'
        f'<header class="doc-header"><p class="doc-kicker">{_esc(kicker)}</p>'
        f"<h1>{_esc(title)}</h1>{subtitle}</header>\n"
        f"{body}\n</body>\n</html>\n"
    )


def _revision_meta(export: WorldExport) -> str:
    if export.revision is None:
        return '<p class="meta small mono">revision: none</p>'
    return (
        f'<p class="meta small mono">revision {_esc(export.revision.id)}'
        f" · {_esc(exported_at(export))}</p>"
    )


def render_entity_html(export: WorldExport, entity_id: str) -> str:
    """The single-entity sheet: portrait embedded, stat block, every
    committed section, relations, media, and the verbatim JSON appendix."""
    names = name_labels(export)
    entity = next(e for e in export.entities if e.id == entity_id)
    subtitle = (
        f'<p class="meta">{_esc(export.campaign.title)} · {_esc(export.campaign.theme)}</p>'
        + _revision_meta(export)
    )
    data = entity.data if isinstance(entity.data, dict) else {}
    role = str(data.get("role") or "character").lower()
    return _document(
        names[entity.id],
        subtitle,
        _entity_sections(export, entity, names, embed=True, appendix=True) + "\n",
        body_class=f"entity-document {role}-sheet",
        kicker="Monster stat block" if role == "monster" else "Character sheet",
    )


def render_world_html(export: WorldExport) -> str:
    """The world document: campaign seed, one card per entity (no embedded
    binaries — the manifest paths + availability flags carry the media
    story), and the complete edge table."""
    names = name_labels(export)
    subtitle_parts = [
        f'<p class="meta">{_esc(export.campaign.theme)}</p>',
        f'<p class="meta">{_esc(export.campaign.description)}</p>',
        f'<p class="prose">{_esc(export.campaign.custom_lore)}</p>'
        if export.campaign.custom_lore
        else "",
        _revision_meta(export),
    ]
    group_order = ("place", "faction", "character")
    groups = {
        kind: [entity for entity in export.entities if str(entity.kind).lower() == kind]
        for kind in group_order
    }
    other_entities = [
        entity for entity in export.entities if str(entity.kind).lower() not in group_order
    ]
    index_items = []
    body_parts = []
    for kind in group_order:
        members = groups[kind]
        if not members:
            continue
        title = f"{kind.title()}s" if kind != "faction" else "Factions"
        index_items.append(f'<li><a href="#section-{kind}">{_esc(title)} · {len(members)}</a></li>')
        cards = "".join(
            _entity_sections(export, entity, names, embed=False, appendix=False)
            for entity in members
        )
        body_parts.append(
            f'<section class="world-section" id="section-{kind}">'
            '<header class="world-section-title">'
            f"<h2>{_esc(title)}</h2><span>{len(members)} records</span></header>"
            f"{cards}</section>"
        )
    if other_entities:
        index_items.append(f'<li><a href="#section-other">Other · {len(other_entities)}</a></li>')
        cards = "".join(
            _entity_sections(export, entity, names, embed=False, appendix=False)
            for entity in other_entities
        )
        body_parts.append(
            '<section class="world-section" id="section-other">'
            '<header class="world-section-title"><h2>Other records</h2></header>'
            f"{cards}</section>"
        )
    if index_items:
        body_parts.insert(
            0,
            '<nav aria-label="World contents"><ul class="world-index">'
            f"{''.join(index_items)}</ul></nav>",
        )
    if export.edges:
        rows = [
            f"<tr><td>{_esc(names.get(edge.src, edge.src))}</td>"
            f"<td>{_esc(edge.type)}</td><td>{_inline(edge.counter)}</td>"
            f"<td>{_esc(names.get(edge.dst, edge.dst))}</td></tr>"
            for edge in export.edges
        ]
        body_parts.append(
            "<section><h2>Edges</h2><table class='edges'>"
            "<thead><tr><th>source</th><th>type</th><th>counter</th><th>target</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table></section>"
        )
    return _document(
        export.campaign.title,
        "".join(subtitle_parts),
        "".join(body_parts),
        body_class="world-document",
        kicker="Campaign atlas",
    )


# ---------------------------------------------------------------------------
# Owlbear / Forge export (spec-5-2)
# ---------------------------------------------------------------------------

#: Extension namespace prefixing every Forge metadata key (measured from
#: the live default-5e AI Template dictionary, 2026-09-09).
_FORGE_NS = "com.battle-system.forge"

#: BID -> meaning (the frozen table from the spec's Design Notes; Ask
#: First before changing — it mirrors the live dictionary, not memory).
#: Z001 identity.level (NPC/BBEG only); Z003 record alignment; Z004
#: record race_type; Z005/Z006 combat hp (current/max); Z007 combat ac;
#: Z014 skills join; Z016 identity.cr fraction->decimal (Monster only);
#: Z017-Z022 the six scores; Z023-Z028 derived saves
#: floor((score-10)/2); Z034 traits; Z035 actions (the attack text gains
#: the structured damage sentence when the block carries parts); Z038
#: boss.legendary_actions; Z039 spells (names, "" descriptions); Z040
#: record equipment. Everything else is omitted — sparse payloads import
#: validly (speeds, senses, languages, resistances, proficiency,
#: Z036/Z037 bonus/reactions have no stored source).


def _forge_key(bid: str) -> str:
    """The extension-namespaced metadata key Forge imports (unit-card
    field menus show the bare ``[Z017]`` BID for the same slot)."""
    return f"{_FORGE_NS}/{bid}"


def _forge_number(value: Any) -> int | float | None:
    """Coerce a stored numeric to a JSON number (Forge ``numb``).
    Ints/floats pass (non-finite never serializes — omitted); numeric
    strings parse; CR fractions (``"1/2"``) divide out. Anything else is
    unmapped and omitted — the export path never validates (FR18)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        text = value.strip()
        if "/" in text:
            parts = text.split("/")
            if len(parts) == 2:
                try:
                    num, den = float(parts[0]), float(parts[1])
                except ValueError:
                    return None
                if den == 0:
                    return None
                result = num / den
                return result if math.isfinite(result) else None
            return None
        try:
            return int(text)
        except ValueError:
            pass
        try:
            result = float(text)
        except ValueError:
            return None
        return result if math.isfinite(result) else None
    return None


def _forge_text(value: Any) -> str | None:
    """A non-blank string, else None (sparse omit)."""
    if isinstance(value, str) and value.strip():
        return value
    return None


#: A dice token, whitespace- and case-insensitive ("2d6", "2 d 6").
_DICE_TOKEN_RE = re.compile(r"(\d+)\s*[dD]\s*(\d+)")


def _states_damage_dice(description: str, sentence: str) -> bool:
    """Whether ``description`` already names every die ``sentence`` states.

    The AR25 contract asks the model to keep the prose and the ``damage``
    list in step (the description still states the numbers for the DM), so
    the common block already reads correctly — appending the sentence there
    would print the same dice twice. Only a description that dropped the
    numbers gains it (spec: structured attack damage, 2026-09-11). The
    comparison reads the dice out of the RENDERED sentence, so the rule
    cannot drift from what ``damage_parts_sentence`` actually emits.
    """
    stated = {(int(count), int(sides)) for count, sides in _DICE_TOKEN_RE.findall(description)}
    wanted = {(int(count), int(sides)) for count, sides in _DICE_TOKEN_RE.findall(sentence)}
    return wanted <= stated


def _forge_entries(
    entity_id: str, items: Any, *, with_damage: bool = False
) -> list[dict[str, str]]:
    """A name/description list as Forge ``list`` entries with index-stable
    ids (``{entity-id-8}-{i}`` — never fresh ULIDs, so repeats are
    byte-identical). Nameless members are skipped, not failed.

    ``with_damage`` is the ACTION list's form: an action carrying usable
    structured ``damage`` parts gains the one-line sentence those parts
    imply, so Z035 states the numbers the auditor read rather than whatever
    the prose happened to say. A block with no parts — every block
    committed before spec 2026-09-11 — renders its description verbatim."""
    if not isinstance(items, list):
        return []
    entries = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        description = item.get("description", "")
        if description is None:
            description = ""
        if not isinstance(description, str):
            description = str(description)
        if with_damage:
            sentence = damage_parts_sentence(item.get("damage"))
            if sentence is not None and not _states_damage_dice(description, sentence):
                description = f"{description} {sentence}" if description else sentence
        entries.append(
            {"id": f"{entity_id[-8:]}-{index}", "name": name, "description": description}
        )
    return entries


def render_entity_owlbear(export: WorldExport, entity_id: str) -> dict[str, Any]:
    """The Forge transfer payload for one committed entity (spec-5-2):
    the FLAT metadata object itself — ``com.battle-system.forge/name``
    first, then the mapped Z-slots, with the observed ``fabd`` constant
    inside the namespaced metadata (both observed live payloads carry it;
    the flat paste imports, the {name, author, metadata} envelope rejects).
    Level/CR derive from ``stat_block.identity`` numerics (owner
    verdict: top-level ``level_cr`` is display-only and never read
    here). Only character-shaped records map — anything unmapped is
    omitted, never validated."""
    entity = next(e for e in export.entities if e.id == entity_id)
    data = entity.data if isinstance(entity.data, dict) else {}
    block = data.get("stat_block")
    block = block if isinstance(block, dict) else {}
    identity = block.get("identity")
    identity = identity if isinstance(identity, dict) else {}
    combat = block.get("combat")
    combat = combat if isinstance(combat, dict) else {}
    attributes = block.get("attributes")
    attributes = attributes if isinstance(attributes, dict) else {}
    boss = data.get("boss")
    boss = boss if isinstance(boss, dict) else {}
    role = identity.get("role")
    metadata: dict[str, Any] = {}
    if role in ("NPC", "BBEG"):
        level = _forge_number(identity.get("level"))
        if level is not None:
            metadata[_forge_key("Z001")] = level
    elif role == "Monster":
        challenge = _forge_number(identity.get("cr"))
        if challenge is not None:
            metadata[_forge_key("Z016")] = challenge
    alignment = _forge_text(data.get("alignment"))
    if alignment is not None:
        metadata[_forge_key("Z003")] = alignment
    race = _forge_text(data.get("race_type"))
    if race is not None:
        metadata[_forge_key("Z004")] = race
    hp_raw = combat.get("hp")
    if hp_raw is None:
        # The 5-1 sheet fixture commits the long names (armor_class /
        # hit_points); the validator's rules text says ac / hp — read both.
        hp_raw = combat.get("hit_points")
    hit_points = _forge_number(hp_raw)
    if hit_points is not None:
        metadata[_forge_key("Z005")] = hit_points
        metadata[_forge_key("Z006")] = hit_points
    ac_raw = combat.get("ac")
    if ac_raw is None:
        ac_raw = combat.get("armor_class")
    armor = _forge_number(ac_raw)
    if armor is not None:
        metadata[_forge_key("Z007")] = armor
    skills = block.get("skills")
    if isinstance(skills, list):
        parts = []
        for entry in skills:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            # A missing/unparseable bonus keeps the bare name — the
            # export never invents a bonus it was not given.
            bonus = _forge_number(entry.get("bonus"))
            if bonus is None:
                parts.append(name)
            else:
                sign = "+" if bonus >= 0 else ""
                parts.append(f"{name} {sign}{bonus}")
        if parts:
            metadata[_forge_key("Z014")] = ", ".join(parts)
    scores: dict[str, int | float] = {}
    for position, ability in enumerate(_ABILITY_ORDER):
        score = _forge_number(attributes.get(ability))
        if score is None:
            continue
        scores[ability] = score
        metadata[_forge_key(f"Z{17 + position:03d}")] = score
    for position, ability in enumerate(_ABILITY_ORDER):
        score = scores.get(ability)
        if score is None:
            continue
        metadata[_forge_key(f"Z{23 + position:03d}")] = math.floor((score - 10) / 2)
    traits = _forge_entries(entity_id, block.get("traits"))
    if traits:
        metadata[_forge_key("Z034")] = traits
    actions = _forge_entries(entity_id, block.get("actions"), with_damage=True)
    if actions:
        metadata[_forge_key("Z035")] = actions
    legendary = _forge_text(boss.get("legendary_actions"))
    if legendary is not None:
        metadata[_forge_key("Z038")] = legendary
    spells = block.get("spells")
    if isinstance(spells, list):
        spell_entries = [
            {"id": f"{entity_id[-8:]}-{index}", "name": name.strip(), "description": ""}
            for index, name in enumerate(spells)
            if isinstance(name, str) and name.strip()
        ]
        if spell_entries:
            metadata[_forge_key("Z039")] = spell_entries
    equipment = _forge_entries(entity_id, data.get("equipment"))
    if equipment:
        metadata[_forge_key("Z040")] = equipment
    # ``fabd`` rides INSIDE the namespaced metadata (both observed live
    # payloads carry ``com.battle-system.forge/fabd: true`` — review
    # round 1), not as a bare top-level key.
    metadata[_forge_key("fabd")] = True
    # Flat metadata object (live-verified 2026-09-10): the Import modal
    # parses the paste AS the metadata object and requires the unit name
    # inside it — the {name, author, metadata} envelope rejects with
    # "must include a valid unit name". Author has no dictionary slot.
    return {_forge_key("name"): entity.name, **metadata}


# ---------------------------------------------------------------------------
# FG Unity (Fantasy Grounds) — the 2024 NPC record XML (spec-5-3)
# ---------------------------------------------------------------------------

#: FG 2024-record tag per committed ability key. The record schema is
#: verified from the product's own Export NPC (fixture:
#: _bmad-output/implementation-artifacts/fixture-fg-npc-record-2024-export.xml,
#: owner-annotated 2026-09-13).
_FG_ABILITY_TAG: dict[str, str] = {
    "str": "strength",
    "dex": "dexterity",
    "con": "constitution",
    "int": "intelligence",
    "wis": "wisdom",
    "cha": "charisma",
}

_FG_ABILITY_LABEL: dict[str, str] = {
    "str": "Str",
    "dex": "Dex",
    "con": "Con",
    "int": "Int",
    "wis": "Wis",
    "cha": "Cha",
}

#: The AR24 lore sections that fill the record's ``text`` notes (owner
#: ruling 2026-09-14: the sheet already shows the stats — the text slot
#: carries the character's lore). Order = the AR24 profile order
#: (CandidatesView LORE_FIELDS).
_FG_LORE_FIELDS: tuple[str, ...] = (
    "appearance",
    "personality",
    "background",
    "goals",
    "relationships",
    "secret",
    "rumor",
    "party_hook",
    "voice_style",
    "catchphrases",
)


def _fg_num(value: Any) -> str | None:
    """A stored numeric as FG XML text: ints stay ints, CR fractions
    divide out ("1/2" -> "0.5"), everything else is unmapped (omitted —
    the export path never validates, FR18)."""
    number = _forge_number(value)
    if number is None:
        return None
    return f"{number:g}"


def _fg_text(value: Any) -> str | None:
    """A non-blank string, else None (sparse omit)."""
    return _forge_text(value)


def _fg_ability_score(attributes: dict[str, Any], ability: str) -> int | None:
    score = _forge_number(attributes.get(ability))
    return int(score) if score is not None else None


def _fg_ability_bonus(score: int | None) -> int | None:
    """The 5e ability modifier FG derives from the score."""
    if score is None:
        return None
    return (score - 10) // 2


def _fg_power_entries(node: ET.Element, tag: str, items: Any, *, with_damage: bool = False) -> None:
    """Name/description lists as the record's enumerated power subtrees
    (``npc_power`` = name + desc — verified in record_npc.xml). The
    enumeration restarts per subtree in list order, so repeats are
    byte-identical. Nameless members are skipped, never failed."""
    if not isinstance(items, list):
        return
    group: ET.Element | None = None
    index = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        description = item.get("description")
        if description is None:
            description = ""
        if not isinstance(description, str):
            description = str(description)
        if with_damage:
            sentence = damage_parts_sentence(item.get("damage"))
            if sentence is not None and not _states_damage_dice(description, sentence):
                description = f"{description} {sentence}" if description else sentence
        if group is None:
            group = ET.SubElement(node, tag)
        index += 1
        entry = ET.SubElement(group, f"id-{index:05d}")
        name_el = ET.SubElement(entry, "name")
        name_el.set("type", "string")
        name_el.text = name
        if description:
            desc_el = ET.SubElement(entry, "desc")
            desc_el.set("type", "string")
            desc_el.text = description


def _fg_fg_text(paragraphs: list[str], node: ET.Element) -> None:
    """The record's ``text`` formattedtext element: the character's AR24
    lore notes (owner ruling 2026-09-14) plus the portrait pointer — the
    sheet already displays the stats, so the notes carry the story. An
    empty notes area still emits one empty ``<p />`` — FG's own export
    writes the same shape."""
    text = ET.SubElement(node, "text")
    text.set("type", "formattedtext")
    if not paragraphs:
        ET.SubElement(text, "p")
        return
    for paragraph in paragraphs:
        p = ET.SubElement(text, "p")
        p.text = paragraph


def _fg_note_lines(value: Any) -> list[str]:
    """Deterministic note paragraphs for the record's ``text`` field:
    multi-line strings split on newlines, lists one item per line,
    anything else a compact JSON line — never dropped, never re-ordered."""
    if isinstance(value, str):
        return [line.strip() for line in value.splitlines() if line.strip()] or [value.strip()]
    if isinstance(value, list):
        notes: list[str] = []
        for item in value:
            if isinstance(item, str):
                notes.extend(line for line in item.splitlines() if line.strip())
            else:
                notes.append(json.dumps(item, ensure_ascii=False, separators=(", ", ": ")))
        return notes
    return [json.dumps(value, ensure_ascii=False, separators=(", ", ": "))]


def _fg_lore_paragraphs(data: dict[str, Any]) -> list[str]:
    """The record's ``text`` notes: the AR24 lore sections (owner ruling
    2026-09-14 — the sheet already shows the stats, so the text slot
    carries the character's lore, not a duplicate stat block). The known
    section order is the AR24 profile order (CandidatesView LORE_FIELDS);
    blank/absent sections are skipped, never invented."""
    paragraphs: list[str] = []
    for key in _FG_LORE_FIELDS:
        value = data.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        paragraphs.append(_label(key))
        paragraphs.extend(_fg_note_lines(value))
    return paragraphs


def render_entity_fg(export: WorldExport, entity_id: str) -> str:
    """Fantasy Grounds Unity import: one committed entity as the 5E
    2024-record XML (spec-5-3) — the exact shape FG's own Export NPC
    writes (fixture-pinned), imported via the NPCs window's Import
    button (file picker). Pure function of the snapshot: no wall clock,
    no store read, byte-identical across repeats. Sparse is legal —
    every field maps only what is committed, nothing is validated
    (FR18). Levels/CR derive from ``stat_block.identity`` numerics
    (owner verdict: top-level ``level_cr`` display-only)."""
    entity = next(e for e in export.entities if e.id == entity_id)
    data = entity.data if isinstance(entity.data, dict) else {}
    block = data.get("stat_block")
    block = block if isinstance(block, dict) else {}
    identity = block.get("identity")
    identity = identity if isinstance(identity, dict) else {}
    combat = block.get("combat")
    combat = combat if isinstance(combat, dict) else {}
    attributes = block.get("attributes")
    attributes = attributes if isinstance(attributes, dict) else {}
    saves = block.get("saves")
    saves = saves if isinstance(saves, dict) else {}

    root = ET.Element("root", {"version": "5.1"})
    npc = ET.SubElement(root, "npc")

    def leaf(tag: str, kind: str, text: str | None) -> None:
        if text is not None:
            el = ET.SubElement(npc, tag)
            el.set("type", kind)
            el.text = text

    # Identity fields (verified record schema, fixture §2c).
    leaf("name", "string", entity.name)
    fg_type = _fg_text(identity.get("race")) or _fg_text(data.get("race_type"))
    leaf("type", "string", fg_type)
    fg_size = _fg_text(data.get("size")) or _fg_text(block.get("size"))
    leaf("size", "string", fg_size)
    fg_alignment = _fg_text(identity.get("alignment")) or _fg_text(data.get("alignment"))
    leaf("alignment", "string", fg_alignment)

    # Combat block.
    ac = _forge_number(combat.get("ac"))
    if ac is None:
        ac = _forge_number(combat.get("armor_class"))
    leaf("ac", "number", _fg_num(ac))
    hp = _forge_number(combat.get("hp"))
    if hp is None:
        hp = _forge_number(combat.get("hit_points"))
    leaf("hp", "number", _fg_num(hp))
    leaf("hd", "string", _fg_text(combat.get("hit_dice")))
    speed = _fg_text(combat.get("speed")) or _fg_text(block.get("speed"))
    leaf("speed", "string", speed)
    init_raw = _forge_number(block.get("initiative"))
    if init_raw is None:
        init_raw = _forge_number(combat.get("initiative"))
    # The importer stores initiative.misc as Initiative bonus − Dex mod
    # (research §2d) — mirror the derivation so the sheet shows the
    # committed bonus.
    if init_raw is not None:
        dex_score = _fg_ability_score(attributes, "dex")
        dex_bonus = _fg_ability_bonus(dex_score) if dex_score is not None else None
        if dex_bonus is not None:
            misc = int(init_raw) - dex_bonus
            ini = ET.SubElement(npc, "initiative")
            misc_el = ET.SubElement(ini, "misc")
            misc_el.set("type", "number")
            misc_el.text = str(misc)

    # Challenge: cr stays a STRING on the 2024 record; xp is not tracked
    # (omitted — sparse is legal).
    role = identity.get("role")
    if role in ("NPC", "BBEG"):
        leaf("cr", "string", _fg_num(_forge_number(identity.get("level"))))
    elif role == "Monster":
        leaf("cr", "string", _fg_num(_forge_number(identity.get("cr"))))

    # Abilities (verified: abilities.<attr>.score / .savemodifier; the
    # stored save modifier is save − ability mod, the importer's
    # derivation — §2c/§2d).
    if attributes:
        abilities = ET.SubElement(npc, "abilities")
        for ability in _ABILITY_ORDER:
            score = _fg_ability_score(attributes, ability)
            if score is None:
                continue
            group = ET.SubElement(abilities, _FG_ABILITY_TAG[ability])
            score_el = ET.SubElement(group, "score")
            score_el.set("type", "number")
            score_el.text = str(score)
            saved = _forge_number(saves.get(ability))
            if saved is not None:
                bonus = _fg_ability_bonus(score)
                if bonus is not None:
                    save_el = ET.SubElement(group, "savemodifier")
                    save_el.set("type", "number")
                    save_el.text = str(int(saved) - bonus)

    # Sheet strings (skills joins with sign — the Z014 rule).
    if isinstance(block.get("skills"), list):
        parts = []
        for entry in block["skills"]:
            if not isinstance(entry, dict):
                continue
            skill_name = entry.get("name")
            if not isinstance(skill_name, str) or not skill_name.strip():
                continue
            skill_bonus = _forge_number(entry.get("bonus"))
            if skill_bonus is None or isinstance(skill_bonus, float):
                parts.append(skill_name if skill_bonus is None else f"{skill_name} {skill_bonus:g}")
            else:
                parts.append(f"{skill_name} {skill_bonus:+d}")
        if parts:
            leaf("skills", "string", ", ".join(parts))
    for key, tag in (
        ("damage_vulnerabilities", "damagevulnerabilities"),
        ("damage_resistances", "damageresistances"),
        ("damage_immunities", "damageimmunities"),
        ("condition_immunities", "conditionimmunities"),
        ("senses", "senses"),
        ("languages", "languages"),
    ):
        value = _fg_text(block.get(key)) or _fg_text(data.get(key))
        leaf(tag, "string", value)

    # Powers: name + desc per entry (npc_power), enumerated per subtree.
    _fg_power_entries(npc, "traits", block.get("traits"))
    _fg_power_entries(npc, "actions", block.get("actions"), with_damage=True)

    # Spells: name-only entries (slots are not tracked — omitted).
    spells = block.get("spells")
    if isinstance(spells, list):
        names = [s for s in spells if isinstance(s, str) and s.strip()]
        if names:
            group = ET.SubElement(npc, "spells")
            for index, name in enumerate(names, start=1):
                entry = ET.SubElement(group, f"id-{index:05d}")
                name_el = ET.SubElement(entry, "name")
                name_el.set("type", "string")
                name_el.text = name.strip()

    # The record's notes: the AR24 lore sections (owner ruling
    # 2026-09-14 — the sheet already shows the stats, so the text slot
    # carries the character's story) + the portrait pointer. The signed
    # portrait URL comes from the portrait-url route (mint-on-demand,
    # expiring); embedding a live URL here would break byte-identical
    # determinism, so the artifact names the file it expects instead.
    paragraphs = _fg_lore_paragraphs(data)
    portrait: str | None = None
    portrait_missing: str | None = None
    newest_available: str | None = None
    for media in entity.media:
        if media.kind != "image":
            continue
        if media.available:
            newest_available = media.filename
        elif portrait_missing is None:
            portrait_missing = media.filename
    if newest_available is not None:
        portrait = newest_available
    elif portrait_missing is not None:
        portrait_missing_marker = f"Portrait: {portrait_missing} [missing]"
        paragraphs.append(f"Information: {portrait_missing_marker}")
    if portrait is not None:
        paragraphs.append(f"Information: Portrait: {portrait}")
    _fg_fg_text(paragraphs, npc)

    leaf("version", "string", "2024")

    ET.indent(root, space="\t")
    body = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="utf-8"?>\n' + body


# ---------------------------------------------------------------------------
# spec-5-4: MapTool / RPGToken export — deterministic .rptok ZIP
# ---------------------------------------------------------------------------
# The .rptok is an ordinary ZIP (research-5-4 §2b): content.xml (the
# Token XStream-serialized by the owner's Dragon template VERBATIM —
# template-fidelity emission, owner directive 2026-09-14), properties.xml
# (version 1.18.6 + herolab), and one embedded image as the
# ``assets/<md5>`` Asset descriptor + ``assets/<md5>.png`` pair. Only the
# template's MARKER tokens carry entity data (derived id GUID, image
# md5, name, notes/gmNotes, propertyMapCI values, macro entries); every
# other byte is the real 1.18.6 save. The token id GUID is DERIVED from
# the entity id (never random), the zip is written with fixed
# timestamps/pinned deflate/no extra fields, and every text run is XML
# 1.0-filtered + fully escaped — byte-identical repeats. The html for
# notes/gmNotes is deliberately conservative and attribute-free (<b>,
# <br>, <p> only — the owner ruling 2026-09-14).

#: Illegal XML 1.0 characters: the control chars (dropped — escaping alone
#: cannot legalize them; the matrix pins \x00-\x08, \x0B, \x0C, \x0E-\x1F)
#: plus the plane-0 non-characters U+FFFE/U+FFFF (outside XML 1.0's Char range).
_XML10_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")


def _xml10(text: str) -> str:
    """An XML 1.0-legal text run: illegal chars dropped, then fully
    escaped (``&<>"'``). Full ``>`` escaping also neutralizes ``]]>``, so
    any value can round-trip through an XML parse."""
    return _html.escape(_XML10_ILLEGAL.sub("", text), quote=True)


def _rptok_guid(entity_id: str) -> str:
    """The token id GUID, derived from the entity id (spec-5-4): the
    first 16 bytes of sha256(entity_id), standard-base64 — the exact
    on-disk ``<id><baGUID>…</baGUID></id>`` shape from the owner's real
    1.18.6 export. Unique per entity, deterministic across exports,
    never random."""
    digest = hashlib.sha256(entity_id.encode("utf-8")).digest()[:16]
    return base64.b64encode(digest).decode("ascii")


def _rptok_md5(data: bytes) -> str:
    """The 32-lowercase-hex MD5 of the image bytes (MD5Key.java) —
    FIPS-safe (``usedforsecurity=False``, spec-5-4 review loop)."""
    return hashlib.new("md5", data, usedforsecurity=False).hexdigest()


#: The bundled token image embedded when the portrait pipeline produced
#: nothing usable (spec-5-4): one swappable 256×256 RGBA PNG committed at
#: backend/app/media/maptool_default_token.png.
_RPTOK_DEFAULT_IMAGE = (
    Path(__file__).resolve().parent.parent / "media" / "maptool_default_token.png"
)


def _rptok_default_image() -> bytes | None:
    """The bundled default token PNG bytes, or None when the file is
    missing/not a PNG — the old no-image fallback + marker."""
    try:
        raw = _RPTOK_DEFAULT_IMAGE.read_bytes()
    except OSError:
        return None
    return raw if raw.startswith(PNG_SIGNATURE) else None


def _rptok_token_image(
    export: WorldExport, entity: EntityExport
) -> tuple[bytes | None, str | None]:
    """The embedded image bytes + the broken-portrait marker (spec-5-4).
    Image = the newest AVAILABLE ``kind=image`` row whose on-disk bytes
    start with the PNG magic (no size cap — the .rptok member is raw,
    unlike the fg path's MAX_INLINE_BYTES); scan newest-first so an older
    valid portrait beats the bundled default; a missing/corrupt portrait
    embeds the bundled default instead; a broken default falls back to
    no image. The marker — set ONLY when no portrait served and the
    default rode in — names the NEWEST failed row with its cause:
    ``[missing]`` when the file is absent from disk, ``[unusable]`` when
    it exists but fails the magic/read check. No marker when the entity
    has no image media at all."""
    image_rows = [row for row in entity.media if row.kind == "image"]
    usable: bytes | None = None
    failed: tuple[str, str] | None = None  # (newest failed filename, cause tag)
    for row in reversed(image_rows):  # newest first
        if not row.available:  # manifest-resolved: file absent from disk
            if failed is None:
                failed = (row.filename, "missing")
            continue
        path = Path(configured_media_dir()) / export.campaign.id / entity.id / row.filename
        try:
            raw = path.read_bytes()
        except OSError:
            if failed is None:
                failed = (row.filename, "unusable")
            continue
        if raw.startswith(PNG_SIGNATURE):
            usable = raw
            break
        if failed is None:
            failed = (row.filename, "unusable")
    if usable is not None:
        return usable, None
    marker: str | None = None
    if failed is not None:
        filename, cause = failed
        marker = f"Portrait: {filename} [{cause} — default image used]"
    default = _rptok_default_image()
    if default is not None:
        return default, marker
    return None, marker


def _rptok_identity_header(
    identity: dict[str, Any], block: dict[str, Any], data: dict[str, Any]
) -> str:
    """The stat-block opening line (2024-Core line 2 shape: ``<size>
    <type>, <alignment>``): the D&D creature type (+ size) and alignment
    from the committed identity — record fields win over the top-level
    AR24 keys (the fg renderer's rule). Any piece is sparse-omitted,
    never invented."""
    size = _forge_text(block.get("size")) or _forge_text(data.get("size"))
    race = _forge_text(identity.get("race")) or _forge_text(data.get("race_type"))
    alignment = _forge_text(identity.get("alignment")) or _forge_text(data.get("alignment"))
    head: list[str] = []
    if size is not None and race is not None:
        head.append(f"{size} {race}")
    elif race is not None:
        head.append(race)
    elif size is not None:
        head.append(size)
    if alignment is not None:
        head.append(alignment)
    return ", ".join(head)


#: The ability columns of the 2024-Core stat block (research-5-3 §2d):
#: three abilities per row, the grammar's lines 7-8.
_RPTOK_ABILITY_COLUMNS: tuple[tuple[str, ...], ...] = (("str", "dex", "con"), ("int", "wis", "cha"))

#: The 2024-Core keyword labels for the damage/condition lines (same
#: committed keys the fg renderer maps; order pinned, never sorted).
_RPTOK_DAMAGE_LABELS: tuple[tuple[str, str], ...] = (
    ("damage_vulnerabilities", "Damage Vulnerabilities"),
    ("damage_resistances", "Damage Resistances"),
    ("damage_immunities", "Damage Immunities"),
    ("condition_immunities", "Condition Immunities"),
)


def _rptok_power_lines(items: Any, *, with_damage: bool = False) -> list[str]:
    """2024-Core section entries (Traits/Actions) as bold-labelled lines:
    ``<b>Name.</b> <description>``. The description gains the structured
    damage sentence when the prose dropped the dice (the fg rule — the
    numbers the DM reads come from the parts, not a re-parse). Nameless
    members and non-dict junk are skipped, never failed."""
    if not isinstance(items, list):
        return []
    out: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        description = item.get("description")
        if description is None:
            description = ""
        if not isinstance(description, str):
            description = str(description)
        if with_damage:
            damage_line = damage_parts_sentence(item.get("damage"))
            if damage_line is None:
                damage_line = _rptok_damage_line(item.get("damage"))
            if damage_line is not None and not _states_damage_dice(description, damage_line):
                description = f"{description} {damage_line}" if description else damage_line
        line = f"<b>{name}.</b>"
        if description:
            line += f" {description}"
        out.append(line)
    return out


def _rptok_notes_html(block: dict[str, Any], data: dict[str, Any]) -> str:
    """The 5e stat block as conservative attribute-free HTML (spec-5-4):
    the 2024-Core import-grammar lines (research-5-3 §2d) as bold-labelled
    lines separated by ``<br>``, race/type/alignment header first. Line
    order and labels follow the 2024-Core grammar so the visible text
    stays FG-paste-shaped at the content level. Sparse is legal — only
    committed keys render. The caller (content.xml) applies the single
    XML-escape pass over the whole HTML run at the element boundary, so
    the text here is raw HTML; the builder itself never invents text."""
    identity = block.get("identity")
    identity = identity if isinstance(identity, dict) else {}
    combat = block.get("combat")
    combat = combat if isinstance(combat, dict) else {}
    attributes = block.get("attributes")
    attributes = attributes if isinstance(attributes, dict) else {}
    saves = block.get("saves")
    saves = saves if isinstance(saves, dict) else {}

    lines: list[str] = []
    header = _rptok_identity_header(identity, block, data)
    if header:
        lines.append(f"<b>{header}</b>")

    # AC (2024-Core line 3) with the optional Initiative marker riding
    # the same line; the parenthetical is the Dex score, the 2024 shape.
    ac = _forge_number(combat.get("ac"))
    if ac is None:
        ac = _forge_number(combat.get("armor_class"))
    if ac is not None:
        ac_line = f"AC {ac:g}"
        initiative = _forge_number(block.get("initiative"))
        if initiative is None:
            initiative = _forge_number(combat.get("initiative"))
        if initiative is not None and float(initiative).is_integer():
            ac_line += f" Initiative {int(initiative):+d}"
            dex_score = _forge_number(attributes.get("dex"))
            if dex_score is not None and float(dex_score).is_integer():
                ac_line += f" ({int(dex_score)})"
        lines.append(ac_line)

    # HP (2024-Core line 4): the parenthetical holds the raw hit-dice
    # string verbatim.
    hp = _forge_number(combat.get("hp"))
    if hp is None:
        hp = _forge_number(combat.get("hit_points"))
    if hp is not None:
        hp_line = f"HP {hp:g}"
        hit_dice = _forge_text(combat.get("hit_dice"))
        if hit_dice is not None:
            hp_line += f" ({hit_dice})"
        lines.append(hp_line)

    speed = _forge_text(combat.get("speed")) or _forge_text(block.get("speed"))
    if speed is not None:
        lines.append(f"Speed {speed}")

    # Ability columns (2024-Core lines 6-8): MOD SAVE header + two rows of
    # ``Abl <score> <+mod> <+save>``; saves ride the columns (the 2024
    # shape — the fg renderer's savemodifier source). The header renders
    # only when ALL six ability cells will (a sparse block would leave a
    # short row); non-integral scores/saves are omitted, never truncated.
    if attributes:
        scores: dict[str, int] = {}
        for ability in _ABILITY_ORDER:
            score = _forge_number(attributes.get(ability))
            if score is not None and float(score).is_integer():
                scores[ability] = int(score)
        if len(scores) == len(_ABILITY_ORDER):
            lines.append("MOD SAVE MOD SAVE MOD SAVE")
        for row in _RPTOK_ABILITY_COLUMNS:
            cells: list[str] = []
            for ability in row:
                score = scores.get(ability)
                if score is None:
                    continue
                bonus = _fg_ability_bonus(score) or 0
                cell = f"{ability.capitalize()} {score} {bonus:+d}"
                saved = _forge_number(saves.get(ability))
                if saved is not None and float(saved).is_integer():
                    cell += f" {int(saved):+d}"
                cells.append(cell)
            if cells:
                lines.append(" ".join(cells))

    # Skills: the Z014 sign join (full names, sign-aware bonuses).
    skills = block.get("skills")
    if isinstance(skills, list):
        skill_parts: list[str] = []
        for entry in skills:
            if not isinstance(entry, dict):
                continue
            skill_name = entry.get("name")
            if not isinstance(skill_name, str) or not skill_name.strip():
                continue
            skill_bonus = _forge_number(entry.get("bonus"))
            if skill_bonus is None:
                skill_parts.append(skill_name)
            elif isinstance(skill_bonus, float):
                skill_parts.append(f"{skill_name} {skill_bonus:g}")
            else:
                skill_parts.append(f"{skill_name} {skill_bonus:+d}")
        if skill_parts:
            lines.append("Skills " + ", ".join(skill_parts))

    for key, label in _RPTOK_DAMAGE_LABELS:
        text = _forge_text(block.get(key)) or _forge_text(data.get(key))
        if text is not None:
            lines.append(f"{label} {text}")

    # Senses — the 2024 shape carries Passive Perception on this line.
    senses = _forge_text(block.get("senses")) or _forge_text(data.get("senses"))
    perception = _forge_number(block.get("passive_perception"))
    if senses is not None or perception is not None:
        senses_bits: list[str] = []
        if senses is not None:
            senses_bits.append(senses)
        if perception is not None and float(perception).is_integer():
            senses_bits.append(f"Passive Perception {int(perception)}")
        if senses_bits:
            lines.append("Senses " + "; ".join(senses_bits))

    languages = _forge_text(block.get("languages")) or _forge_text(data.get("languages"))
    if languages is not None:
        lines.append(f"Languages {languages}")

    # Challenge derives from the stat_block.identity numerics — level for
    # NPC/BBEG, the CR decimal for Monster (the fg derivation).
    role = identity.get("role")
    challenge: int | float | None = None
    if role in ("NPC", "BBEG"):
        challenge = _forge_number(identity.get("level"))
    elif role == "Monster":
        challenge = _forge_number(identity.get("cr"))
    if challenge is not None:
        lines.append(f"CR {challenge:g}")

    proficiency = _forge_number(block.get("proficiency_bonus"))
    if proficiency is not None:
        lines.append(f"Proficiency Bonus {proficiency:g}")

    trait_lines = _rptok_power_lines(block.get("traits"))
    if trait_lines:
        lines.append("Traits")
        lines.extend(trait_lines)
    action_lines = _rptok_power_lines(block.get("actions"), with_damage=True)
    if action_lines:
        lines.append("Actions")
        lines.extend(action_lines)

    # Spells: a bold-labelled name-only block (the fg record format) after
    # Actions; non-blank spell names only, skipped entirely when empty.
    spells = block.get("spells")
    if isinstance(spells, list):
        spell_names = [name.strip() for name in spells if isinstance(name, str) and name.strip()]
        if spell_names:
            lines.append("<b>Spells</b>")
            lines.extend(f"<b>{name}.</b>" for name in spell_names)

    return "<br>".join(lines)


def _rptok_gmnotes_html(data: dict[str, Any]) -> str:
    """The AR24 lore sections as labelled HTML paragraphs (spec-5-4): one
    ``<p><b>Label</b><br>…</p>`` per non-blank section in the AR24
    profile order (_FG_LORE_FIELDS — the fg notes' order). Blank/absent
    sections are skipped, never invented; the caller escapes the whole
    run once at the content.xml element boundary."""
    paragraphs: list[str] = []
    for key in _FG_LORE_FIELDS:
        value = data.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        runs = [f"<b>{_label(key)}</b>"]
        runs.extend(_fg_note_lines(value))
        paragraphs.append("<p>" + "<br>".join(runs) + "</p>")
    return "".join(paragraphs)


#: A clean dice expression, the pipeline's dice grammar plus an optional
#: embedded sign+bonus (``1d8`` / ``1d8+7`` / ``2d6 - 1``). Full-match
#: anchored: a dice string with trailing junk ("1d8+2 acid") is NOT a
#: clean expression — the part falls back to its integer fields.
_RPTOK_DICE_RE = re.compile(r"(\d+)[dD](\d+)(?:([+-])\s*(\d+))?", flags=re.IGNORECASE)


def _rptok_damage_roll(part: Any) -> str | None:
    """One roll token from an AR25 damage part (spec-5-4): ``{count}d{sides}``
    + ``{±bonus}`` (operators tight, ``+0`` dropped) built from the
    integer fields, or parsed from a CLEAN ``dice`` string — an embedded
    bonus in the dice string is respected and never double-added (a
    separate integer ``bonus`` field is used only when the dice string
    carries none). A part with neither usable integers nor a parseable
    dice string yields no roll (the documented parse-or-skip rule);
    ``count < 1`` or ``sides < 1`` is never rolled."""
    if not isinstance(part, dict):
        return None
    count: int | None
    sides: int | None
    bonus = 0
    dice = part.get("dice")
    if isinstance(dice, str) and dice.strip():
        match = _RPTOK_DICE_RE.fullmatch(dice.strip())
        if match is None:
            count, sides = part.get("count"), part.get("sides")
            if type(count) is not int or type(sides) is not int or count < 1 or sides < 1:
                return None
        else:
            count, sides = int(match.group(1)), int(match.group(2))
            if match.group(3) is not None:
                bonus = int(match.group(4)) * (-1 if match.group(3) == "-" else 1)
            else:
                field_bonus = part.get("bonus")
                if type(field_bonus) is int:
                    bonus = field_bonus
    else:
        count, sides = part.get("count"), part.get("sides")
        if type(count) is not int or type(sides) is not int or count < 1 or sides < 1:
            return None
        field_bonus = part.get("bonus")
        if type(field_bonus) is int:
            bonus = field_bonus
    roll = f"{count}d{sides}"
    if bonus:
        roll = f"{roll}{bonus:+d}"
    return roll


def _rptok_damage_line(damage: Any) -> str | None:
    """A readable damage line for the notes when ``damage_parts_sentence``
    produced nothing (dice-string parts it cannot average): mirrors its
    shape — ``Hit: 1d12+1 damage.`` / ``… plus 2d6 damage.`` — from the
    same ``_rptok_damage_roll`` grammar the macros roll (notes and macro
    buttons never disagree on the dice). None when no part yields a roll."""
    if not isinstance(damage, list):
        return None
    rolls = [roll for part in damage if (roll := _rptok_damage_roll(part)) is not None]
    if not rolls:
        return None
    head, *tail = rolls
    line = f"Hit: {head} damage"
    for roll in tail:
        line += f" plus {roll} damage"
    return line + "."


#: uuid5 namespace for derived macro UUIDs — any fixed value yields
#: deterministic uuid5s; the nil namespace is pinned so the derived IDs
#: can never drift across builds (spec-5-4: derived, never random).
_RPTOK_UUID_NS = uuid.UUID("00000000-0000-0000-0000-000000000000")


def _rptok_attack_macros(block: dict[str, Any], entity_id: str) -> list[dict[str, Any]]:
    """One bare-roll MacroButtonProperties per action with structured
    damage (spec-5-4). Command = ``[1d20+N]`` (only when ``to_hit`` is
    stored, sign-aware) + one ``[XdY±Z]`` per AR25 damage part, tight
    operators, chained in a single chat message; ``macroUUID`` =
    uuid5(pinned namespace, entity_id + 1-based action index) —
    deterministic, never random; ``index`` = the 1-based position in the
    stat-block action order. Actions without usable damage parts —
    pre-2026-09-11 prose-only blocks included — get no button."""
    actions = block.get("actions")
    if not isinstance(actions, list):
        return []
    buttons: list[dict[str, Any]] = []
    for position, item in enumerate(actions, start=1):
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        rolls: list[str] = []
        damage = item.get("damage")
        if isinstance(damage, list):
            for part in damage:
                roll = _rptok_damage_roll(part)
                if roll is not None:
                    rolls.append(f"[{roll}]")
        if not rolls:
            continue
        command: list[str] = []
        to_hit = _forge_number(item.get("to_hit"))
        if to_hit is not None and float(to_hit).is_integer():
            command.append(f"[1d20{int(to_hit):+d}]")
        command.extend(rolls)
        buttons.append(
            {
                "macroUUID": str(uuid.uuid5(_RPTOK_UUID_NS, f"{entity_id}{position}")),
                "index": position,
                "label": name,
                "command": " ".join(command),
            }
        )
    return buttons


_RPTOK_TEMPLATE = (
    "<net.rptools.maptool.model.Token>" + "\n"
    "<id>" + "\n"
    "<baGUID>1C2FKGDORM6JNJ51XguqIw==</baGUID>" + "\n"
    "</id>" + "\n"
    "<beingImpersonated>true</beingImpersonated>" + "\n"
    "<exposedAreaGUID>" + "\n"
    "<baGUID>9maH6b4tRVGCP61AXNzjpA==</baGUID>" + "\n"
    "</exposedAreaGUID>" + "\n"
    "<imageAssetMap>" + "\n"
    "<entry>" + "\n"
    "<null/>" + "\n"
    "<net.rptools.lib.MD5Key>" + "\n"
    "<id>87f4e9bfa4f1f3db250b57b3599fa4e9</id>" + "\n"
    "</net.rptools.lib.MD5Key>" + "\n"
    "</entry>" + "\n"
    "</imageAssetMap>" + "\n"
    "<x>400</x>" + "\n"
    "<y>300</y>" + "\n"
    "<z>1</z>" + "\n"
    "<lastX>0</lastX>" + "\n"
    "<lastY>0</lastY>" + "\n"
    "<anchorX>0</anchorX>" + "\n"
    "<anchorY>0</anchorY>" + "\n"
    "<sizeScale>1.0</sizeScale>" + "\n"
    "<scaleX>1.0</scaleX>" + "\n"
    "<scaleY>1.0</scaleY>" + "\n"
    "<snapToScale>true</snapToScale>" + "\n"
    "<width>200</width>" + "\n"
    "<height>200</height>" + "\n"
    "<isoWidth>0</isoWidth>" + "\n"
    "<isoHeight>0</isoHeight>" + "\n"
    "<sizeMap>" + "\n"
    "<entry>" + "\n"
    "<string>net.rptools.maptool.model.SquareGrid</string>" + "\n"
    "<net.rptools.maptool.model.GUID>" + "\n"
    "<baGUID>fwABAc9lFSoFAAAAKgABAQ==</baGUID>" + "\n"
    "</net.rptools.maptool.model.GUID>" + "\n"
    "</entry>" + "\n"
    "</sizeMap>" + "\n"
    "<snapToGrid>true</snapToGrid>" + "\n"
    "<isVisible>true</isVisible>" + "\n"
    "<visibleOnlyToOwner>false</visibleOnlyToOwner>" + "\n"
    "<vblColorSensitivity>-1</vblColorSensitivity>" + "\n"
    "<alwaysVisibleTolerance>2</alwaysVisibleTolerance>" + "\n"
    "<isAlwaysVisible>false</isAlwaysVisible>" + "\n"
    "<name>TM_NAME</name>" + "\n"
    "<ownerList/>" + "\n"
    "<ownerType>0</ownerType>" + "\n"
    "<tokenShape>CIRCLE</tokenShape>" + "\n"
    "<tokenType>NPC</tokenType>" + "\n"
    "<layer>TOKEN</layer>" + "\n"
    "<propertyType>Basic</propertyType>" + "\n"
    "<tokenOpacity>1.0</tokenOpacity>" + "\n"
    "<speechName/>" + "\n"
    "<terrainModifier>0.0</terrainModifier>" + "\n"
    "<terrainModifierOperation>NONE</terrainModifierOperation>" + "\n"
    "<terrainModifiersIgnored>"
    + "\n"
    + (
        "<net.rptools.maptool.model.Token_-TerrainModifierOperation>NONE"
        "</net.rptools.maptool.model.Token_-TerrainModifierOperation>"
    )
    + "\n"
    "</terrainModifiersIgnored>" + "\n"
    "<isFlippedX>false</isFlippedX>" + "\n"
    "<isFlippedY>false</isFlippedY>" + "\n"
    "<isFlippedIso>false</isFlippedIso>" + "\n"
    '<uniqueLightSources class="linked-hash-map"/>' + "\n"
    "<lightSourceList/>" + "\n"
    "<sightType>Normal</sightType>" + "\n"
    "<hasSight>false</hasSight>" + "\n"
    "<hasImageTable>false</hasImageTable>" + "\n"
    "<notes>Note_NOTES</notes>" + "\n"
    "<notesType>text/html</notesType>" + "\n"
    "<gmNotes>GM_NOTES</gmNotes>" + "\n"
    "<gmNotesType>text/html</gmNotesType>" + "\n"
    "<state/>" + "\n"
    "<propertyMapCI>" + "\n"
    "<store>" + "\n"
    "<entry>" + "\n"
    "<string>dexterity</string>" + "\n"
    "<net.rptools.CaseInsensitiveHashMap_-KeyValue>" + "\n"
    "<key>Dexterity</key>" + "\n"
    '<value class="string">PROP_DEX</value>' + "\n"
    "</net.rptools.CaseInsensitiveHashMap_-KeyValue>" + "\n"
    "</entry>" + "\n"
    "<entry>" + "\n"
    "<string>elevation</string>" + "\n"
    "<net.rptools.CaseInsensitiveHashMap_-KeyValue>" + "\n"
    "<key>Elevation</key>" + "\n"
    '<value class="string">-</value>' + "\n"
    "</net.rptools.CaseInsensitiveHashMap_-KeyValue>" + "\n"
    "</entry>" + "\n"
    "<entry>" + "\n"
    "<string>ac</string>" + "\n"
    "<net.rptools.CaseInsensitiveHashMap_-KeyValue>" + "\n"
    "<key>AC</key>" + "\n"
    '<value class="string">PROP_AC</value>' + "\n"
    "</net.rptools.CaseInsensitiveHashMap_-KeyValue>" + "\n"
    "</entry>" + "\n"
    "<entry>" + "\n"
    "<string>constitution</string>" + "\n"
    "<net.rptools.CaseInsensitiveHashMap_-KeyValue>" + "\n"
    "<key>Constitution</key>" + "\n"
    '<value class="string">PROP_CON</value>' + "\n"
    "</net.rptools.CaseInsensitiveHashMap_-KeyValue>" + "\n"
    "</entry>" + "\n"
    "<entry>" + "\n"
    "<string>strength</string>" + "\n"
    "<net.rptools.CaseInsensitiveHashMap_-KeyValue>" + "\n"
    "<key>Strength</key>" + "\n"
    '<value class="string">PROP_STR</value>' + "\n"
    "</net.rptools.CaseInsensitiveHashMap_-KeyValue>" + "\n"
    "</entry>" + "\n"
    "<entry>" + "\n"
    "<string>defense</string>" + "\n"
    "<net.rptools.CaseInsensitiveHashMap_-KeyValue>" + "\n"
    "<key>Defense</key>" + "\n"
    '<value class="string">-</value>' + "\n"
    "</net.rptools.CaseInsensitiveHashMap_-KeyValue>" + "\n"
    "</entry>" + "\n"
    "<entry>" + "\n"
    "<string>hp</string>" + "\n"
    "<net.rptools.CaseInsensitiveHashMap_-KeyValue>" + "\n"
    "<key>HP</key>" + "\n"
    '<value class="string">PROP_HP</value>' + "\n"
    "</net.rptools.CaseInsensitiveHashMap_-KeyValue>" + "\n"
    "</entry>" + "\n"
    "<entry>" + "\n"
    "<string>description</string>" + "\n"
    "<net.rptools.CaseInsensitiveHashMap_-KeyValue>" + "\n"
    "<key>Description</key>" + "\n"
    '<value class="string">PROP_DESC</value>' + "\n"
    "</net.rptools.CaseInsensitiveHashMap_-KeyValue>" + "\n"
    "</entry>" + "\n"
    "<entry>" + "\n"
    "<string>movement</string>" + "\n"
    "<net.rptools.CaseInsensitiveHashMap_-KeyValue>" + "\n"
    "<key>Movement</key>" + "\n"
    '<value class="string">PROP_MOVE</value>' + "\n"
    "</net.rptools.CaseInsensitiveHashMap_-KeyValue>" + "\n"
    "</entry>" + "\n"
    "<entry>" + "\n"
    "<string>charisma</string>" + "\n"
    "<net.rptools.CaseInsensitiveHashMap_-KeyValue>" + "\n"
    "<key>Charisma</key>" + "\n"
    '<value class="string">PROP_CHA</value>' + "\n"
    "</net.rptools.CaseInsensitiveHashMap_-KeyValue>" + "\n"
    "</entry>" + "\n"
    "<entry>" + "\n"
    "<string>intelligence</string>" + "\n"
    "<net.rptools.CaseInsensitiveHashMap_-KeyValue>" + "\n"
    "<key>Intelligence</key>" + "\n"
    '<value class="string">PROP_INT</value>' + "\n"
    "</net.rptools.CaseInsensitiveHashMap_-KeyValue>" + "\n"
    "</entry>" + "\n"
    "<entry>" + "\n"
    "<string>wisdom</string>" + "\n"
    "<net.rptools.CaseInsensitiveHashMap_-KeyValue>" + "\n"
    "<key>Wisdom</key>" + "\n"
    '<value class="string">PROP_WIS</value>' + "\n"
    "</net.rptools.CaseInsensitiveHashMap_-KeyValue>" + "\n"
    "</entry>" + "\n"
    "</store>" + "\n"
    "</propertyMapCI>" + "\n"
    "<macroPropertiesMap>" + "\n"
    "MACRO_ENTRIES" + "\n"
    "</macroPropertiesMap>" + "\n"
    "<speechMap/>" + "\n"
    "<allowURIAccess>false</allowURIAccess>" + "\n"
    "</net.rptools.maptool.model.Token>"
)


#: The template's two identity literals — the token-id baGUID and the
#: imageAssetMap MD5Key — are replaced with derived/committed values; the
#: exposedAreaGUID/sizeMap GUIDs stay verbatim (template fidelity).
_RPTOK_BA_GUID_MARKER = "1C2FKGDORM6JNJ51XguqIw=="
_RPTOK_IMAGE_MD5_MARKER = "87f4e9bfa4f1f3db250b57b3599fa4e9"

#: All template marker tokens, substituted in ONE regex pass — a replaced
#: value (name/notes/props/macros) is never re-scanned for marker text.
_RPTOK_MARKERS: tuple[str, ...] = (
    _RPTOK_BA_GUID_MARKER,
    _RPTOK_IMAGE_MD5_MARKER,
    "TM_NAME",
    "Note_NOTES",
    "GM_NOTES",
    "PROP_STR",
    "PROP_DEX",
    "PROP_CON",
    "PROP_INT",
    "PROP_WIS",
    "PROP_CHA",
    "PROP_AC",
    "PROP_HP",
    "PROP_MOVE",
    "PROP_DESC",
    "MACRO_ENTRIES",
)
_RPTOK_MARKER_RE = re.compile("|".join(re.escape(marker) for marker in _RPTOK_MARKERS))

#: Leading numeric of a speed string ("30 ft." -> 30; "Swim 60 ft." -> None).
_RPTOK_SPEED_NUM = re.compile(r"\d+(?:\.\d+)?")


def _rptok_speed_number(speed: Any) -> int | float | None:
    """The leading numeric of a committed speed string (the MOVE property):
    "30 ft." -> 30; a non-numeric speed -> None ("-" per the template's
    own convention for absent slots)."""
    if not isinstance(speed, str):
        return None
    match = _RPTOK_SPEED_NUM.match(speed.strip())
    if match is None:
        return None
    text = match.group(0)
    return int(text) if text.isdigit() else float(text)


def _rptok_properties(block: dict[str, Any], data: dict[str, Any]) -> dict[str, str]:
    """The propertyMapCI string values (the template's PROP_* markers ->
    committed numbers; "-" per the template's own convention for absent
    slots — Elevation/Defense/Description ride as "-"). Ability scores,
    AC and HP read exactly like the notes/fg renderers; non-integral
    values are omitted ("-"), never truncated."""
    attributes = block.get("attributes")
    attributes = attributes if isinstance(attributes, dict) else {}
    combat = block.get("combat")
    combat = combat if isinstance(combat, dict) else {}
    values: dict[str, Any] = {}
    for ability in _ABILITY_ORDER:
        score = _forge_number(attributes.get(ability))
        values[f"PROP_{ability.upper()}"] = (
            int(score) if score is not None and float(score).is_integer() else None
        )
    ac = _forge_number(combat.get("ac"))
    if ac is None:
        ac = _forge_number(combat.get("armor_class"))
    hp = _forge_number(combat.get("hp"))
    if hp is None:
        hp = _forge_number(combat.get("hit_points"))
    values["PROP_AC"] = int(ac) if ac is not None and float(ac).is_integer() else None
    values["PROP_HP"] = int(hp) if hp is not None and float(hp).is_integer() else None
    speed = combat.get("speed")
    if not isinstance(speed, str):
        speed = block.get("speed")
    values["PROP_MOVE"] = _rptok_speed_number(speed)
    values["PROP_DESC"] = None
    return {marker: "-" if value is None else str(value) for marker, value in values.items()}


def _rptok_macro_buttons_xml(buttons: list[dict[str, Any]]) -> str:
    """The MACRO_ENTRIES slot content (template fidelity): one UNINDENTED
    ``<entry><int>N</int>`` + full MacroButtonProperties block per button —
    the fixture's field set copied field-for-field (saveLocation=Token,
    colorKey=default, hotKey=None, autoExecute=true, includeLabel=false,
    applyToTokens=false, fontColorKey=default, fontSize=1.00em,
    displayHotKey=true, commonMacro=false, compare* true,
    allowPlayerEdits=true)."""
    entries: list[str] = []
    for button in buttons:
        index = button["index"]
        entries.append(
            "<entry>\n"
            f"<int>{index}</int>\n"
            "<net.rptools.maptool.model.MacroButtonProperties>\n"
            f"<macroUUID>{button['macroUUID']}</macroUUID>\n"
            "<saveLocation>Token</saveLocation>\n"
            f"<index>{index}</index>\n"
            "<colorKey>default</colorKey>\n"
            "<hotKey>None</hotKey>\n"
            f"<command>{_xml10(button['command'])}</command>\n"
            f"<label>{_xml10(button['label'])}</label>\n"
            "<group></group>\n"
            "<sortby></sortby>\n"
            "<autoExecute>true</autoExecute>\n"
            "<includeLabel>false</includeLabel>\n"
            "<applyToTokens>false</applyToTokens>\n"
            "<fontColorKey>default</fontColorKey>\n"
            "<fontSize>1.00em</fontSize>\n"
            "<minWidth></minWidth>\n"
            "<maxWidth></maxWidth>\n"
            "<allowPlayerEdits>true</allowPlayerEdits>\n"
            "<toolTip></toolTip>\n"
            "<displayHotKey>true</displayHotKey>\n"
            "<commonMacro>false</commonMacro>\n"
            "<compareGroup>true</compareGroup>\n"
            "<compareSortPrefix>true</compareSortPrefix>\n"
            "<compareCommand>true</compareCommand>\n"
            "<compareIncludeLabel>true</compareIncludeLabel>\n"
            "<compareAutoExecute>true</compareAutoExecute>\n"
            "<compareApplyToSelectedTokens>true</compareApplyToSelectedTokens>\n"
            "</net.rptools.maptool.model.MacroButtonProperties>\n"
            "</entry>"
        )
    return "\n".join(entries)


def _rptok_content_xml(
    entity: EntityExport,
    notes: str,
    gm_notes: str,
    image_md5: str | None,
    buttons: list[dict[str, Any]],
    props: dict[str, str],
) -> str:
    """The token's content.xml — the owner's Dragon template VERBATIM with
    ONLY the marker tokens substituted (owner directive 2026-09-14): the
    derived id GUID, the imageAssetMap MD5Key (when an image embeds; a
    broken default leaves the template's literal — a dangling reference
    MapTool skips with a log error, the old no-image behavior), the name,
    the notes/gmNotes HTML runs, the propertyMapCI values and the
    MACRO_ENTRIES. Every other byte stays exactly the template's (a real
    1.18.6 save — positions, exposedAreaGUID, sizeMap grid GUID, runtime
    flags included). Substitution is ONE regex pass, so replaced values
    are never re-scanned for marker text; notes/gmNotes get their single
    XML-escape pass here and keep the pinned ``<notes></notes>`` form
    when empty."""
    replacements: dict[str, str] = {
        _RPTOK_BA_GUID_MARKER: _rptok_guid(entity.id),
        _RPTOK_IMAGE_MD5_MARKER: image_md5 if image_md5 is not None else _RPTOK_IMAGE_MD5_MARKER,
        "TM_NAME": _xml10(entity.name),
        "Note_NOTES": _xml10(notes),
        "GM_NOTES": _xml10(gm_notes),
        "MACRO_ENTRIES": _rptok_macro_buttons_xml(buttons),
    }
    replacements.update({marker: _xml10(value) for marker, value in props.items()})
    return _RPTOK_MARKER_RE.sub(lambda match: replacements[match.group(0)], _RPTOK_TEMPLATE)


#: properties.xml verbatim — the fixture's Map<String,Object> shape with
#: the pinned version 1.18.6 (research-5-4 §2b: version ≤ client loads).
_RPTOK_PROPERTIES_XML = (
    "<map>\n"
    "  <entry>\n"
    "    <string>version</string>\n"
    "    <string>1.18.6</string>\n"
    "  </entry>\n"
    "  <entry>\n"
    "    <string>herolab</string>\n"
    "    <boolean>false</boolean>\n"
    "  </entry>\n"
    "</map>\n"
)


def _rptok_asset_descriptor_xml(entity: EntityExport, image_md5: str) -> str:
    """The ``assets/<md5>`` Asset descriptor — the fixture's exact shape:
    a nested ``<id>`` holding the 32-hex md5, the token name, and the
    png/IMAGE pins (the parse contract in PackedFile.getAsset)."""
    return (
        "<net.rptools.maptool.model.Asset>\n"
        "  <id>\n"
        f"    <id>{image_md5}</id>\n"
        "  </id>\n"
        f"  <name>{_xml10(entity.name)}</name>\n"
        "  <extension>png</extension>\n"
        "  <type>IMAGE</type>\n"
        "</net.rptools.maptool.model.Asset>\n"
    )


#: Fixed member timestamp for every zip entry (spec-5-4: no wall clock).
_RPTOK_ZIP_DATE = (1980, 1, 1, 0, 0, 0)


def _rptok_zip(entries: list[tuple[str, bytes]]) -> bytes:
    """A deterministic ZIP (spec-5-4): fixed member timestamps, pinned
    deflate, no member extra fields, stable entry order. Deflate is
    deterministic for identical inputs — byte-identity is scoped to one
    Python/zlib build (design note)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for arcname, payload in entries:
            info = zipfile.ZipInfo(arcname, date_time=_RPTOK_ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, payload)
    return buffer.getvalue()


def render_entity_maptool(export: WorldExport, entity_id: str) -> bytes:
    """MapTool 1.18.6 token file (spec-5-4): one committed entity as a
    deterministic ``.rptok`` ZIP — content.xml (the Token XML = the
    fixture's named stable subset), properties.xml (version 1.18.6), and
    the ``assets/<md5>`` Asset descriptor + raw PNG member pair. The
    portrait is the newest AVAILABLE ``kind=image`` row whose on-disk
    bytes start with the PNG magic; a missing/broken portrait embeds the
    bundled default token image instead (honesty marker in the notes; no
    marker when the entity has no media); a broken default falls back to
    the no-image shape. Pure function of the snapshot plus file bytes —
    no wall clock, no store write, byte-identical repeats (FR18)."""
    entity = next(e for e in export.entities if e.id == entity_id)
    data = entity.data if isinstance(entity.data, dict) else {}
    block = data.get("stat_block")
    block = block if isinstance(block, dict) else {}

    notes = _rptok_notes_html(block, data)
    image_bytes, marker = _rptok_token_image(export, entity)
    if marker is not None:
        notes = f"{notes}<br>{marker}" if notes else marker
    gm_notes = _rptok_gmnotes_html(data)
    buttons = _rptok_attack_macros(block, entity.id)

    image_md5 = _rptok_md5(image_bytes) if image_bytes is not None else None
    content = _rptok_content_xml(
        entity, notes, gm_notes, image_md5, buttons, _rptok_properties(block, data)
    )
    entries: list[tuple[str, bytes]] = [
        ("content.xml", content.encode("utf-8")),
        ("properties.xml", _RPTOK_PROPERTIES_XML.encode("utf-8")),
    ]
    if image_md5 is not None:
        assert image_bytes is not None
        entries.append(
            (f"assets/{image_md5}", _rptok_asset_descriptor_xml(entity, image_md5).encode("utf-8"))
        )
        entries.append((f"assets/{image_md5}.png", image_bytes))
    return _rptok_zip(entries)
