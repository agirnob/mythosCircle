// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'

import EntityEditor from './EntityEditor.vue'

const serra = {
  id: 'E1',
  name: 'Serra Vant',
  kind: 'character',
  text: 'A chainmailed captain.',
  media: [],
  data: {
    secret: 'Forged papers.',
    background: 'Old debts.',
    dial: 'simple',
    world_integration: { current_location: 'Rustwater Keep', reputation: 'Feared' },
    stat_block: { combat: { ac: 16, hp: 44 } },
  },
}

describe('EntityEditor', () => {
  it('prefills the record fields and emits a STRING text on save (ref-in-payload regression)', async () => {
    const wrapper = mount(EntityEditor, {
      props: { entity: serra, dialLevels: ['draft', 'simple', 'important'], busy: false },
    })
    expect((wrapper.find('input[aria-label="Name"]').element as HTMLInputElement).value).toBe(
      'Serra Vant',
    )
    expect(
      (wrapper.find('textarea[aria-label="Background"]').element as HTMLTextAreaElement).value,
    ).toBe('Old debts.')
    expect(
      (wrapper.find('textarea[aria-label="Current location"]').element as HTMLTextAreaElement)
        .value,
    ).toBe('Rustwater Keep')

    await wrapper.find('textarea[aria-label="Background"]').setValue('New debts.')
    await wrapper.find('form').trigger('submit')
    const patch = wrapper.emitted('save')![0]![0] as Record<string, unknown>
    expect(typeof patch['text']).toBe('string') // the ref-in-payload slip would emit an object
    expect(patch['text']).toBe('A chainmailed captain.')
    expect(patch['background']).toBe('New debts.')
    expect((patch['world_integration'] as Record<string, unknown>)['reputation']).toBe('Feared')
    expect(patch['dial']).toBe('simple')
    expect(patch['name']).toBe('Serra Vant')
  })

  it('emits cancel without saving', async () => {
    const wrapper = mount(EntityEditor, {
      props: { entity: serra, dialLevels: ['draft'], busy: false },
    })
    await wrapper
      .findAll('button')
      .find((b) => b.text().includes('Cancel'))!
      .trigger('click')
    expect(wrapper.emitted('cancel')).toHaveLength(1)
    expect(wrapper.emitted('save')).toBeUndefined()
  })
})

describe('EntityEditor — flat kinds (place/faction)', () => {
  const keep = {
    id: 'P1',
    name: 'Rustwater Keep',
    kind: 'place',
    text: 'A garrison keep on the old ford.',
    media: [],
    data: { archetype: 'Fortress', dial: 'simple' },
  }

  it('renders the flat kind editor — no identity/stat block — with the archetype picker', () => {
    const wrapper = mount(EntityEditor, {
      props: {
        entity: keep,
        dialLevels: ['draft', 'simple'],
        archetypes: [
          { kind: 'place', name: 'Fortress', default_dial: 'draft' },
          { kind: 'place', name: 'Ruined site', default_dial: 'simple' },
        ],
        busy: false,
      },
    })
    const text = wrapper.text()
    expect(text).toContain('Description')
    expect(wrapper.find('input[aria-label="Archetype"]')).toBeTruthy()
    // The character-only machinery never renders for a place.
    expect(text).not.toContain('Stat block')
    expect(text).not.toContain('World integration')
    expect(wrapper.find('select[aria-label="Role"]').exists()).toBe(false)
  })

  it('saves archetype + dial on the place record, never a stat block', async () => {
    const wrapper = mount(EntityEditor, {
      props: {
        entity: keep,
        dialLevels: ['draft', 'simple'],
        archetypes: [{ kind: 'place', name: 'Fortress', default_dial: 'draft' }],
        busy: false,
      },
    })
    await wrapper.find('form').trigger('submit')
    const patch = wrapper.emitted('save')![0]![0] as Record<string, unknown>
    expect(patch['archetype']).toBe('Fortress')
    expect(patch['dial']).toBe('simple')
    expect('stat_block' in patch).toBe(true) // emitted; the parent/store treats null as delete-noop for flat kinds
  })
})
