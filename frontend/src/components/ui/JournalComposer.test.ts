// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import JournalComposer from './JournalComposer.vue'
import JournalText from './JournalText.vue'
import type { JournalDraft, PlaySession } from '../../api/journal'
const session: PlaySession = {
  id: 'S',
  campaign_id: 'C',
  title: 'Play',
  play_date: '2026-10-05',
  sequence: 1,
  version: 1,
  created_at: '',
  updated_at: '',
}
const entities = [
  { id: 'E1', name: '同名', kind: 'character' },
  { id: 'E2', name: '同名', kind: 'place' },
]
const setup = (initial?: JournalDraft) =>
  mount(JournalComposer, { props: { session, entities, busy: false, error: '', initial } })
describe('Journal mention identity', () => {
  it('uses picker IDs for duplicate Unicode names and keeps unknown @text plain', async () => {
    const wrapper = setup()
    await wrapper.get('#journal-headline').setValue('😀 @')
    await wrapper.get('#journal-headline').trigger('keydown', { key: 'ArrowDown' })
    await wrapper.get('#journal-headline').trigger('keydown', { key: 'Enter' })
    const checkboxes = wrapper.findAll<HTMLInputElement>('input[type=checkbox]')
    expect(checkboxes[0]!.element.checked).toBe(false)
    expect(checkboxes[1]!.element.checked).toBe(true)
    await wrapper.get('#journal-context').setValue('Unknown @text')
    await wrapper.get('form').trigger('submit')
    const draft = wrapper.emitted('save')![0]![0] as JournalDraft
    expect(draft.headline).toBe('😀 @同名 ')
    expect(draft.references).toEqual([
      { entity_id: 'E2', label: '同名', token: '@同名', field: 'headline', start: 2, end: 5 },
    ])
    expect(draft.context).toBe('Unknown @text')
    wrapper.unmount()
  })
  it('includes saved mentions in related selection and unlinks every occurrence when unchecked', async () => {
    const wrapper = setup({
      headline: '@同名 arrived',
      context: 'Met @同名',
      references: [
        { entity_id: 'E1', label: '同名', token: '@同名', field: 'headline', start: 0, end: 3 },
        { entity_id: 'E1', label: '同名', token: '@同名', field: 'context', start: 4, end: 7 },
        { entity_id: 'E1', label: '同名', field: null, start: null, end: null },
      ],
    })
    const checkbox = wrapper.findAll<HTMLInputElement>('input[type=checkbox]')[0]!
    expect(checkbox.element.checked).toBe(true)
    await checkbox.setValue(false)
    expect(checkbox.element.checked).toBe(false)
    await wrapper.get('form').trigger('submit')
    expect(wrapper.emitted('save')![0]![0]).toEqual({
      headline: '@同名 arrived',
      context: 'Met @同名',
      references: [],
    })
    await checkbox.setValue(true)
    await wrapper.get('form').trigger('submit')
    expect((wrapper.emitted('save')![1]![0] as JournalDraft).references).toEqual([
      { entity_id: 'E1', label: '同名', field: null, start: null, end: null },
    ])
    wrapper.unmount()
  })
  it('clears the automatic related selection when the only mention is removed', async () => {
    const wrapper = setup({
      headline: 'Arrival',
      context: '@同名',
      references: [
        { entity_id: 'E2', label: '同名', token: '@同名', field: 'context', start: 0, end: 3 },
      ],
    })
    const checkbox = wrapper.findAll<HTMLInputElement>('input[type=checkbox]')[1]!
    expect(checkbox.element.checked).toBe(true)
    await wrapper.get('#journal-context').setValue('No mention')
    expect(checkbox.element.checked).toBe(false)
    wrapper.unmount()
  })
  it('shifts spans when text is inserted before mentions and unbinds edited tokens', async () => {
    const wrapper = setup({
      headline: '😀 @同名',
      context: '',
      references: [
        { entity_id: 'E1', label: '同名', token: '@同名', field: 'headline', start: 2, end: 5 },
      ],
    })
    await wrapper.get('#journal-headline').setValue('prefix 😀 @同名')
    await wrapper.get('form').trigger('submit')
    expect((wrapper.emitted('save')![0]![0] as JournalDraft).references[0]).toMatchObject({
      entity_id: 'E1',
      start: 9,
      end: 12,
    })
    await wrapper.get('#journal-headline').setValue('prefix 😀 @different')
    await wrapper.get('form').trigger('submit')
    expect((wrapper.emitted('save')![1]![0] as JournalDraft).references).toEqual([])
    wrapper.unmount()
  })
  it('suggestion Escape closes suggestions before form Escape cancels', async () => {
    const wrapper = setup()
    await wrapper.get('#journal-headline').setValue('@')
    await wrapper.get('#journal-headline').trigger('keydown', { key: 'Escape' })
    expect(wrapper.find('[role=listbox]').exists()).toBe(false)
    expect(wrapper.emitted('cancel')).toBeUndefined()
    await wrapper.get('form').trigger('keydown', { key: 'Escape' })
    expect(wrapper.emitted('cancel')).toHaveLength(1)
    wrapper.unmount()
  })
  it('renders historical labels safely and missing entities without links', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        {
          path: '/campaigns/:id/entities/:entityId',
          name: 'entity',
          component: { template: '<div />' },
        },
      ],
    })
    const wrapper = mount(JournalText, {
      props: {
        text: '😀 @同名 @unpicked <script>',
        field: 'context',
        references: [
          {
            entity_id: 'E1',
            label: 'Old label',
            token: '@同名',
            field: 'context',
            start: 2,
            end: 5,
          },
        ],
        campaignId: 'C',
        liveEntityIds: ['E1'],
      },
      global: { plugins: [router] },
    })
    expect(wrapper.get('a').text()).toBe('Old label')
    expect(wrapper.get('a').attributes('href')).toContain('/E1')
    expect(wrapper.text()).toContain('@unpicked <script>')
    expect(wrapper.find('script').exists()).toBe(false)
    await wrapper.setProps({ liveEntityIds: [] })
    expect(wrapper.find('a').exists()).toBe(false)
    expect(wrapper.text()).toContain('Old label')
    wrapper.unmount()
  })
})

it('accepts 180 Unicode codepoints without native UTF-16 truncation and keeps an oversized draft', async () => {
  const wrapper = setup()
  expect(wrapper.get('#journal-headline').attributes('maxlength')).toBeUndefined()
  await wrapper.get('#journal-headline').setValue('😀'.repeat(180))
  await wrapper.get('form').trigger('submit')
  expect((wrapper.emitted('save')![0]![0] as JournalDraft).headline).toBe('😀'.repeat(180))
  await wrapper.get('#journal-headline').setValue('😀'.repeat(181))
  await wrapper.get('form').trigger('submit')
  expect(wrapper.emitted('save')).toHaveLength(1)
  expect(wrapper.get('[role=alert]').text()).toContain('180 characters')
  expect((wrapper.get('#journal-headline').element as HTMLInputElement).value).toBe(
    '😀'.repeat(181),
  )
  wrapper.unmount()
})
