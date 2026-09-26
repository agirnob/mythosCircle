// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'

import EdgeComposer from './EdgeComposer.vue'

const kinds = {
  version: 'abc',
  edge_types: ['debt', 'located_in', 'part_of', 'relationship'],
  kind_rules: {
    debt: {
      src: ['character', 'faction'],
      dst: ['character', 'faction'],
      counter_semantic: 'count',
      counter_bounds: [1, 9],
    },
    located_in: {
      src: ['character'],
      dst: ['place'],
      counter_semantic: 'neutral',
      counter_bounds: [1],
    },
    part_of: {
      src: ['place', 'faction'],
      dst: ['place', 'faction'],
      counter_semantic: 'neutral',
      counter_bounds: [1],
    },
    // The legacy catch-all: null src/dst means ANY kind — it must be
    // offered to every source, never filtered out (live-matrix lesson).
    relationship: {
      src: null,
      dst: null,
      counter_semantic: 'neutral',
      counter_bounds: null,
    },
  },
  dial_levels: ['draft', 'simple'],
  archetypes: [],
}

const candidates = [
  { id: 'C1', name: 'Roth', kind: 'character' },
  { id: 'P1', name: 'Greymarch', kind: 'place' },
]

describe('EdgeComposer', () => {
  it('only offers relations the source kind may emit, then filters targets by the dst set', async () => {
    const srcEntity = { id: 'E1', name: 'Mira', kind: 'character' }
    const wrapper = mount(EdgeComposer, {
      props: { kinds, srcEntity, candidates, busy: false },
    })
    const typeOptions = wrapper
      .findAll('select')[0]!
      .findAll('option')
      .map((option) => option.text())
    // character src: debt (character/faction), located_in (place) AND the
    // null-src catch-all relationship (any kind) — NOT part_of (no
    // character cell). Option labels carry the cell's counter semantic.
    expect(typeOptions.some((text) => text.includes('debt'))).toBe(true)
    expect(typeOptions.some((text) => text.includes('located_in'))).toBe(true)
    expect(typeOptions.some((text) => text.includes('relationship'))).toBe(true)
    expect(typeOptions.some((text) => text.includes('part_of'))).toBe(false)

    await wrapper.findAll('select')[0]!.setValue('located_in')
    await wrapper.findAll('select')[0]!.trigger('change')
    const targetOptions = wrapper
      .findAll('select')[1]!
      .findAll('option')
      .map((option) => option.text())
    // located_in dst = place only: Roth drops out, Greymarch stays.
    expect(targetOptions.some((text) => text.includes('Roth'))).toBe(false)
    expect(targetOptions.some((text) => text.includes('Greymarch'))).toBe(true)
  })

  it('blocks submit until a reason is present (AD-32 client gate)', async () => {
    const srcEntity = { id: 'E1', name: 'Mira', kind: 'character' }
    const wrapper = mount(EdgeComposer, {
      props: { kinds, srcEntity, candidates, busy: false },
    })
    await wrapper.findAll('select')[0]!.setValue('debt')
    await wrapper.findAll('select')[0]!.trigger('change')
    await wrapper.findAll('select')[1]!.setValue('C1')
    expect(wrapper.find('button[type="submit"]').attributes('disabled')).toBeDefined() // no reason yet
    await wrapper.find('textarea').setValue('Sold a cover note against the toll')
    expect(wrapper.find('button[type="submit"]').attributes('disabled')).toBeUndefined()
    await wrapper.find('form').trigger('submit')
    const events = wrapper.emitted('create')
    expect(events).toBeDefined()
    expect(events![0]![0]).toEqual({
      src: 'E1',
      dst: 'C1',
      type: 'debt',
      counter: 1,
      reason: 'Sold a cover note against the toll',
    })
  })

  it('a kind without outgoing relations renders an honest empty state', () => {
    // A fixture WITHOUT a null-src catch-all: nothing names 'deity'.
    const strictKinds = {
      version: 'abc',
      edge_types: ['part_of'],
      kind_rules: {
        part_of: {
          src: ['place', 'faction'],
          dst: ['place', 'faction'],
          counter_semantic: 'neutral',
          counter_bounds: [1],
        },
      },
      dial_levels: ['draft', 'simple'],
      archetypes: [],
    }
    const wrapper = mount(EdgeComposer, {
      props: {
        kinds: strictKinds,
        srcEntity: { id: 'P1', name: 'Greymarch', kind: 'deity' }, // no cells
        candidates,
        busy: false,
      },
    })
    expect(wrapper.text()).toContain('No outgoing relations')
  })

  it('a missing registry renders the loading line, never a broken form', () => {
    const wrapper = mount(EdgeComposer, {
      props: {
        kinds: null,
        srcEntity: { id: 'E1', name: 'Mira', kind: 'character' },
        candidates,
        busy: false,
      },
    })
    expect(wrapper.text()).toContain('Loading the relation vocabulary')
  })
})
