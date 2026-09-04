// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest'

import router from './router'

// The real route table: the WorldView link and the CandidatesView route
// are wired by name — this pins the path the name resolves to.
describe('router', () => {
  it('resolves the candidates route under its campaign', () => {
    expect(router.resolve({ name: 'candidates', params: { id: 'X' } }).path).toBe(
      '/campaigns/X/candidates',
    )
  })

  it('resolves the world route under its campaign', () => {
    expect(router.resolve({ name: 'world', params: { id: 'X' } }).path).toBe('/campaigns/X/world')
  })
})
