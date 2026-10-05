/** Scoped journal requests; schema aliases are generated from OpenAPI. */
import type { components } from './schema'

export type PlaySession = components['schemas']['PlaySessionResponse']
export type JournalEntry = components['schemas']['JournalEntryResponse']
export type JournalReference = components['schemas']['JournalReference']
export type JournalDraft = Pick<JournalEntry, 'headline' | 'context' | 'references'>

export const requestKey = () => globalThis.crypto.randomUUID()
