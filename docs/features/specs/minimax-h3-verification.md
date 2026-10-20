# H3 enhancement verification

Independent browser verification used a disposable database, private test account,
real job queue and real OpenAI-compatible HTTP adapter. A deterministic vision
test double checked the received image bytes and supplied a known H3 response;
this verifies integration, not live model quality.

- The request carried the selected PNG's actual bytes (SHA-256
  `7535dfead47a5b1ffc6e360c042b8010ab04dde9351bed31ec560abf08ba90eb`)
  and the entered video direction. Deliberately conflicting appearance and lore
  markers from the entity and campaign were absent.
- The response appeared as the H3 alignment line and three required sections.
- A manual edit survived a page refresh and a newly inserted portrait.
- The render adapter received that exact edit and the original portrait filename,
  rather than the newer portrait.
- A deliberate HTTP 400 from the vision test double produced a visible failure
  while preserving the direction and edited prompt.
- At a 390-pixel viewport, both textareas stayed inside the panel; the source
  preview used `object-fit: contain`. Selecting **Use latest portrait** switched
  to the new image while retaining the direction and edited prompt.
- Provider tests cover local, Gemini and OpenRouter request shapes, bounded backup
  calls retaining the image, absence of base64 in journals/settings representations,
  and unchanged text-only message shape.

The real local app's selected OpenRouter provider had no API key configured during
verification. No live vision inference is claimed, and provider routing was left
unchanged. The previously tested FastVideo workflow handles rendering separately.

Independent reviews covered UI lifecycle, image binding, ownership, validation,
and missing regression tests. Follow-up fixes add explicit source replacement,
pending request guards, asynchronous restoration, complete-image previews and
image decode limit tests.

Final checks: 1,904 backend tests passed; 551 frontend tests passed under Node 22.23.3. Type checking, production build, lint and offline `uv lock --check` passed. Existing notices: one Starlette/httpx deprecation warning and twelve frontend lint warnings.
