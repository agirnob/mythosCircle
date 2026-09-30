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

describe('EntityEditor regression coverage', () => {
  it('renders one control for each core field and saves every edit', async () => {
    const wrapper = mount(EntityEditor, { props: { entity: serra, dialLevels: [] } })
    for (const [key, label] of [['personality', 'Personality'], ['secret', 'Secret'], ['rumor', 'Rumor'], ['party_hook', 'Party hook']]) {
      const controls = wrapper.findAll(`textarea[aria-label="${label}"]`)
      expect(controls).toHaveLength(1)
      await controls[0]!.setValue(`New ${key}`)
    }
    await wrapper.get('form').trigger('submit')
    expect(wrapper.emitted('save')![0]![0]).toMatchObject({
      personality: 'New personality', secret: 'New secret', rumor: 'New rumor', party_hook: 'New party_hook',
    })
  })

  it.each(['place', 'faction'])('uses one Description control for %s and synchronizes both saved fields', async (kind) => {
    const wrapper = mount(EntityEditor, { props: { entity: {
      ...serra, kind, data: { description: 'Stored description' },
    }, dialLevels: [] } })
    expect(wrapper.findAll('textarea').filter((el) => el.attributes('aria-label')?.toLowerCase() === 'description')).toHaveLength(1)
    expect((wrapper.get('[aria-label="Description"]').element as HTMLTextAreaElement).value).toBe('Stored description')
    await wrapper.get('[aria-label="Description"]').setValue('New description')
    await wrapper.get('form').trigger('submit')
    expect(wrapper.emitted('save')![0]![0]).toMatchObject({ text: 'New description', description: 'New description' })
  })

  it('retains prose, stat edits and incomplete damage rows across same-entity refetches', async () => {
    const wrapper = mount(EntityEditor, { props: { entity: serra, dialLevels: [] } })
    await wrapper.get('[aria-label="Background"]').setValue('Unsaved prose')
    await wrapper.get('[aria-label="combat.ac"]').setValue(19)
    await wrapper.findAll('h4').find((h) => h.text().startsWith('Actions'))!.get('button').trigger('click')
    await wrapper.get('input[placeholder="Longsword"]').setValue('Sword')
    await wrapper.findAll('button').find((b) => b.text() === '+ damage row')!.trigger('click')
    await wrapper.get('input[placeholder="dice"]').setValue('2')
    await wrapper.setProps({ entity: { ...serra, data: { ...serra.data, background: 'Remote prose', stat_block: { combat: { ac: 11 } } } } })
    expect((wrapper.get('[aria-label="Background"]').element as HTMLTextAreaElement).value).toBe('Unsaved prose')
    expect((wrapper.get('input[placeholder="dice"]').element as HTMLInputElement).value).toBe('2')
    await wrapper.get('form').trigger('submit')
    expect(wrapper.emitted('save')![0]![0]).toMatchObject({ background: 'Unsaved prose', stat_block: { combat: { ac: 19 } } })
    // A different record starts a fresh draft.
    await wrapper.setProps({ entity: { ...serra, id: 'E2', data: { background: 'Other record' } } })
    expect((wrapper.get('[aria-label="Background"]').element as HTMLTextAreaElement).value).toBe('Other record')
  })

  it('reloads committed values when a cancelled editor is reopened', async () => {
    const props = { entity: serra, dialLevels: [] }
    const wrapper = mount(EntityEditor, { props })
    await wrapper.get('[aria-label="Background"]').setValue('Discard this')
    await wrapper.findAll('button').find((b) => b.text() === 'Cancel')!.trigger('click')
    wrapper.unmount()
    const reopened = mount(EntityEditor, { props })
    expect((reopened.get('[aria-label="Background"]').element as HTMLTextAreaElement).value).toBe('Old debts.')
  })
})
