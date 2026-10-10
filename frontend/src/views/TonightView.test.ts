// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { useCampaignsStore } from '../stores/campaigns'
import { useTonightStore } from '../stores/tonight'
import { useWorldStore } from '../stores/world'
import type { JournalEntry, PlaySession } from '../api/journal'
import TonightView from './TonightView.vue'

beforeEach(() => vi.restoreAllMocks())
afterEach(() => vi.unstubAllGlobals())
const session = (id: string, sequence = 1, playDate = '2026-10-05'): PlaySession => ({
  id,
  campaign_id: 'C1',
  title: id,
  sequence,
  play_date: playDate,
  version: 1,
  created_at: '',
  updated_at: '',
})
const story = (id: string, sessionId = 'S1'): JournalEntry => ({
  id,
  campaign_id: 'C1',
  session_id: sessionId,
  headline: id,
  context: 'Story context',
  references: [],
  position: 1024,
  version: 1,
  source_event_id: null,
  action_revision_id: null,
  corrected: false,
  created_at: '',
  updated_at: '',
})
async function setup(legacy = '') {
  const pinia = createPinia()
  setActivePinia(pinia)
  useAuthStore().account = { id: 'A', email: 'a@example.com', is_admin: false }
  const stored = new Map<string, string>()
  if (legacy) stored.set('mythoscircle:tonight:A:C1', legacy)
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => stored.get(key) ?? null,
    setItem: (key: string, value: string) => stored.set(key, value),
    removeItem: (key: string) => stored.delete(key),
  })
  const world = useWorldStore(),
    tonight = useTonightStore()
  vi.spyOn(world, 'load').mockResolvedValue()
  vi.spyOn(useCampaignsStore(), 'fetchOne').mockResolvedValue()
  vi.spyOn(tonight, 'load').mockResolvedValue()
  vi.spyOn(tonight, 'fetchKinds').mockResolvedValue()
  vi.spyOn(tonight, 'fetchJournal').mockResolvedValue(true)
  world.byCampaign.C1 = {
    world: {
      campaign: {
        id: 'C1',
        title: 'Campaign',
        theme: '',
        description: '',
        custom_lore: '',
        is_generic: false,
        created_at: '',
      },
      revision: null,
      entities: [
        { id: 'E1', name: 'Blacksmith', kind: 'character', text: '', data: {}, media: [] },
        { id: 'E2', name: 'Harbor', kind: 'place', text: '', data: {}, media: [] },
      ],
      edges: [],
    },
    loading: false,
    error: null,
    notFound: false,
    fetching: false,
    dirty: false,
  }
  const entry = tonight.ensureEntry('C1')
  entry.sessions = [session('S1'), session('S2', 2, '2026-09-01'), session('empty', 3)]
  entry.activeSessionId = 'S1'
  entry.runState = {
    session: { E1: { defeated: true, notes: 'Original reference notes' } },
    knowledge: {},
  }
  entry.revisions = []
  entry.journal[tonight.journalKey({ sessionId: 'S1' })] = {
    entries: [story('J1')],
    nextCursor: null,
  }
  entry.journal[tonight.journalKey({ sessionId: 'S2' })] = {
    entries: [story('J2', 'S2')],
    nextCursor: null,
  }
  entry.journal[tonight.journalKey()] = {
    entries: [story('J1'), story('J2', 'S2')],
    nextCursor: null,
  }
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/campaigns/:id/tonight', name: 'tonight', component: TonightView },
      { path: '/campaigns/:id', name: 'overview', component: { template: '<div />' } },
      {
        path: '/campaigns/:id/entities/:entityId',
        name: 'entity',
        component: { template: '<div />' },
      },
    ],
  })
  await router.push('/campaigns/C1/tonight')
  const wrapper = mount(TonightView, {
    attachTo: globalThis.document.body,
    global: { plugins: [pinia, router] },
  })
  await flushPromises()
  return { wrapper, router, tonight, stored, entry }
}
const button = (wrapper: Awaited<ReturnType<typeof setup>>['wrapper'], text: string) =>
  wrapper.findAll('button').find((item) => item.text() === text)!

