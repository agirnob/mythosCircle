// @vitest-environment happy-dom
import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import CharacterSheetEditor from './CharacterSheetEditor.vue'
vi.mock('../api/client', () => ({
  apiFetch: vi.fn(async () => ({ entities: [], next_cursor: null })),
}))

describe('authored sheet recovery', () => {
  it('roundtrips stable seed keys, every relation target tier and raw authored identity/prose', async () => {
    const seed = {
      key: 'authored-mara',
      name: 'Mara',
      role: 'NPC',
      record: {
        race_type: '  Half-elven traveler  ',
        class_profession: ' River pilot ',
        alignment: ' She follows her own oath. ',
        level_cr: ' seasoned captain ',
        appearance: '  A silver eye.\nA dark coat.  ',
        background: '  Raised by ferrymen.\nStill remembers every oath.  ',
        personality: ' Quiet, then loud. ',
        secret: ' Keeps a map. ',
        rumor: ' Was once a queen. ',
        party_hook: ' Find her brother. ',
        goals: ' Return home. ',
        relationships: ' Loves the ferrymen. ',
        voice_style: '  Speaks slowly. ',
        catchphrases: ' Never twice. ',
        world_integration: {
          reputation: '  Trusted by the river.  ',
          factions: ' Ferrymen ',
          current_location: ' Harbor ',
          reaction_matrix: ' Warm to sailors. ',
          on_defeat: ' Her crew takes over. ',
        },
      },
      relations: [
        { type: 'located_in', target_key: 'planned-harbor', counter: 1 },
        { type: 'member_of', target_id: 'existing-guild', counter: 2 },
        { type: 'ally_of', target_name: 'The White Sail', counter: 3 },
      ],
    }
    const wrapper = mount(CharacterSheetEditor, { props: { campaignId: 'C1', initialSeed: seed } })
    await flushPromises()
    expect(wrapper.vm.buildFigure()).toEqual(seed)
    const snapshot = wrapper.vm.snapshotDraft()
    wrapper.unmount()
    const restored = mount(CharacterSheetEditor, {
      props: { campaignId: 'C1', initialDraft: snapshot },
    })
    await flushPromises()
    expect(restored.vm.buildFigure()).toEqual(seed)
    restored.unmount()
  })

  it('preserves exact editor prose, incomplete mechanics, toggles and relations through unmount/remount', async () => {
    const initialDraft = {
      name: '  Mara  ',
      role: 'BBEG',
      section: 'mechanics',
      recordText: {
        personality: '  Quiet.\nStill water.  ',
        secret: '',
        rumor: '',
        party_hook: '',
        appearance: ' Salt-stained coat. ',
        background: '  I was here before the harbor.\n  ',
        goals: '',
        relationships: '',
        voice_style: '',
        catchphrases: '',
      },
      optionalOpen: { secret: true, voice_style: false },
      authorCombat: true,
      ac: 17,
      hp: null,
      hitDiceCount: 3,
      hitDiceSides: null,
      hitDiceMod: '+2',
      authorActions: false,
      actionDrafts: [
        {
          name: '  Lash  ',
          description: '  A rope across the deck.\nHold fast! ',
          damageCount: null,
          damageSides: 8,
          damageMod: '-1',
        },
      ],
      authorAttributes: false,
      attributes: { str: 16, dex: null, con: null, int: null, wis: null, cha: null },
      authorBoss: true,
      bossFields: {
        lair_actions: ' A wave crosses the deck. ',
        legendary_actions: '',
        immunities: '',
        vulnerabilities: '',
      },
      relationsByCampaign: {
        C1: [
          {
            type: 'located_in',
            mode: 'key',
            target_key: 'harbor-key',
            target_id: '',
            target_name: '',
            counter: '2',
          },
        ],
      },
      seedKey: 'mara-key',
    }
    const wrapper = mount(CharacterSheetEditor, { props: { campaignId: 'C1', initialDraft } })
    await flushPromises()
    const snapshot = wrapper.vm.snapshotDraft()
    expect(snapshot).toMatchObject(initialDraft)
    wrapper.unmount()
    const restored = mount(CharacterSheetEditor, {
      props: { campaignId: 'C1', initialDraft: snapshot },
    })
    await flushPromises()
    expect(restored.vm.snapshotDraft()).toEqual(snapshot)
    expect(restored.vm.buildFigure().relations).toEqual([
      { type: 'located_in', target_key: 'harbor-key', counter: 2 },
    ])
    expect(restored.vm.buildFigure().record?.background).toBe(initialDraft.recordText.background)
    restored.unmount()
  })
})
