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
import html as _html
import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.core.settings import configured_media_dir
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
:root { color-scheme: light; }
@page { size: auto; margin: 18mm 16mm; }
* { box-sizing: border-box; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body {
  font-family: "Iowan Old Style", "Palatino Linotype", Palatino, Georgia, serif;
  color: #26201a; background: #ffffff; margin: 0 auto; max-width: 19cm;
  padding: 2rem 1.5rem; line-height: 1.45; font-size: 11.5pt;
}
h1 { font-size: 1.9rem; margin: 0 0 0.15rem; color: #1a3a5c; letter-spacing: 0.02em; }
h2, h3 { color: #1a3a5c; margin: 1.2rem 0 0.4rem; break-after: avoid; page-break-after: avoid; }
.meta { color: #6b5d4f; font-size: 0.9rem; margin: 0.1rem 0; }
.small { font-size: 0.8rem; }
.mono { font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace; }
.doc-header { border-bottom: 4px double #1a3a5c; margin-bottom: 1.2rem; padding-bottom: 0.4rem; }
.entity-card {
  background: #f8f3e6; border-top: 3px double #1a3a5c; border-bottom: 3px double #1a3a5c;
  padding: 0.9rem 1.1rem 0.6rem; margin: 0 0 1.6rem; break-inside: avoid; page-break-inside: avoid;
}
.hero { text-align: center; margin: 0.6rem 0; break-inside: avoid; page-break-inside: avoid; }
.hero img { max-width: 100%; max-height: 9cm; border: 1px solid #c9b28a; }
.prose { white-space: pre-line; margin: 0.45rem 0; }
.stat-block {
  background: #fdfaf1; border: 1px solid #c9b28a; padding: 0.7rem 0.9rem;
  margin: 0.8rem 0; break-inside: avoid; page-break-inside: avoid;
}
.stat-block h3 {
  margin-top: 0; border-bottom: 1px solid #1a3a5c; padding-bottom: 0.15rem;
}
.abilities {
  display: grid; grid-template-columns: repeat(6, 1fr); gap: 0.4rem;
  text-align: center; margin: 0.5rem 0;
}
.ab { break-inside: avoid; }
.ab-k {
  display: block; font-weight: 700; font-size: 0.8rem;
  letter-spacing: 0.06em; color: #1a3a5c;
}
.ab-v { display: block; font-size: 1.05rem; }
dl.fields dt { font-weight: 700; color: #1a3a5c; font-size: 0.9rem; margin-top: 0.35rem; }
dl.fields dd { margin: 0 0 0.2rem 1rem; }
ul.entries { margin: 0.35rem 0; padding-left: 1.2rem; }
ul.entries li { margin: 0.15rem 0; break-inside: avoid; }
.relations, .media { font-size: 0.95rem; }
.badge { color: #8b1a1a; font-weight: 700; }
table.edges { border-collapse: collapse; width: 100%; margin: 0.6rem 0; }
table.edges th, table.edges td {
  border: 1px solid #b9a97f; padding: 0.25rem 0.5rem;
  text-align: left; font-size: 0.9rem;
}
table.edges th { background: #1a3a5c; color: #ffffff; }
table.edges tr { break-inside: avoid; page-break-inside: avoid; }
details.appendix { margin-top: 1rem; font-size: 0.85rem; }
details.appendix pre {
  white-space: pre-wrap; word-break: break-word; background: #f1ead9; padding: 0.6rem;
}
a { color: inherit; }
p { orphans: 3; widows: 3; }
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
        lines.append(f"- {neighbor} <--{_edge_label(edge)}-- {subject}")
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
#: different because of it (dogfood fix 2026-09-09).
_STAT_BLOCK_ORDER: tuple[str, ...] = (
    "identity",
    "attributes",
    "combat",
    "skills",
    "actions",
    "traits",
    "spells",
)

#: Field order for the known section dicts; unknown keys follow in
#: committed order.
_KEY_ORDER: dict[str, tuple[str, ...]] = {
    "attributes": _ABILITY_ORDER,
    "identity": ("role", "level", "cr", "race", "class", "alignment"),
    "combat": ("ac", "armor_class", "hp", "hit_points", "speed", "initiative"),
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
    return str(key).replace("_", " ").capitalize()


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


def _stat_block_panel(stat_block: Any) -> str:
    """The dedicated stat-block section. A layout preference over the
    committed shape (spec-2.4/AR25: identity, attributes, combat, skills,
    actions, traits, spells) — every key still renders, unknown or not."""
    if not isinstance(stat_block, dict):
        return f"<section>{_render_value(stat_block)}</section>"
    parts = ["<h3>Stat Block</h3>"]
    for key, value in _ordered(stat_block, _STAT_BLOCK_ORDER):
        if key == "attributes" and isinstance(value, dict):
            parts.append(_abilities_grid(value))
        elif isinstance(value, dict):
            parts.append(
                f"<h4>{_esc(_label(str(key)))}</h4>"
                f"{_render_mapping(value, _KEY_ORDER.get(key, ()))}"
            )
        else:
            rendered = _render_value(value)
            if rendered:
                parts.append(f"<h4>{_esc(_label(str(key)))}</h4>{rendered}")
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
    header = [
        f"<h2>{_esc(names[entity.id])}</h2>",
        f'<p class="meta">{_esc(entity.kind)}'
        + (f" · {_esc(' · '.join(identity_bits))}" if identity_bits else "")
        + "</p>",
    ]
    body = []
    if embed:
        body.append(_hero_figure(export, entity))
    if entity.text is not None:
        body.append(f'<p class="prose">{_esc(entity.text)}</p>')
    for key, value in data.items():
        if key == _STAT_BLOCK_KEY:
            body.append(_stat_block_panel(value))
        elif (
            key in identity_consumed
            or (key == "name" and isinstance(value, str))
            or value is None
            or value == ""
        ):
            continue
        else:
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
            relations.append(f"<li>{neighbor} &lt;--{label}-- {subject}</li>")
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
    return f'<article class="entity-card">{"".join(header)}{"".join(body)}</article>'


def _document(title: str, subtitle: str, body: str) -> str:
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8"/>\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1"/>\n'
        f"<title>{_esc(title)}</title>\n"
        f"<style>{SHEET_CSS}</style>\n</head>\n<body>\n"
        f'<header class="doc-header"><h1>{_esc(title)}</h1>{subtitle}</header>\n'
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
    """The single-character sheet: portrait embedded, stat block, every
    committed section, relations, media, and the verbatim JSON appendix."""
    names = name_labels(export)
    entity = next(e for e in export.entities if e.id == entity_id)
    subtitle = (
        f'<p class="meta">{_esc(export.campaign.title)} · {_esc(export.campaign.theme)}</p>'
        + _revision_meta(export)
    )
    return _document(
        names[entity.id],
        subtitle,
        _entity_sections(export, entity, names, embed=True, appendix=True) + "\n",
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
    body_parts = [
        _entity_sections(export, entity, names, embed=False, appendix=False)
        for entity in export.entities
    ]
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
    return _document(export.campaign.title, "".join(subtitle_parts), "".join(body_parts))


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
#: floor((score-10)/2); Z034 traits; Z035 actions; Z038
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


def _forge_entries(entity_id: str, items: Any) -> list[dict[str, str]]:
    """A name/description list as Forge ``list`` entries with index-stable
    ids (``{entity-id-8}-{i}`` — never fresh ULIDs, so repeats are
    byte-identical). Nameless members are skipped, not failed."""
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
    actions = _forge_entries(entity_id, block.get("actions"))
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