describe('Named Tonight journal', () => {
  it('offers keyboard tabs and keeps active session independent of reading selection', async () => {
    const { wrapper, entry } = await setup()
    expect(wrapper.findAll('[role=tab]')).toHaveLength(3)
    await wrapper.get('#play-session-select').setValue('S2')
    expect(entry.activeSessionId).toBe('S1')
    await wrapper.get('#tonight-tab-session').trigger('keydown', { key: 'ArrowRight' })
    expect(wrapper.get('#tonight-tab-timeline').attributes('aria-selected')).toBe('true')
    expect(wrapper.get('#tonight-tab-session').attributes('tabindex')).toBe('-1')
    wrapper.unmount()
  })
  it('groups by play date, then sequence, and omits empty sessions', async () => {
    const { wrapper } = await setup()
    await wrapper.get('#tonight-tab-timeline').trigger('click')
    const groups = wrapper.findAll('.mc-timeline-group')
    expect(groups).toHaveLength(2)
    expect(groups[0]!.text()).toContain('S2')
    expect(groups[1]!.text()).toContain('S1')
    wrapper.unmount()
  })
  it('opens one labeled composer and Cancel makes no write or empty card', async () => {
    const { wrapper, tonight } = await setup()
    const save = vi.spyOn(tonight, 'saveJournal')
    await button(wrapper, '＋ Record event').trigger('click')
    expect(wrapper.findAll('.mc-journal-composer')).toHaveLength(1)
    expect(wrapper.get('label[for=journal-headline]').text()).toBe('Headline')
    await button(wrapper, 'Cancel').trigger('click')
    expect(wrapper.find('.mc-journal-composer').exists()).toBe(false)
    expect(save).not.toHaveBeenCalled()
    expect(wrapper.findAll('#tonight-panel-session .mc-journal-entry')).toHaveLength(1)
    wrapper.unmount()
  })
  it('edits timeline context in its original session and keeps failed drafts', async () => {
    const { wrapper, tonight } = await setup()
    const save = vi.spyOn(tonight, 'saveJournal').mockRejectedValue(new Error('Conflict'))
    await wrapper.get('#tonight-tab-timeline').trigger('click')
    await wrapper.get('#tonight-panel-timeline [aria-label="Edit event: J2"]').trigger('click')
    await wrapper.get('#journal-context').setValue('Draft for older session')
    await wrapper.get('.mc-journal-composer').trigger('submit')
    await flushPromises()
    expect(save).toHaveBeenCalledWith(
      'C1',
      'S2',
      expect.objectContaining({ context: 'Draft for older session' }),
      expect.any(String),
      expect.objectContaining({ id: 'J2' }),
      expect.any(Object),
    )
    expect((wrapper.get('#journal-context').element as HTMLTextAreaElement).value).toBe(
      'Draft for older session',
    )
    expect(wrapper.get('[role=alert]').text()).toContain('Conflict')
    wrapper.unmount()
  })
  it('closes a confirmed save even when refresh fails', async () => {
    const { wrapper, tonight } = await setup()
    vi.spyOn(tonight, 'saveJournal').mockResolvedValue(story('saved'))
    vi.mocked(tonight.fetchJournal).mockResolvedValue(false)
    await button(wrapper, '＋ Record event').trigger('click')
    await wrapper.get('#journal-headline').setValue('Saved event')
    await wrapper.get('.mc-journal-composer').trigger('submit')
    await flushPromises()
    expect(wrapper.find('.mc-journal-composer').exists()).toBe(false)
    expect(wrapper.text()).toContain('Event saved. Could not refresh')
    wrapper.unmount()
  })
  it('ignores a late save after scope changed and clears old draft', async () => {
    const { wrapper, tonight, router } = await setup()
    let resolve!: (row: JournalEntry) => void
    vi.spyOn(tonight, 'saveJournal').mockImplementation(
      () =>
        new Promise((done) => {
          resolve = done
        }),
    )
    await button(wrapper, '＋ Record event').trigger('click')
    await wrapper.get('#journal-headline').setValue('Private draft')
    await wrapper.get('.mc-journal-composer').trigger('submit')
    await router.push('/campaigns/C2/tonight')
    resolve(story('saved'))
    await flushPromises()
    expect(wrapper.find('.mc-journal-composer').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('Private draft')
    expect(wrapper.text()).not.toContain('Event saved')
    wrapper.unmount()
  })
  it('copies SQL notes into chosen session without changing original notes', async () => {
    const { wrapper, entry, tonight } = await setup()
    vi.spyOn(tonight, 'saveJournal').mockResolvedValue(story('copy'))
    await wrapper.get('#play-session-select').setValue('S2')
    await wrapper.get('.mc-reference-note button').trigger('click')
    expect((wrapper.get('#journal-context').element as HTMLTextAreaElement).value).toBe(
      'Original reference notes',
    )
    await wrapper.get('.mc-journal-composer').trigger('submit')
    await flushPromises()
    expect(tonight.saveJournal).toHaveBeenCalledWith(
      'C1',
      'S2',
      expect.objectContaining({ context: 'Original reference notes' }),
      expect.any(String),
      undefined,
      expect.any(Object),
    )
    expect(entry.runState!.session.E1!.notes).toBe('Original reference notes')
    wrapper.unmount()
  })
  it('removes a canceled stateless note editor without adding a state row', async () => {
    const { wrapper } = await setup()
    await wrapper.get('#notes-entity').setValue('E2')
    expect(wrapper.text()).toContain('Notes for Harbor')
    await button(wrapper, 'Cancel').trigger('click')
    expect(wrapper.find('textarea').exists()).toBe(false)
    expect(wrapper.get('.mc-tonight-state').text()).not.toContain('Harbor')
    wrapper.unmount()
  })
})

describe('Browser notes recovery', () => {
  it('copies browser text into composer while preserving browser source', async () => {
    const { wrapper, stored } = await setup('Browser notes')
    await wrapper.get('.mc-legacy-recovery .mc-text-button').trigger('click')
    expect((wrapper.get('#journal-context').element as HTMLTextAreaElement).value).toBe(
      'Browser notes',
    )
    expect(stored.get('mythoscircle:tonight:A:C1')).toBe('Browser notes')
    wrapper.unmount()
  })
  it('appends only after choosing an entity and clears unchanged browser copy', async () => {
    const { wrapper, tonight, stored } = await setup('Browser notes')
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ session: { E1: { notes: 'Fresh existing' } }, knowledge: {} })),
    )
    vi.spyOn(tonight, 'saveNotes').mockResolvedValue({ refreshed: true })
    expect(button(wrapper, 'Append recovered notes').attributes('disabled')).toBeDefined()
    await wrapper.get('#recovery-target').setValue('E1')
    await button(wrapper, 'Append recovered notes').trigger('click')
    await flushPromises()
    expect(tonight.saveNotes).toHaveBeenCalledWith(
      'C1',
      'E1',
      'Fresh existing\n\nBrowser notes',
      'Fresh existing',
    )
    expect(stored.has('mythoscircle:tonight:A:C1')).toBe(false)
    wrapper.unmount()
  })
  it('keeps failed recovery and never duplicates append after lost response', async () => {
    const { wrapper, tonight, stored } = await setup('Legacy note')
    let server = 'Original'
    vi.spyOn(globalThis, 'fetch').mockImplementation(
      async () =>
        new Response(JSON.stringify({ session: { E1: { notes: server } }, knowledge: {} })),
    )
    const save = vi
      .spyOn(tonight, 'saveNotes')
      .mockImplementation(async (_campaign, _entity, notes) => {
        server = notes
        throw new Error('Response lost')
      })
    vi.spyOn(tonight, 'refreshProjections').mockResolvedValue({
      refreshed: true,
      runState: { session: { E1: { notes: 'Original\n\nLegacy note' } }, knowledge: {} },
    })
    await wrapper.get('#recovery-target').setValue('E1')
    await button(wrapper, 'Append recovered notes').trigger('click')
    await flushPromises()
    expect(stored.get('mythoscircle:tonight:A:C1')).toBe('Legacy note')
    await button(wrapper, 'Append recovered notes').trigger('click')
    await flushPromises()
    expect(save).toHaveBeenCalledTimes(1)
    expect(server).toBe('Original\n\nLegacy note')
    expect(stored.has('mythoscircle:tonight:A:C1')).toBe(false)
    wrapper.unmount()
  })
  it('keeps oversized Unicode recovery copyable and rejects changed browser source', async () => {
    const { wrapper, tonight, stored } = await setup('😀'.repeat(20001))
    const save = vi.spyOn(tonight, 'saveNotes')
    await wrapper.get('#recovery-target').setValue('E2')
    await button(wrapper, 'Append recovered notes').trigger('click')
    expect(wrapper.text()).toContain('exceed 20,000')
    expect(save).not.toHaveBeenCalled()
    stored.set('mythoscircle:tonight:A:C1', 'Changed')
    await button(wrapper, 'Append recovered notes').trigger('click')
    expect(wrapper.text()).toContain('Browser notes changed or were removed')
    expect(wrapper.get('.mc-legacy-recovery pre').text()).toBe('Changed')
    wrapper.unmount()
  })
})

