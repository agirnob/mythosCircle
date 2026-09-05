/**
 * AR24 appearance gate for portraits (spec-4.1, FR12).
 *
 * The portrait prompt is a projection of the committed entity's AR24
 * `appearance` — never free text — and the backend enqueues an image
 * job only when a non-blank appearance exists
 * (store.jobs._validate_image_payload). THE frontend mirrors that gate
 * through ONE helper so the WorldView button and the CandidatesView
 * accept path can never diagree about what "has an appearance" means.
 * The backend keeps its own copy
 * (app.media.service.appearance_prompt) with the identical shape —
 * dict-known-keys-join | verbatim string | blank -> no portrait.
 */

/** The known AR24 appearance keys (the frozen spec's prompt-source list). */
export const APPEARANCE_KEYS: readonly string[] = [
  'face',
  'body',
  'clothing',
  'scars',
  'marks',
]

/** True iff the appearance can produce a portrait prompt: a non-blank
 * string verbatim, or a dict with at least one non-blank KNOWN key
 * (unknown keys are AR24-forward-compatible but never prompt sources). */
export function hasNonBlankAppearance(appearance: unknown): boolean {
  if (typeof appearance === 'string') return appearance.trim() !== ''
  if (typeof appearance !== 'object' || appearance === null || Array.isArray(appearance)) {
    return false
  }
  const record = appearance as Record<string, unknown>
  return APPEARANCE_KEYS.some(
    (key) => typeof record[key] === 'string' && String(record[key]).trim() !== '',
  )
}