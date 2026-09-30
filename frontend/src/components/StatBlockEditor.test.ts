// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import StatBlockEditor from './StatBlockEditor.vue'

function emittedBlock(wrapper: ReturnType<typeof mount>) {
  return wrapper.emitted('update:modelValue')!.at(-1)![0] as Record<string, unknown>
}

describe('StatBlockEditor round trips', () => {
  it('preserves unedited mechanics, nested metadata and the server power stamp across edits', async () => {
    const original = {
      identity: { role: 'NPC', level: 4, size: 'Medium' },
      combat: { ac: 12, hp: 30, hit_dice: '4d8', speed: { walk: 30 } },
      attributes: { str: 14, custom: 7 },
      saves: { str: 4 }, initiative: 2, passive_perception: 13, proficiency_bonus: 2,
      spellcasting: { dc: 14, slots: [4, 2] }, features: ['Alert'], resources: { ki: 4 },
      power: { dpr: 6, band: [5, 8], verdict: 'ok', version: 1 },
      custom: { notes: ['keep'] },
      actions: [{ name: 'Claw', recharge: '5–6', damage: [{ dice: '1d6', bonus: -2, type: 'slashing', custom: true }] }],
      skills: [{ name: 'Stealth', bonus: 3, expertise: true }],
      traits: [{ name: 'Brave', description: 'Fearless', source: 'ancestry' }],
    }
    const snapshot = JSON.stringify(original)
    const wrapper = mount(StatBlockEditor, { props: { modelValue: original } })
    await wrapper.get('[aria-label="combat.ac"]').setValue(15)
    let block = emittedBlock(wrapper)
    for (const key of ['saves', 'initiative', 'passive_perception', 'proficiency_bonus', 'spellcasting', 'features', 'resources', 'power', 'custom']) {
      expect(block[key]).toEqual(original[key as keyof typeof original])
    }
    expect(block.identity).toEqual(original.identity)
    expect(block.attributes).toEqual(original.attributes)
    expect(block.combat).toEqual({ ...original.combat, ac: 15 })
    expect(block.actions).toMatchObject([{ recharge: '5–6', damage: [{ bonus: -2, custom: true }] }])
    expect(block.skills).toEqual(original.skills)
    expect(block.traits).toEqual(original.traits)
    await wrapper.setProps({ modelValue: block })
    await wrapper.get('[aria-label="combat.hit_dice"]').setValue('')
    block = emittedBlock(wrapper)
    expect(block.combat).toEqual({ ac: 15, hp: 30, speed: { walk: 30 } })
    expect(block.power).toEqual(original.power)
    expect(JSON.stringify(original)).toBe(snapshot)
  })

  it.each([
    { dice: '1d6', bonus: -2 },
    { dice: '1d6-2' },
    { dice: '1d6 + 2' },
  ])('keeps the signed damage modifier after an unrelated edit: %j', async (part) => {
    const wrapper = mount(StatBlockEditor, { props: { modelValue: {
      combat: { ac: 12 }, actions: [{ name: 'Claw', damage: [{ ...part, type: 'slashing' }] }],
    } } })
    const bonus = part.dice.includes('+') ? 2 : -2
    expect((wrapper.get('input[placeholder="mod"]').element as HTMLInputElement).value).toBe(String(bonus))
    await wrapper.get('[aria-label="combat.ac"]').setValue(15)
    expect(emittedBlock(wrapper).actions).toEqual([{ name: 'Claw', damage: [{
      ...part, type: 'slashing',
    }] }])
  })

  it('preserves fractional averages and original damage representations on repeated unrelated edits', async () => {
    const damage = [
      { dice: '1d6', count: 1, sides: 6, bonus: 0, average: 3.5, type: 'slashing' },
      { dice: '1d6 - 2', average: 1.5, type: 'piercing', custom: 'legacy' },
      { count: 1, sides: 8, bonus: -1, average: 3.5, type: 'cold' },
      { dice: '2d6', bonus: 0, average: 7.25, type: 'fire' },
    ]
    const wrapper = mount(StatBlockEditor, { props: { modelValue: {
      combat: { ac: 12, hp: 30 }, actions: [{ name: 'Claw', damage }],
    } } })
    await wrapper.get('[aria-label="combat.ac"]').setValue(15)
    expect(emittedBlock(wrapper).actions).toEqual([{ name: 'Claw', damage }])
    await wrapper.setProps({ modelValue: emittedBlock(wrapper) })
    await wrapper.get('[aria-label="combat.hp"]').setValue(35)
    expect(emittedBlock(wrapper).actions).toEqual([{ name: 'Claw', damage }])
  })

  it('recomputes edited damage with fractional precision and preserves other parts', async () => {
    const untouched = { dice: '1d6-2', average: 1.5, type: 'cold' }
    const wrapper = mount(StatBlockEditor, { props: { modelValue: {
      actions: [{ name: 'Claw', damage: [
        { dice: '1d6', count: 1, sides: 6, bonus: 0, average: 3.5, type: 'slashing', custom: true },
        untouched,
      ] }],
    } } })
    await wrapper.findAll('input[placeholder="mod"]')[0]!.setValue('-1')
    const expected = [{ name: 'Claw', damage: [
      { dice: '1d6', count: 1, sides: 6, bonus: -1, average: 2.5, type: 'slashing', custom: true },
      untouched,
    ] }]
    expect(emittedBlock(wrapper).actions).toEqual(expected)
    await wrapper.setProps({ modelValue: emittedBlock(wrapper) })
    await wrapper.get('[aria-label="combat.ac"]').setValue(15)
    expect(emittedBlock(wrapper).actions).toEqual(expected)
  })

  it('keeps parent-owned identity when hidden and removes explicitly deleted action rows', async () => {
    const wrapper = mount(StatBlockEditor, { props: { hideIdentity: true, modelValue: {
      identity: { role: 'Monster', cr: 3 }, actions: [{ name: 'Claw' }], resources: { rage: 2 },
    } } })
    await wrapper.get('.action-block button').trigger('click')
    expect(emittedBlock(wrapper)).toEqual({ identity: { role: 'Monster', cr: 3 }, resources: { rage: 2 } })
  })
})