it('loads cached sessions immediately and pages a requested deep-link target beyond first100', async () => {
  const { wrapper, tonight, router } = await setup()
  expect(tonight.fetchJournal).toHaveBeenCalledWith('C1', { sessionId: 'S1' }, false, undefined)
  vi.mocked(tonight.fetchJournal).mockRestore()
  const rows = Array.from({ length: 101 }, (_, index) => ({
    ...story(`Deep${index}`),
    position: index + 1,
  }))
  const fetcher = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const cursor = new URL(input as string, 'https://test').searchParams.get('cursor')
    return new Response(
      JSON.stringify({
        entries: cursor ? rows.slice(100) : rows.slice(0, 100),
        next_cursor: cursor ? null : 'after100',
      }),
    )
  })
  await router.push('/campaigns/C1/tonight?session=S1&entry=Deep100')
  await flushPromises()
  expect(fetcher).toHaveBeenCalledTimes(2)
  expect(wrapper.find('#session-story-Deep100').exists()).toBe(true)
  expect(globalThis.document.activeElement).toBe(wrapper.get('#session-story-Deep100').element)
  expect(
    tonight.entry('C1').journal[tonight.journalKey({ sessionId: 'S1' })]!.entries,
  ).toHaveLength(101)
  fetcher.mockClear()
  await wrapper.get('#play-session-select').setValue('S2')
  await flushPromises()
  expect(fetcher).toHaveBeenCalledTimes(1)
  const reading = new URL(fetcher.mock.calls[0]![0] as string, 'https://test')
  expect(reading.searchParams.get('session_id')).toBe('S2')
  expect(reading.searchParams.has('cursor')).toBe(false)
  wrapper.unmount()
})

