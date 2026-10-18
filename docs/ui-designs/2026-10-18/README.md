# Frozen UI design code · 2026-10-18

These are the literal HTML/CSS/JavaScript prototypes used in design review, saved in Git so implementation does not have to reconstruct them from screenshots or prose. No application routes, backend behavior or production deployment change here.

## Open the designs

From this directory run `python -m http.server 8765 --bind 127.0.0.1` and open `http://127.0.0.1:8765/`. Or open a full HTML file from `mockups/` directly. Stop the server with Ctrl+C.

| Design | Literal runnable source | Extracted code | Review state |
|---|---|---|---|
| Guided Build / World workspace · v6 | [HTML](mockups/guided-build-proposal.html) | [Template](code/guided-build/template.html), [CSS](code/guided-build/styles.css), [JavaScript](code/guided-build/interactions.js) | Accepted direction, good enough for now; backend work required |
| Your Worlds / new-world lore · v2 | [HTML](mockups/worlds-proposal.html) | [Code](code/worlds/) | Design proposal |
| Add Character · v2 | [HTML](mockups/add-character-proposal.html) | [Code](code/add-character/) | Design proposal |
| Private Library · v2 | [HTML](mockups/library-proposal.html) | [Code](code/library/) | Design proposal |
| Forge · v7 | [HTML](mockups/forge-superdesign-v7.html) | [Code](code/forge-reference/) | Style reference; leave Forge unchanged |
| World directory · v1 | [HTML](mockups/world-superdesign-baseline.html) | [Code](code/world-reference/) | Existing-layout reference |
| Overview · v3 | [HTML](mockups/overview-superdesign-baseline.html) | [Code](code/overview-reference/) | Existing-layout reference |

In Guided Build choose **Explore a sample world**, then **Harbor → + Place → + Character** beneath that planned place. **Move, connect & guide** exposes placement, membership and guidance. Try removal Undo and Generate planned additions.

## Preserve during implementation

Use the frozen markup and styles directly as the starting point for Vue components. The extracted files are mechanical copies of inline styles, inline scripts and body markup; they are not a framework rewrite or a production runtime. Reuse the production shared components/editor and replace local demo controllers with the real store/API contracts. Inline event attributes and generated HTML strings in the prototypes need Vue bindings. Never substitute the demo generation/save/move logic for backend work.

- Compare implemented screens with the current desktop/mobile screenshots, including the expanded editor states; [IMPLEMENTATION-PARITY.md](IMPLEMENTATION-PARITY.md) records the behavior checklist.
- Keep named/shaped/list/authored inputs, counts, notes, nested place creation, stable relationships, membership, move controls, Undo and the Existing/Planned distinction. An intentional change needs a recorded reason and a comparison, rather than silent omission.
- The first Guided Build and ongoing workspace are separate. Generate only unfinished additions; reconcile confirmed results and preserve incomplete work after partial failure.
- Read [BACKEND-HANDOFF.md](BACKEND-HANDOFF.md) before implementation. It is required scope, including typed planning, model-named slots, canonical validation/name collision handling, ID mapping, persistence and idempotent retries.
- [DESIGN.md](DESIGN.md), [EXPERIENCE.md](EXPERIENCE.md) and [CONCEPT-REVIEW.md](CONCEPT-REVIEW.md) retain visual/behavioral guidance and bounded verification evidence. Historical artifacts are included for provenance; they do not override the versioned sources in the manifest.

## Integrity and dependencies

[manifest.json](manifest.json) identifies exact draft versions, SHA-256 source hashes, extracted code and external script dependencies. [checksums.json](checksums.json) freezes bundle files. Run `python verify.py` to validate integrity, compare extracted parts to their original source, and syntax-check JavaScript with Node 22. No package installation is required.

The literal prototypes retain their original Tailwind CDN and, where present, Iconify/Google Fonts resources. Internet access is needed for those dependencies; this is not an offline-certified bundle. Local CSS, controller code, tokens and screenshots are preserved even if a design service disappears. Prototype navigation links retain original remote/hash destinations, and do not represent production routes. Fixtures use sample data and generation is a local demonstration. Screenshot states may differ; refer to the review record.

These files are frozen references. Implement application changes in frontend/backend source; create another dated design snapshot when the design changes. Do not silently edit the frozen source or update hashes to conceal divergence.

Archived guide links were relocated for this tracked directory. Unavailable older references are marked as historical plain text; literal prototype HTML was not rewritten.
