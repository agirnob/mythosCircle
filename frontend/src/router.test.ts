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

  it('resolves the graph route under its campaign (story 7.1 entry point)', () => {
    const resolved = router.resolve({ name: 'graph', params: { id: 'X' }, query: { focus: 'E1' } })
    expect(resolved.path).toBe('/campaigns/X/graph')
    expect(resolved.query.focus).toBe('E1')
    expect(resolved.meta.requiresAuth).toBe(true)
  })

  it('resolves the overview route under its campaign (rebuild stage 2)', () => {
    expect(router.resolve({ name: 'overview', params: { id: 'X' } }).path).toBe(
      '/campaigns/X/overview',
    )
  })

  it('resolves the entity detail route under its campaign (rebuild stage 3)', () => {
    const resolved = router.resolve({ name: 'entity', params: { id: 'X', entityId: 'E1' } })
    expect(resolved.path).toBe('/campaigns/X/entities/E1')
    expect(resolved.meta.requiresAuth).toBe(true)
  })

  it('resolves the ask route under its campaign (rebuild stage 4)', () => {
    const resolved = router.resolve({ name: 'ask', params: { id: 'X' } })
    expect(resolved.path).toBe('/campaigns/X/ask')
    expect(resolved.meta.requiresAuth).toBe(true)
  })
})