it('selects and focuses another cached session on a mounted query-only deep-link navigation', async () => {
  const { wrapper, tonight, router } = await setup()
  const mountedElement = wrapper.element
  vi.mocked(tonight.fetchJournal).mockRestore()
  const target = story('S2-linked-story', 'S2')
  const fetcher = vi
    .spyOn(globalThis, 'fetch')
    .mockResolvedValue(new Response(JSON.stringify({ entries: [target], next_cursor: null })))
  await router.push('/campaigns/C1/tonight?session=S2&entry=S2-linked-story')
  await flushPromises()
  expect(wrapper.element).toBe(mountedElement)
  expect((wrapper.get('#play-session-select').element as HTMLSelectElement).value).toBe('S2')
  expect(fetcher).toHaveBeenCalledTimes(1)
  expect(fetcher.mock.calls[0]![0]).toContain('session_id=S2')
  expect(globalThis.document.activeElement).toBe(
    wrapper.get('#session-story-S2-linked-story').element,
  )
  expect(tonight.entry('C1').activeSessionId).toBe('S1')
  await router.push('/campaigns/C1/tonight?session=unknown&entry=S2-linked-story')
  await flushPromises()
  expect((wrapper.get('#play-session-select').element as HTMLSelectElement).value).toBe('S2')
  expect(fetcher.mock.calls.every(([input]) => !String(input).includes('session_id=unknown'))).toBe(
    true,
  )
  wrapper.unmount()
})

it('keeps journal draft and captured session across tabs, reading selection and new-record clicks', async () => {
  const { wrapper, tonight } = await setup()
  const save = vi.spyOn(tonight, 'saveJournal').mockResolvedValue(story('saved'))
  await button(wrapper, '＋ Record event').trigger('click')
  await wrapper.get('#journal-headline').setValue('Keep my story')
  await wrapper.get('#journal-context').setValue('Unfinished context')
  await wrapper.get('#tonight-tab-session').trigger('keydown', { key: 'ArrowRight' })
  expect((wrapper.get('#journal-context').element as HTMLTextAreaElement).value).toBe(
    'Unfinished context',
  )
  await wrapper.get('#tonight-tab-session').trigger('click')
  await wrapper.get('#play-session-select').setValue('S2')
  await button(wrapper, '＋ Record event').trigger('click')
  expect((wrapper.get('#journal-headline').element as HTMLInputElement).value).toBe('Keep my story')
  expect(wrapper.findAll('.mc-journal-composer')).toHaveLength(1)
  await wrapper.get('.mc-journal-composer').trigger('submit')
  await flushPromises()
  expect(save).toHaveBeenCalledWith(
    'C1',
    'S1',
    expect.objectContaining({ context: 'Unfinished context' }),
    expect.any(String),
    undefined,
    expect.any(Object),
  )
  wrapper.unmount()
})

