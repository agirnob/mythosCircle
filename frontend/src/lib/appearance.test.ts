// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest'

import { hasNonBlankAppearance } from './appearance'

describe('hasNonBlankAppearance (spec-4.1 shared gate)', () => {
  it('accepts a non-blank string verbatim', () => {
    expect(hasNonBlankAppearance('gaunt, ink-stained fingers')).toBe(true)
    expect(hasNonBlankAppearance('  sharp features  ')).toBe(true)
  })

  it('accepts a dict with at least one non-blank known key', () => {
    expect(hasNonBlankAppearance({ face: 'hollow eyes', unknown_key: 'x' })).toBe(true)
    expect(hasNonBlankAppearance({ body: 'lean' })).toBe(true)
    expect(hasNonBlankAppearance({ clothing: '  patched coat  ' })).toBe(true)
  })

  it('rejects a dict with only unknown keys (never a prompt source)', () => {
    expect(hasNonBlankAppearance({ ambient_detail: 'cloak' })).toBe(false)
    expect(hasNonBlankAppearance({ face: '', unknown_key: 'x' })).toBe(false)
    expect(hasNonBlankAppearance({ clothing: '   ' })).toBe(false)
  })

  it('rejects blank and whitespace strings', () => {
    expect(hasNonBlankAppearance('')).toBe(false)
    expect(hasNonBlankAppearance('   \n\t ')).toBe(false)
  })

  it('rejects non-string, non-dict shapes', () => {
    expect(hasNonBlankAppearance(42)).toBe(false)
    expect(hasNonBlankAppearance(null)).toBe(false)
    expect(hasNonBlankAppearance(undefined)).toBe(false)
    expect(hasNonBlankAppearance(['a', 'b'])).toBe(false)
  })
})