it('preserves independent entity-note drafts when the reference dropdown changes', async () => {
  const { wrapper } = await setup()
  await wrapper.get('#notes-entity').setValue('E1')
  await wrapper.get('.mc-tonight-notes textarea').setValue('Blacksmith unsaved draft')
  await wrapper.get('#notes-entity').setValue('E2')
  await wrapper.findAll('.mc-tonight-notes textarea')[1]!.setValue('Harbor unsaved draft')
  await wrapper.get('#notes-entity').setValue('E1')
  expect(
    (wrapper.findAll('.mc-tonight-notes textarea')[0]!.element as HTMLTextAreaElement).value,
  ).toBe('Blacksmith unsaved draft')
  await wrapper.get('#notes-entity').setValue('E2')
  expect(
    (wrapper.findAll('.mc-tonight-notes textarea')[1]!.element as HTMLTextAreaElement).value,
  ).toBe('Harbor unsaved draft')
  await wrapper
    .findAll('.mc-tonight-notes')[1]!
    .findAll('button')
    .find((item) => item.text() === 'Cancel')!
    .trigger('click')
  expect(wrapper.get('.mc-tonight-state').text()).not.toContain('Harbor')
  expect(wrapper.findAll('.mc-tonight-notes')).toHaveLength(1)
  wrapper.unmount()
})

it.each([
  ['insert', 'B', 'Insert event before', ['A', 'New', 'B', 'C'], 2],
  ['earlier', 'B', 'Move earlier', ['B', 'A', 'C'], 1],
  ['later', 'A', 'Move later', ['B', 'A', 'C'], 3],
] as const)(
  'dense story %s controls send correct positions through view and store',
  async (kind, id, label, expected, position) => {
    const { wrapper, tonight, entry } = await setup()
    let server = ['A', 'B', 'C'].map((name, index) => ({ ...story(name), position: index + 1 }))
    entry.journal[tonight.journalKey({ sessionId: 'S1' })] = {
      entries: [...server],
      nextCursor: null,
    }
    vi.mocked(tonight.fetchJournal).mockRestore()
    const fetcher = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = new URL(input as string, 'https://test')
      if (init?.method === 'POST' || init?.method === 'PATCH') {
        const payload = JSON.parse(init.body as string) as {
          headline: string
          context: string
          position: number
        }
        const targetId = init.method === 'POST' ? 'New' : url.pathname.split('/').at(-1)!
        server = server
          .filter((row) => row.id !== targetId)
          .map((row) =>
            row.position >= payload.position
              ? { ...row, position: row.position + 1024, version: row.version + 1 }
              : row,
          )
        const saved = { ...story(targetId), ...payload }
        server.push(saved)
        return new Response(JSON.stringify(saved))
      }
      if (url.pathname.endsWith('/journal-entries'))
        return new Response(
          JSON.stringify({
            entries: [...server].sort((a, b) => a.position - b.position),
            next_cursor: null,
          }),
        )
      if (url.pathname.endsWith('/run-state'))
        return new Response(JSON.stringify({ session: {}, knowledge: {} }))
      return new Response(JSON.stringify({ revisions: [], next_cursor: null }))
    })
    await wrapper.vm.$nextTick()
    await wrapper
      .get(`#session-story-${id}`)
      .findAll('button')
      .find((item) => item.text() === label)!
      .trigger('click')
    if (kind === 'insert') {
      await wrapper.get('#journal-headline').setValue('New')
      await wrapper.get('.mc-journal-composer').trigger('submit')
    }
    await flushPromises()
    const write = fetcher.mock.calls.find(
      (call) => call[1]?.method === (kind === 'insert' ? 'POST' : 'PATCH'),
    )!
    expect(JSON.parse(write[1]!.body as string).position).toBe(position)
    expect(
      wrapper.findAll('#tonight-panel-session .mc-journal-entry h3').map((item) => item.text()),
    ).toEqual(expected)
    wrapper.unmount()
  },
)

it('timeline editing focuses a visible unique card after save', async () => {
  const { wrapper, tonight } = await setup()
  vi.spyOn(tonight, 'saveJournal').mockResolvedValue(story('J1'))
  await wrapper.get('#tonight-tab-timeline').trigger('click')
  await wrapper.get('#timeline-story-J1 [aria-label="Edit event: J1"]').trigger('click')
  await wrapper.get('.mc-journal-composer').trigger('submit')
  await flushPromises()
  const ids = wrapper.findAll('[id]').map((item) => item.attributes('id'))
  expect(new Set(ids).size).toBe(ids.length)
  expect(wrapper.find('#session-story-J1').exists()).toBe(true)
  expect(wrapper.find('#timeline-story-J1').exists()).toBe(true)
  expect(globalThis.document.activeElement).toBe(wrapper.get('#timeline-story-J1').element)
  wrapper.unmount()
})

it('Changes copy-to-journal saves the source event into intended session and removes promotion', async () => {
  const { wrapper, tonight, entry } = await setup()
  entry.revisions = [
    {
      revision_id: 'R-source',
      created_at: '',
      events: [
        {
          event_id: 'EV-source',
          revision_id: 'R-source',
          created_at: '',
          actor: 'dm',
          action: 'edited',
          kind: 'session',
          target_names: ['Blacksmith'],
          details: ['Marked defeated'],
          source_event_id: 'EV-source',
          entry_id: null,
        },
      ],
    },
  ]
  await wrapper.get('#play-session-select').setValue('S2')
  let posted: Record<string, unknown> | undefined
  let saved = false
  vi.mocked(tonight.fetchJournal).mockRestore()
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = new URL(input as string, 'https://test')
    if (init?.method === 'POST') {
      posted = JSON.parse(init.body as string) as Record<string, unknown>
      saved = true
      return new Response(
        JSON.stringify({
          ...story('promoted', 'S2'),
          source_event_id: 'EV-source',
          action_entity_id: 'E1',
          references: [{ entity_id: 'E1', label: 'Blacksmith' }],
        }),
      )
    }
    if (url.pathname.endsWith('/run-state'))
      return new Response(JSON.stringify({ session: {}, knowledge: {} }))
    if (url.pathname.endsWith('/revisions'))
      return new Response(
        JSON.stringify({
          revisions: entry.revisions!.map((revision) => ({
            ...revision,
            events: revision.events.map((event) => ({
              ...event,
              entry_id: saved ? 'promoted' : null,
            })),
          })),
          next_cursor: null,
        }),
      )
    return new Response(
      JSON.stringify({
        entries: saved
          ? [
              {
                ...story('promoted', 'S2'),
                source_event_id: 'EV-source',
                action_entity_id: 'E1',
                references: [{ entity_id: 'E1', label: 'Blacksmith' }],
              },
            ]
          : [],
        next_cursor: null,
      }),
    )
  })
  await wrapper.get('#tonight-tab-changes').trigger('click')
  await wrapper.get('.mc-feed-promote').trigger('click')
  await wrapper.get('.mc-journal-composer').trigger('submit')
  await flushPromises()
  expect(posted).toMatchObject({
    source_event_id: 'EV-source',
    session_id: 'S2',
    headline: 'Blacksmith — Marked defeated',
  })
  await wrapper.get('#tonight-tab-changes').trigger('click')
  expect(wrapper.find('.mc-feed-promote').exists()).toBe(false)
  expect(wrapper.get('#session-story-promoted a').text()).toBe('Blacksmith')
  expect(wrapper.get('#session-story-promoted a').attributes('href')).toContain('/entities/E1')
  wrapper.unmount()
})

it('offers independent session, journal, and older-Changes retry controls', async () => {
  const { wrapper, tonight, entry } = await setup()
  entry.sessionsError = 'Sessions offline'
  entry.journalError = 'Journal offline'
  entry.changesError = 'History offline'
  const sessions = vi.spyOn(tonight, 'fetchSessions').mockResolvedValue()
  const changes = vi.spyOn(tonight, 'moreChanges').mockResolvedValue()
  vi.mocked(tonight.fetchJournal).mockClear()
  await wrapper.vm.$nextTick()
  await button(wrapper, 'Retry journal').trigger('click')
  expect(tonight.fetchJournal).toHaveBeenCalledWith('C1', { sessionId: 'S1' })
  expect(sessions).not.toHaveBeenCalled()
  await button(wrapper, 'Retry play sessions').trigger('click')
  expect(sessions).toHaveBeenCalledWith('C1')
  await wrapper.get('#tonight-tab-changes').trigger('click')
  await button(wrapper, 'Retry older changes').trigger('click')
  expect(changes).toHaveBeenCalledWith('C1')
  wrapper.unmount()
})
