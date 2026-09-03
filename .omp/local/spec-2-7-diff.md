diff --git a/frontend/package.json b/frontend/package.json
index 11132d2..56d6b2f 100644
--- a/frontend/package.json
+++ b/frontend/package.json
@@ -20,9 +20,11 @@
   "devDependencies": {
     "@eslint/js": "^10.0.1",
     "@vitejs/plugin-vue": "^6.0.8",
+    "@vue/test-utils": "^2.5.0",
     "eslint": "^10.9.1",
     "eslint-config-prettier": "^10.1.8",
     "eslint-plugin-vue": "^10.10.0",
+    "happy-dom": "^20.13.2",
     "openapi-typescript": "^7.13.0",
     "prettier": "^3.9.6",
     "typescript": "^6.0.3",
diff --git a/frontend/src/api/schema.ts b/frontend/src/api/schema.ts
index c849c8d..eb82121 100644
--- a/frontend/src/api/schema.ts
+++ b/frontend/src/api/schema.ts
@@ -234,6 +234,46 @@ export interface paths {
     patch?: never
     trace?: never
   }
+  '/api/campaigns/{campaign_id}/entities/{entity_id}': {
+    parameters: {
+      query?: never
+      header?: never
+      path?: never
+      cookie?: never
+    }
+    get?: never
+    put?: never
+    post?: never
+    /**
+     * Delete Entity
+     * @description FR4/AD-5: delete one entity through the store's commit path.
+     */
+    delete: operations['delete_entity_api_campaigns__campaign_id__entities__entity_id__delete']
+    options?: never
+    head?: never
+    patch?: never
+    trace?: never
+  }
+  '/api/campaigns/{campaign_id}/export': {
+    parameters: {
+      query?: never
+      header?: never
+      path?: never
+      cookie?: never
+    }
+    /**
+     * Export World
+     * @description The complete latest-revision world state as JSON or Obsidian Markdown.
+     */
+    get: operations['export_world_api_campaigns__campaign_id__export_get']
+    put?: never
+    post?: never
+    delete?: never
+    options?: never
+    head?: never
+    patch?: never
+    trace?: never
+  }
 }
 export type webhooks = Record<string, never>
 export interface components {
@@ -269,6 +309,21 @@ export interface components {
       /** Next Cursor */
       next_cursor: string | null
     }
+    /** CampaignMeta */
+    CampaignMeta: {
+      /** Id */
+      id: string
+      /** Title */
+      title: string
+      /** Theme */
+      theme: string
+      /** Description */
+      description: string
+      /** Custom Lore */
+      custom_lore: string
+      /** Created At */
+      created_at: string
+    }
     /** CampaignResponse */
     CampaignResponse: {
       /** Id */
@@ -297,6 +352,34 @@ export interface components {
       /** Custom Lore */
       custom_lore?: string | null
     }
+    /** EdgeExport */
+    EdgeExport: {
+      /** Id */
+      id: string
+      /** Src */
+      src: string
+      /** Dst */
+      dst: string
+      /** Type */
+      type: string
+      /** Counter */
+      counter: number
+    }
+    /** EntityExport */
+    EntityExport: {
+      /** Id */
+      id: string
+      /** Kind */
+      kind: string
+      /** Name */
+      name: string
+      /** Text */
+      text: string | null
+      /** Data */
+      data: {
+        [key: string]: unknown
+      }
+    }
     /** HTTPValidationError */
     HTTPValidationError: {
       /** Detail */
@@ -393,6 +476,13 @@ export interface components {
       /** Password */
       password: string
     }
+    /** RevisionMeta */
+    RevisionMeta: {
+      /** Id */
+      id: string
+      /** Created At */
+      created_at: string
+    }
     /** ValidationError */
     ValidationError: {
       /** Location */
@@ -406,6 +496,15 @@ export interface components {
       /** Context */
       ctx?: Record<string, never>
     }
+    /** WorldExport */
+    WorldExport: {
+      campaign: components['schemas']['CampaignMeta']
+      revision: components['schemas']['RevisionMeta'] | null
+      /** Entities */
+      entities: components['schemas']['EntityExport'][]
+      /** Edges */
+      edges: components['schemas']['EdgeExport'][]
+    }
   }
   responses: never
   parameters: never
@@ -861,4 +960,71 @@ export interface operations {
       }
     }
   }
+  delete_entity_api_campaigns__campaign_id__entities__entity_id__delete: {
+    parameters: {
+      query?: never
+      header?: never
+      path: {
+        campaign_id: string
+        entity_id: string
+      }
+      cookie?: {
+        mythoscircle_session?: string | null
+      }
+    }
+    requestBody?: never
+    responses: {
+      /** @description Successful Response */
+      204: {
+        headers: {
+          [name: string]: unknown
+        }
+        content?: never
+      }
+      /** @description Validation Error */
+      422: {
+        headers: {
+          [name: string]: unknown
+        }
+        content: {
+          'application/json': components['schemas']['HTTPValidationError']
+        }
+      }
+    }
+  }
+  export_world_api_campaigns__campaign_id__export_get: {
+    parameters: {
+      query?: {
+        format?: 'json' | 'markdown'
+      }
+      header?: never
+      path: {
+        campaign_id: string
+      }
+      cookie?: {
+        mythoscircle_session?: string | null
+      }
+    }
+    requestBody?: never
+    responses: {
+      /** @description Successful Response */
+      200: {
+        headers: {
+          [name: string]: unknown
+        }
+        content: {
+          'application/json': components['schemas']['WorldExport']
+        }
+      }
+      /** @description Validation Error */
+      422: {
+        headers: {
+          [name: string]: unknown
+        }
+        content: {
+          'application/json': components['schemas']['HTTPValidationError']
+        }
+      }
+    }
+  }
 }
diff --git a/frontend/src/router.ts b/frontend/src/router.ts
index 57b2564..b5096e3 100644
--- a/frontend/src/router.ts
+++ b/frontend/src/router.ts
@@ -19,6 +19,12 @@ const router = createRouter({
       component: () => import('./views/BuildInView.vue'),
       meta: { requiresAuth: true },
     },
+    {
+      path: '/campaigns/:id/world',
+      name: 'world',
+      component: () => import('./views/WorldView.vue'),
+      meta: { requiresAuth: true },
+    },
   ],
 })
 
diff --git a/frontend/src/views/BuildInView.vue b/frontend/src/views/BuildInView.vue
index 64b4aaa..1443cfd 100644
--- a/frontend/src/views/BuildInView.vue
+++ b/frontend/src/views/BuildInView.vue
@@ -118,6 +118,11 @@ function stateLabel(job: Job): string {
     <div v-else class="card seed">
       <h2>{{ campaigns.current.title }}</h2>
       <p class="muted">{{ campaigns.current.description || 'No description.' }}</p>
+      <p>
+        <RouterLink :to="{ name: 'world', params: { id: campaignId } }" class="back"
+          >Open world view</RouterLink
+        >
+      </p>
       <p class="muted">
         Theme: <strong>{{ campaigns.current.theme }}</strong>
       </p>
diff --git a/frontend/src/views/CampaignsView.vue b/frontend/src/views/CampaignsView.vue
index a3cdb82..959db52 100644
--- a/frontend/src/views/CampaignsView.vue
+++ b/frontend/src/views/CampaignsView.vue
@@ -38,6 +38,9 @@ onMounted(async () => {
         <RouterLink :to="{ name: 'build-in', params: { id: campaign.id } }" class="cta">
           Open build-in
         </RouterLink>
+        <RouterLink :to="{ name: 'world', params: { id: campaign.id } }" class="cta secondary">
+          Open world
+        </RouterLink>
       </li>
     </ul>
   </section>
@@ -65,4 +68,9 @@ onMounted(async () => {
   color: #fff;
   text-decoration: none;
 }
+.cta.secondary {
+  background: transparent;
+  border: 1px solid #2c3038;
+  color: #9aa0a6;
+}
 </style>
=== NEW FILE: frontend/src/stores/world.ts ===
     1	/**
     2	 * Read-only world view store (spec-2-7).
     3	 *
     4	 * Holds the export-JSON projection (`GET /api/campaigns/{id}/export`) per
     5	 * campaign plus loading/error/not-found flags. Rendering decisions —
     6	 * grouping, counter labels, stat-block sections — live in the view and
     7	 * components; this store never writes world state.
     8	 *
     9	 * Live updates (FR1, NFR9): WS frames only signal "the world changed", so
    10	 * `handleJobMessage` re-fetches the snapshot — once for a build-in
    11	 * `job_progress` at/after the wave-1 commit (progress 0.5), once for any
    12	 * terminal build-in frame (a mid-wave-2 failure still leaves wave 1
    13	 * committed), and the view re-fetches on WS reconnect. Fetches coalesce:
    14	 * a frame landing while a fetch is in flight marks the entry dirty and
    15	 * one trailing fetch covers the delta; parallel fetches never stack.
    16	 */
    17	import { defineStore } from 'pinia'
    18	
    19	import type { components } from '../api/schema'
    20	import { ApiError, apiFetch } from '../api/client'
    21	import { useJobsStore } from './jobs'
    22	import type { WsMessage } from '../ws'
    23	
    24	type WorldExport = components['schemas']['WorldExport']
    25	
    26	/** Wave-1 commit boundary — build_in.py reports 0.5 (core) and 1.0 (complete). */
    27	const WAVE1_PROGRESS = 0.5
    28	
    29	/** Terminal frames: refetch even on failure/cancel (wave 1 may be committed). */
    30	const TERMINAL_TYPES: ReadonlySet<WsMessage['type']> = new Set([
    31	  'job_done',
    32	  'job_failed',
    33	  'job_cancelled',
    34	])
    35	
    36	export interface WorldEntry {
    37	  world: WorldExport | null
    38	  loading: boolean
    39	  error: string | null
    40	  notFound: boolean
    41	  /** A fetch is in flight — frames arriving now set `dirty` instead of stacking. */
    42	  fetching: boolean
    43	  /** A refetch is owed once the in-flight fetch settles. */
    44	  dirty: boolean
    45	}
    46	
    47	function emptyEntry(): WorldEntry {
    48	  return {
    49	    world: null,
    50	    loading: false,
    51	    error: null,
    52	    notFound: false,
    53	    fetching: false,
    54	    dirty: false,
    55	  }
    56	}
    57	
    58	export const useWorldStore = defineStore('world', {
    59	  state: () => ({
    60	    byCampaign: {} as Record<string, WorldEntry>,
    61	  }),
    62	  getters: {
    63	    entry:
    64	      (state) =>
    65	      (campaignId: string): WorldEntry =>
    66	        state.byCampaign[campaignId] ?? emptyEntry(),
    67	  },
    68	  actions: {
    69	    /** Initial mount load — same coalescing fetch as refetches. */
    70	    async load(campaignId: string) {
    71	      await this.fetchSnapshot(campaignId)
    72	    },
    73	    /** Fire-and-forget re-sync (WS frame / reconnect); coalesced. */
    74	    requestRefetch(campaignId: string) {
    75	      void this.fetchSnapshot(campaignId)
    76	    },
    77	    /**
    78	     * The single fetch path for an entry: never stacks — an overlapping
    79	     * call marks `dirty` and one trailing fetch runs after the current
    80	     * one settles, so a frame mid-fetch still lands its delta.
    81	     */
    82	    async fetchSnapshot(campaignId: string) {
    83	      if (!this.byCampaign[campaignId]) {
    84	        this.byCampaign[campaignId] = emptyEntry()
    85	      }
    86	      // Read the entry back through the reactive proxy — mutating a raw
    87	      // object stashed in state never re-triggers the view.
    88	      const entry = this.byCampaign[campaignId]
    89	      if (entry.fetching) {
    90	        entry.dirty = true
    91	        return
    92	      }
    93	      entry.fetching = true
    94	      entry.error = null
    95	      try {
    96	        const world = await apiFetch<WorldExport>(
    97	          `/api/campaigns/${encodeURIComponent(campaignId)}/export`,
    98	        )
    99	        entry.world = world
   100	        entry.notFound = false
   101	      } catch (err) {
   102	        if (err instanceof ApiError && err.status === 404) {
   103	          // Foreign or unknown campaign — the indistinguishable 404; a
   104	          // previous snapshot must not linger in the not-found view.
   105	          entry.world = null
   106	          entry.notFound = true
   107	        } else {
   108	          entry.error = err instanceof ApiError ? err.message : 'Could not load the world.'
   109	        }
   110	      } finally {
   111	        entry.fetching = false
   112	        if (entry.dirty) {
   113	          entry.dirty = false
   114	          void this.fetchSnapshot(campaignId)
   115	        }
   116	      }
   117	    },
   118	    /**
   119	     * WS dispatch for the world view. Mirrors the jobs-store pattern:
   120	     * let the jobs store absorb the frame first (it also REST-recovers an
   121	     * uncached job id), then decide by the cached job's kind — the wire
   122	     * frame itself carries no kind. Refetch on a build-in `job_progress`
   123	     * at/after 0.5 (wave 1 committed) and on any terminal build-in frame;
   124	     * sub-threshold progress, queue_changed, and non-build_in kinds are
   125	     * ignored.
   126	     */
   127	    async handleJobMessage(campaignId: string, message: WsMessage) {
   128	      const jobs = useJobsStore()
   129	      try {
   130	        await jobs.handleWsMessage(campaignId, message)
   131	      } catch {
   132	        // REST recovery failed (e.g. transient server error) — fall
   133	        // through to the cache check; the frame is simply dropped.
   134	      }
   135	      const job = jobs.byId[message.job_id]
   136	      if (!job || job.kind !== 'build_in' || job.campaign_id !== campaignId) return
   137	      const qualifies =
   138	        (message.type === 'job_progress' && (message.progress ?? 0) >= WAVE1_PROGRESS) ||
   139	        TERMINAL_TYPES.has(message.type)
   140	      if (!qualifies) return
   141	      void this.fetchSnapshot(campaignId)
   142	    },
   143	  },
   144	})
=== NEW FILE: frontend/src/stores/world.test.ts ===
     1	import { beforeEach, describe, expect, it, vi } from 'vitest'
     2	import { createPinia, setActivePinia } from 'pinia'
     3	
     4	import type { components } from '../api/schema'
     5	import { useJobsStore } from './jobs'
     6	import { useWorldStore } from './world'
     7	import type { WsMessage } from '../ws'
     8	
     9	type Job = components['schemas']['JobResponse']
    10	type WorldExport = components['schemas']['WorldExport']
    11	
    12	function job(id: string, overrides: Partial<Job> = {}): Job {
    13	  return {
    14	    id,
    15	    campaign_id: 'C1',
    16	    kind: 'build_in',
    17	    payload: { places: ['Greymarch'] },
    18	    state: 'queued',
    19	    progress: 0,
    20	    max_llm_calls: 64,
    21	    max_media_calls: 8,
    22	    error: null,
    23	    result: null,
    24	    created_at: '2026-08-30T20:00:00Z',
    25	    started_at: null,
    26	    finished_at: null,
    27	    queue_position: 1,
    28	    ...overrides,
    29	  } as Job
    30	}
    31	
    32	function wsMessage(overrides: Partial<WsMessage>): WsMessage {
    33	  return { type: 'job_progress', job_id: 'J1', state: 'running', ...overrides }
    34	}
    35	
    36	function worldExport(): WorldExport {
    37	  return {
    38	    campaign: {
    39	      id: 'C1',
    40	      title: 'Greymarch',
    41	      theme: 'frontier dread',
    42	      description: '',
    43	      custom_lore: '',
    44	      created_at: '2026-08-30T20:00:00Z',
    45	    },
    46	    revision: { id: '01JZZZZZZZZZZZZZZZZZZZZZZZ', created_at: '2026-08-30T20:05:00Z' },
    47	    entities: [],
    48	    edges: [],
    49	  }
    50	}
    51	
    52	function requestUrl(input: Request | URL | string): string {
    53	  if (typeof input === 'string') return input
    54	  if (input instanceof URL) return input.href
    55	  return input.url
    56	}
    57	
    58	function jsonResponse(payload: unknown, status = 200): Response {
    59	  return new Response(JSON.stringify(payload), {
    60	    status,
    61	    headers: { 'Content-Type': 'application/json' },
    62	  })
    63	}
    64	
    65	describe('world store', () => {
    66	  let exportCalls: string[]
    67	
    68	  /** Route mocks: export calls are counted; jobs-list calls drain empty. */
    69	  function mockFetch() {
    70	    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    71	      const url = requestUrl(input)
    72	      if (url.includes('/export')) {
    73	        exportCalls.push(url)
    74	        return jsonResponse(worldExport())
    75	      }
    76	      if (url.includes('/api/jobs')) {
    77	        return jsonResponse({ jobs: [], next_cursor: null })
    78	      }
    79	      throw new Error(`unexpected fetch: ${url}`)
    80	    })
    81	    return spy
    82	  }
    83	
    84	  /** Gate the next export fetch on a manual release, still counting it. */
    85	  function gateNextExport(): (value: Response) => void {
    86	    let release!: (value: Response) => void
    87	    const gate = new Promise<Response>((resolve) => {
    88	      release = resolve
    89	    })
    90	    vi.mocked(globalThis.fetch).mockImplementationOnce(async (input) => {
    91	      const url = requestUrl(input)
    92	      exportCalls.push(url)
    93	      return gate
    94	    })
    95	    return release
    96	  }
    97	
    98	  beforeEach(() => {
    99	    setActivePinia(createPinia())
   100	    vi.restoreAllMocks()
   101	    exportCalls = []
   102	  })
   103	
   104	  it('load stores the export snapshot', async () => {
   105	    mockFetch()
   106	    const world = useWorldStore()
   107	    await world.load('C1')
   108	    const entry = world.entry('C1')
   109	    expect(entry.world?.campaign.title).toBe('Greymarch')
   110	    expect(entry.notFound).toBe(false)
   111	    expect(entry.error).toBe(null)
   112	    expect(exportCalls).toHaveLength(1)
   113	  })
   114	
   115	  it('maps a 404 to not-found without an error', async () => {
   116	    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
   117	      jsonResponse({ code: 'not_found', message: 'Campaign not found.' }, 404),
   118	    )
   119	    const world = useWorldStore()
   120	    await world.load('C1')
   121	    const entry = world.entry('C1')
   122	    expect(entry.notFound).toBe(true)
   123	    expect(entry.world).toBe(null)
   124	    expect(entry.error).toBe(null)
   125	  })
   126	
   127	  it('refetches on qualifying build_in frames only', async () => {
   128	    mockFetch()
   129	    const jobs = useJobsStore()
   130	    jobs.upsert(job('J1'))
   131	    const world = useWorldStore()
   132	    await world.load('C1')
   133	    expect(exportCalls).toHaveLength(1)
   134	
   135	    // Sub-threshold progress (before the wave-1 commit) is ignored.
   136	    await world.handleJobMessage('C1', wsMessage({ type: 'job_progress', progress: 0.25 }))
   137	    expect(exportCalls).toHaveLength(1)
   138	
   139	    // 0.5 = wave-1 commit; 1.0 = wave-2 commit.
   140	    await world.handleJobMessage('C1', wsMessage({ type: 'job_progress', progress: 0.5 }))
   141	    expect(exportCalls).toHaveLength(2)
   142	    await world.handleJobMessage('C1', wsMessage({ type: 'job_progress', progress: 1.0 }))
   143	    expect(exportCalls).toHaveLength(3)
   144	
   145	    // Terminal frames refetch: a mid-wave-2 failure still leaves wave 1.
   146	    await world.handleJobMessage('C1', wsMessage({ type: 'job_done', state: 'succeeded' }))
   147	    expect(exportCalls).toHaveLength(4)
   148	    await world.handleJobMessage('C1', wsMessage({ type: 'job_failed', state: 'failed' }))
   149	    expect(exportCalls).toHaveLength(5)
   150	    await world.handleJobMessage('C1', wsMessage({ type: 'job_cancelled', state: 'cancelled' }))
   151	    expect(exportCalls).toHaveLength(6)
   152	
   153	    // queue_changed carries no world change.
   154	    await world.handleJobMessage('C1', wsMessage({ type: 'queue_changed', state: 'queued' }))
   155	    expect(exportCalls).toHaveLength(6)
   156	  })
   157	
   158	  it('ignores non-build_in jobs (cached and REST-recovered)', async () => {
   159	    mockFetch()
   160	    const jobs = useJobsStore()
   161	    jobs.upsert(job('J1', { kind: 'text' }))
   162	    const world = useWorldStore()
   163	    await world.load('C1')
   164	
   165	    await world.handleJobMessage('C1', wsMessage({ type: 'job_progress', progress: 1.0 }))
   166	    await world.handleJobMessage('C1', wsMessage({ type: 'job_done', state: 'succeeded' }))
   167	    expect(exportCalls).toHaveLength(1)
   168	
   169	    // A frame for an uncached id recovers the row via REST — still no
   170	    // refetch when the recovered kind is not build_in.
   171	    vi.mocked(globalThis.fetch).mockImplementationOnce(async (input) => {
   172	      const url = requestUrl(input)
   173	      expect(url).toContain('/api/jobs')
   174	      return jsonResponse({ jobs: [job('J9', { kind: 'text' })], next_cursor: null })
   175	    })
   176	    await world.handleJobMessage(
   177	      'C1',
   178	      wsMessage({ type: 'job_done', job_id: 'J9', state: 'succeeded' }),
   179	    )
   180	    expect(exportCalls).toHaveLength(1)
   181	  })
   182	
   183	  it('refetches once on reconnect', async () => {
   184	    mockFetch()
   185	    const world = useWorldStore()
   186	    await world.load('C1')
   187	    world.requestRefetch('C1')
   188	    await vi.waitFor(() => expect(exportCalls).toHaveLength(2))
   189	  })
   190	
   191	  it('coalesces a frame arriving during an in-flight fetch into one trailing refetch', async () => {
   192	    mockFetch()
   193	    const release = gateNextExport()
   194	
   195	    const jobs = useJobsStore()
   196	    jobs.upsert(job('J1'))
   197	    const world = useWorldStore()
   198	    const load = world.load('C1')
   199	
   200	    // A qualifying frame lands while the mount load is still in flight.
   201	    await world.handleJobMessage('C1', wsMessage({ type: 'job_progress', progress: 1.0 }))
   202	    expect(exportCalls).toHaveLength(1) // no parallel fetch stacked
   203	
   204	    release(jsonResponse(worldExport()))
   205	    await load
   206	
   207	    // Exactly one trailing fetch covers the frame's delta.
   208	    await vi.waitFor(() => expect(exportCalls).toHaveLength(2))
   209	    await vi.waitFor(() => expect(world.entry('C1').fetching).toBe(false))
   210	    expect(exportCalls).toHaveLength(2)
   211	  })
   212	
   213	  it('collapses repeated reconnect requests during an in-flight fetch', async () => {
   214	    mockFetch()
   215	    const release = gateNextExport()
   216	
   217	    const world = useWorldStore()
   218	    const load = world.load('C1')
   219	    world.requestRefetch('C1')
   220	    world.requestRefetch('C1')
   221	
   222	    release(jsonResponse(worldExport()))
   223	    await load
   224	    await vi.waitFor(() => expect(world.entry('C1').fetching).toBe(false))
   225	    expect(exportCalls).toHaveLength(2)
   226	  })
   227	})
=== NEW FILE: frontend/src/views/WorldView.vue ===
     1	<script setup lang="ts">
     2	import { computed, onMounted, onUnmounted } from 'vue'
     3	import { RouterLink, useRoute, useRouter } from 'vue-router'
     4	
     5	import type { components } from '../api/schema'
     6	import StatBlock from '../components/StatBlock.vue'
     7	import { useAuthStore } from '../stores/auth'
     8	import { useWorldStore } from '../stores/world'
     9	import { connectJobSocket } from '../ws'
    10	
    11	type WorldExport = components['schemas']['WorldExport']
    12	type EntityExport = components['schemas']['EntityExport']
    13	type EdgeExport = components['schemas']['EdgeExport']
    14	
    15	const route = useRoute()
    16	const router = useRouter()
    17	const campaignId = route.params.id as string
    18	
    19	const world = useWorldStore()
    20	
    21	let disconnectSocket: (() => void) | null = null
    22	
    23	onMounted(async () => {
    24	  await world.load(campaignId)
    25	  const entry = world.entry(campaignId)
    26	  // No socket for a foreign/unknown campaign (404) or a failed load — the
    27	  // WS handshake would 4401 and the view stays read-only on the snapshot.
    28	  if (entry.world === null) return
    29	  disconnectSocket = connectJobSocket(
    30	    campaignId,
    31	    (message) => {
    32	      void world.handleJobMessage(campaignId, message)
    33	    },
    34	    {
    35	      onReconnect: () => {
    36	        world.requestRefetch(campaignId)
    37	      },
    38	      onAuthFailure: () => {
    39	        const auth = useAuthStore()
    40	        auth.account = null
    41	        void router.push({ name: 'login' })
    42	      },
    43	    },
    44	  )
    45	})
    46	
    47	onUnmounted(() => {
    48	  disconnectSocket?.()
    49	})
    50	
    51	const entry = computed(() => world.entry(campaignId))
    52	const exportData = computed<WorldExport | null>(() => entry.value.world)
    53	const revision = computed(() => exportData.value?.revision ?? null)
    54	const entities = computed<EntityExport[]>(() => exportData.value?.entities ?? [])
    55	
    56	/** Entities grouped by kind, kinds in first-seen (rowid) order. */
    57	const kinds = computed(() => {
    58	  const groups = new Map<string, EntityExport[]>()
    59	  for (const entity of entities.value) {
    60	    const group = groups.get(entity.kind)
    61	    if (group) {
    62	      group.push(entity)
    63	    } else {
    64	      groups.set(entity.kind, [entity])
    65	    }
    66	  }
    67	  return [...groups.entries()]
    68	})
    69	
    70	const nameById = computed(() => {
    71	  const names = new Map<string, string>()
    72	  for (const entity of entities.value) {
    73	    names.set(entity.id, entity.name)
    74	  }
    75	  return names
    76	})
    77	
    78	/**
    79	 * Frontend mirror of 2.6's `_edge_label` (AD-23): neutral types render
    80	 * bare; debt/grudge/loyalty/ally/enemy carry the counter.
    81	 */
    82	const COUNTER_TYPES: ReadonlySet<string> = new Set([
    83	  'debt',
    84	  'grudge',
    85	  'loyalty',
    86	  'ally_of',
    87	  'enemy_of',
    88	])
    89	
    90	function edgeLabel(edge: EdgeExport): string {
    91	  return COUNTER_TYPES.has(edge.type) ? `${edge.type}(${edge.counter})` : edge.type
    92	}
    93	
    94	interface RelationLine {
    95	  edgeId: string
    96	  srcName: string
    97	  dstName: string
    98	  label: string
    99	}
   100	
   101	const relationsByEntity = computed(() => {
   102	  const lines = new Map<string, RelationLine[]>()
   103	  for (const edge of exportData.value?.edges ?? []) {
   104	    for (const endpoint of [edge.src, edge.dst]) {
   105	      const list = lines.get(endpoint)
   106	      const line: RelationLine = {
   107	        edgeId: edge.id,
   108	        srcName: nameById.value.get(edge.src) ?? '(unknown)',
   109	        dstName: nameById.value.get(edge.dst) ?? '(unknown)',
   110	        label: edgeLabel(edge),
   111	      }
   112	      if (list) {
   113	        list.push(line)
   114	      } else {
   115	        lines.set(endpoint, [line])
   116	      }
   117	    }
   118	  }
   119	  return lines
   120	})
   121	
   122	function relationsFor(entityId: string): RelationLine[] {
   123	  return relationsByEntity.value.get(entityId) ?? []
   124	}
   125	</script>
   126	
   127	<template>
   128	  <section>
   129	    <h1>{{ exportData?.campaign.title ?? 'World' }}</h1>
   130	
   131	    <div v-if="entry.notFound" class="card">
   132	      <p class="error">World not found.</p>
   133	      <p class="muted">This campaign does not exist or belongs to another DM.</p>
   134	      <RouterLink :to="{ name: 'campaigns' }" class="back">Back to your worlds</RouterLink>
   135	    </div>
   136	    <p v-else-if="entry.loading && !exportData" class="muted">Loading the world…</p>
   137	    <div v-else-if="entry.error && !exportData" class="card">
   138	      <p class="error">{{ entry.error }}</p>
   139	      <RouterLink :to="{ name: 'campaigns' }" class="back">Back to your worlds</RouterLink>
   140	    </div>
   141	    <template v-else-if="exportData">
   142	      <div class="card">
   143	        <p class="muted">
   144	          Theme: <strong>{{ exportData.campaign.theme }}</strong>
   145	        </p>
   146	        <p v-if="exportData.campaign.description" class="muted">
   147	          {{ exportData.campaign.description }}
   148	        </p>
   149	        <p v-if="exportData.campaign.custom_lore" class="lore">
   150	          {{ exportData.campaign.custom_lore }}
   151	        </p>
   152	        <p v-if="revision" class="muted small mono">Revision {{ revision.id }}</p>
   153	        <p class="muted small">Read-only view — the world updates live as build-in jobs commit.</p>
   154	      </div>
   155	
   156	      <div v-if="entities.length === 0" class="card">
   157	        <p class="muted">This world is still empty — nothing has been built yet.</p>
   158	        <RouterLink :to="{ name: 'build-in', params: { id: campaignId } }" class="cta">
   159	          Open build-in
   160	        </RouterLink>
   161	      </div>
   162	
   163	      <template v-else>
   164	        <section v-for="[kind, group] in kinds" :key="kind" class="kind-group">
   165	          <h2>
   166	            {{ kind }} <span class="muted small">({{ group.length }})</span>
   167	          </h2>
   168	          <article v-for="entity in group" :key="entity.id" class="card entity">
   169	            <h3>{{ entity.name }}</h3>
   170	            <p v-if="entity.text" class="text">{{ entity.text }}</p>
   171	            <p v-else class="muted">No description.</p>
   172	            <StatBlock
   173	              v-if="entity.data && entity.data['stat_block']"
   174	              :block="entity.data['stat_block']"
   175	            />
   176	            <div v-if="relationsFor(entity.id).length > 0" class="relations">
   177	              <h4>Relations</h4>
   178	              <p v-for="relation in relationsFor(entity.id)" :key="relation.edgeId" class="mono">
   179	                {{ relation.srcName }} --{{ relation.label }}--&gt; {{ relation.dstName }}
   180	              </p>
   181	            </div>
   182	          </article>
   183	        </section>
   184	      </template>
   185	    </template>
   186	  </section>
   187	</template>
   188	
   189	<style scoped>
   190	.kind-group h2 {
   191	  margin-top: 1.5rem;
   192	  text-transform: capitalize;
   193	}
   194	.entity h3 {
   195	  margin: 0.25rem 0;
   196	}
   197	.text {
   198	  white-space: pre-wrap;
   199	}
   200	.relations h4 {
   201	  margin: 0.5rem 0 0.25rem;
   202	  font-size: 0.85rem;
   203	  color: #9aa0a6;
   204	}
   205	.relations p {
   206	  margin: 0.1rem 0;
   207	  font-size: 0.85rem;
   208	}
   209	.mono {
   210	  font-family: ui-monospace, monospace;
   211	}
   212	.small {
   213	  font-size: 0.85rem;
   214	}
   215	.back {
   216	  display: inline-block;
   217	  margin-top: 0.5rem;
   218	  color: #2f6feb;
   219	  text-decoration: none;
   220	}
   221	.cta {
   222	  display: inline-block;
   223	  padding: 0.5rem 0.75rem;
   224	  border-radius: 6px;
   225	  background: #2f6feb;
   226	  color: #fff;
   227	  text-decoration: none;
   228	}
   229	.lore {
   230	  border-left: 3px solid #2c3038;
   231	  padding-left: 0.75rem;
   232	}
   233	</style>
=== NEW FILE: frontend/src/views/WorldView.test.ts ===
     1	// @vitest-environment happy-dom
     2	import { beforeEach, describe, expect, it, vi } from 'vitest'
     3	import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
     4	import { createPinia, setActivePinia } from 'pinia'
     5	
     6	import type { components } from '../api/schema'
     7	import { connectJobSocket } from '../ws'
     8	import WorldView from './WorldView.vue'
     9	
    10	type WorldExport = components['schemas']['WorldExport']
    11	
    12	vi.mock('vue-router', () => ({
    13	  RouterLink: { name: 'RouterLink', props: ['to'], template: '<a><slot /></a>' },
    14	  useRoute: () => ({ params: { id: 'C1' } }),
    15	  useRouter: () => ({ push: vi.fn() }),
    16	}))
    17	
    18	/** The view must never open a live socket in a mount test — asserted below. */
    19	vi.mock('../ws', () => ({ connectJobSocket: vi.fn(() => vi.fn()) }))
    20	
    21	let fetchCalls = 0
    22	
    23	function worldExport(overrides: Partial<WorldExport> = {}): WorldExport {
    24	  return {
    25	    campaign: {
    26	      id: 'C1',
    27	      title: 'Gildenholt',
    28	      theme: 'Gilded Vice',
    29	      description: 'A city of debts.',
    30	      custom_lore: '',
    31	      created_at: '2026-09-03T00:00:00Z',
    32	    },
    33	    revision: { id: '01REV1', created_at: '2026-09-03T00:00:00Z' },
    34	    entities: [],
    35	    edges: [],
    36	    ...overrides,
    37	  } as WorldExport
    38	}
    39	function jsonResponse(payload: unknown, status = 200): Response {
    40	  return new Response(JSON.stringify(payload), {
    41	    status,
    42	    headers: { 'Content-Type': 'application/json' },
    43	  })
    44	}
    45	
    46	async function mountView(fetchResult: () => Response): Promise<VueWrapper> {
    47	  vi.spyOn(globalThis, 'fetch').mockImplementation(() => {
    48	    fetchCalls += 1
    49	    return Promise.resolve(fetchResult())
    50	  })
    51	  const wrapper = mount(WorldView, { global: { plugins: [createPinia()] } })
    52	  await flushPromises()
    53	  return wrapper
    54	}
    55	
    56	const BARKEEP = {
    57	  id: '01E1',
    58	  kind: 'character',
    59	  name: 'Mira Vane',
    60	  text: 'Runs the Gilded Bar.',
    61	  data: {
    62	    stat_block: {
    63	      identity: {
    64	        role: 'NPC',
    65	        race: 'Human',
    66	        level: 5,
    67	        class: 'Rogue',
    68	        alignment: 'Chaotic Neutral',
    69	      },
    70	      attributes: { str: 10, dex: 14, con: 12, int: 13, wis: 9, cha: 16 },
    71	      combat: { ac: 16, hp: 44 },
    72	      actions: [{ name: 'Rapier', bonus: 6, description: 'Melee attack.' }],
    73	    },
    74	  },
    75	}
    76	const TAVERN = { id: '01P1', kind: 'place', name: 'The Gilded Bar', text: 'A tavern.', data: {} }
    77	const DEBT_EDGE = { id: '01ED1', src: '01E1', dst: '01P1', type: 'debt', counter: 50 }
    78	const LOCATED_EDGE = { id: '01ED2', src: '01E1', dst: '01P1', type: 'located_in', counter: 1 }
    79	
    80	describe('WorldView (I/O matrix render rows)', () => {
    81	  beforeEach(() => {
    82	    fetchCalls = 0
    83	    setActivePinia(createPinia())
    84	    vi.clearAllMocks()
    85	  })
    86	
    87	  it('HAPPY_PATH: kind groups, relations with per-type counters, and the stat block render', async () => {
    88	    const world = worldExport({ entities: [BARKEEP, TAVERN], edges: [DEBT_EDGE, LOCATED_EDGE] })
    89	    const wrapper = await mountView(() => jsonResponse(world))
    90	    const text = wrapper.text()
    91	
    92	    expect(text).toContain('Gildenholt')
    93	    expect(text).toContain('Revision 01REV1')
    94	    expect(text).toContain('character (1)') // grouped by kind
    95	    expect(text).toContain('place (1)')
    96	    // Counter semantics mirror 2.6 _edge_label: debt carries the amount,
    97	    // located_in (neutral) renders bare. Each endpoint card lists the edge
    98	    // in its own (the export's src->dst) direction, never reversed.
    99	    expect(text).toContain('Mira Vane --debt(50)--> The Gilded Bar')
   100	    expect(wrapper.findAll('.mono').filter((n) => n.text().includes('debt(50)'))).toHaveLength(2)
   101	    expect(text).not.toContain('The Gilded Bar --debt(50)--> Mira Vane')
   102	    expect(text).toContain('Mira Vane --located_in--> The Gilded Bar')
   103	    expect(text).not.toContain('located_in(')
   104	    // Stat block ready to roll initiative: identity line, scores, AC/HP/initiative.
   105	    expect(text).toContain('Stat block')
   106	    expect(text).toContain('NPC — Human · Rogue · Level 5 · Chaotic Neutral')
   107	    expect(text).toContain('AC 16 · HP 44 · Initiative +2')
   108	    expect(text).toContain('STR10 (+0)')
   109	    expect(text).toContain('Rapier — Melee attack.')
   110	    expect(fetchCalls).toBe(1)
   111	    // The socket opened for a live snapshot.
   112	    expect(connectJobSocket).toHaveBeenCalledOnce()
   113	  })
   114	
   115	  it('EMPTY_WORLD: empty state with a build-in CTA, no error', async () => {
   116	    const wrapper = await mountView(() => jsonResponse(worldExport()))
   117	    expect(wrapper.text()).toContain('still empty')
   118	    expect(wrapper.find('a.cta').exists()).toBe(true)
   119	    expect(wrapper.text()).not.toContain('error')
   120	  })
   121	
   122	  it('FOREIGN_CAMPAIGN: not-found state, indistinguishable from unknown', async () => {
   123	    const wrapper = await mountView(() =>
   124	      jsonResponse({ code: 'not_found', message: 'Not found.' }, 404),
   125	    )
   126	    expect(wrapper.text()).toContain('World not found.')
   127	    expect(wrapper.find('a.cta').exists()).toBe(false)
   128	  })
   129	
   130	  it('ODD_DATA: null text, empty data, and missing stat_block render gracefully', async () => {
   131	    const bald = { id: '01E2', kind: 'character', name: 'Vex', text: null, data: {} }
   132	    const world = worldExport({ entities: [bald], edges: [] })
   133	    const wrapper = await mountView(() => jsonResponse(world))
   134	    const text = wrapper.text()
   135	    expect(text).toContain('Vex')
   136	    expect(text).toContain('No description.')
   137	    expect(text).not.toContain('Stat block')
   138	    expect(text).not.toContain('(unknown)')
   139	  })
   140	})
=== NEW FILE: frontend/src/components/StatBlock.vue ===
     1	<script setup lang="ts">
     2	import { computed } from 'vue'
     3	
     4	/**
     5	 * Minimal 5e stat-block renderer (spec-2-7) for `data['stat_block']`.
     6	 *
     7	 * The pipeline (2.4) guarantees the committed shape — identity, six
     8	 * ability scores, AC/HP, optional skills/actions/traits/spells — but the
     9	 * view tolerates absence: anything that is not an object renders nothing,
    10	 * and missing sections/fields render no line rather than an error.
    11	 */
    12	
    13	interface StatIdentity {
    14	  role?: unknown
    15	  race?: unknown
    16	  level?: unknown
    17	  cr?: unknown
    18	  class?: unknown
    19	  alignment?: unknown
    20	}
    21	
    22	interface StatAttributes {
    23	  str?: unknown
    24	  dex?: unknown
    25	  con?: unknown
    26	  int?: unknown
    27	  wis?: unknown
    28	  cha?: unknown
    29	}
    30	
    31	interface NamedEntry {
    32	  name?: unknown
    33	  bonus?: unknown
    34	  description?: unknown
    35	}
    36	
    37	const props = defineProps<{ block: unknown }>()
    38	
    39	const isObject = (value: unknown): value is Record<string, unknown> =>
    40	  typeof value === 'object' && value !== null && !Array.isArray(value)
    41	
    42	const identity = computed<StatIdentity | null>(() => {
    43	  const raw = isObject(props.block) ? props.block['identity'] : null
    44	  return isObject(raw) ? (raw as StatIdentity) : null
    45	})
    46	
    47	const attributes = computed<StatAttributes | null>(() => {
    48	  const raw = isObject(props.block) ? props.block['attributes'] : null
    49	  return isObject(raw) ? (raw as StatAttributes) : null
    50	})
    51	
    52	const combat = computed<Record<string, unknown> | null>(() => {
    53	  const raw = isObject(props.block) ? props.block['combat'] : null
    54	  return isObject(raw) ? raw : null
    55	})
    56	
    57	const entries = (section: string): NamedEntry[] => {
    58	  const raw = isObject(props.block) ? props.block[section] : null
    59	  return Array.isArray(raw) ? raw.filter(isObject) : []
    60	}
    61	
    62	const skills = computed(() => entries('skills'))
    63	const actions = computed(() => entries('actions'))
    64	const traits = computed(() => entries('traits'))
    65	
    66	const spells = computed<string[]>(() => {
    67	  const raw = isObject(props.block) ? props.block['spells'] : null
    68	  return Array.isArray(raw) ? raw.filter((spell): spell is string => typeof spell === 'string') : []
    69	})
    70	
    71	/** "Monster" blocks carry cr instead of level. */
    72	const powerLabel = computed(() => {
    73	  if (!identity.value) return null
    74	  if (identity.value.cr !== undefined && identity.value.cr !== null)
    75	    return `CR ${String(identity.value.cr)}`
    76	  if (identity.value.level !== undefined && identity.value.level !== null)
    77	    return `Level ${String(identity.value.level)}`
    78	  return null
    79	})
    80	
    81	const identityLine = computed(() => {
    82	  const id = identity.value
    83	  if (!id) return null
    84	  const parts = [id.race, id.class, powerLabel.value, id.alignment]
    85	    .filter((part) => typeof part === 'string' && part.trim())
    86	    .map((part) => String(part))
    87	  return parts.length > 0 ? `${String(id.role)} — ${parts.join(' · ')}` : null
    88	})
    89	
    90	const ABILITY_KEYS = ['str', 'dex', 'con', 'int', 'wis', 'cha'] as const
    91	const ABILITY_LABELS: Record<(typeof ABILITY_KEYS)[number], string> = {
    92	  str: 'STR',
    93	  dex: 'DEX',
    94	  con: 'CON',
    95	  int: 'INT',
    96	  wis: 'WIS',
    97	  cha: 'CHA',
    98	}
    99	
   100	const abilityScores = computed(() => {
   101	  const attrs = attributes.value
   102	  if (!attrs) return []
   103	  return ABILITY_KEYS.map((key) => {
   104	    const value = attrs[key]
   105	    const modifier =
   106	      typeof value === 'number' && Number.isFinite(value) ? Math.floor((value - 10) / 2) : null
   107	    const modifierText = modifier === null ? '' : ` (${modifier >= 0 ? '+' : ''}${modifier})`
   108	    return {
   109	      key,
   110	      label: ABILITY_LABELS[key],
   111	      score: value === undefined || value === null ? '—' : String(value),
   112	      modifierText,
   113	    }
   114	  })
   115	})
   116	
   117	/** Initiative rides DEX — the block is "ready to roll initiative". */
   118	const initiative = computed(() => {
   119	  const dex = attributes.value?.dex
   120	  if (typeof dex !== 'number' || !Number.isFinite(dex)) return null
   121	  const modifier = Math.floor((dex - 10) / 2)
   122	  return modifier >= 0 ? `+${modifier}` : String(modifier)
   123	})
   124	
   125	const combatLine = computed(() => {
   126	  const combatValue = combat.value
   127	  if (!combatValue) return null
   128	  const parts = [
   129	    combatValue['ac'] !== undefined && combatValue['ac'] !== null
   130	      ? `AC ${String(combatValue['ac'])}`
   131	      : null,
   132	    combatValue['hp'] !== undefined && combatValue['hp'] !== null
   133	      ? `HP ${String(combatValue['hp'])}`
   134	      : null,
   135	    initiative.value !== null ? `Initiative ${initiative.value}` : null,
   136	  ].filter((part): part is string => part !== null)
   137	  return parts.length > 0 ? parts.join(' · ') : null
   138	})
   139	
   140	const skillLine = (skill: NamedEntry) =>
   141	  `${String(skill.name ?? '?')}${typeof skill.bonus === 'number' ? (skill.bonus >= 0 ? ' +' : ' ') + String(skill.bonus) : ''}`
   142	</script>
   143	
   144	<template>
   145	  <section v-if="identity || attributes || combatLine" class="stat-block">
   146	    <h4>Stat block</h4>
   147	    <p v-if="identityLine" class="identity">{{ identityLine }}</p>
   148	    <div v-if="abilityScores.length > 0" class="abilities">
   149	      <span v-for="ability in abilityScores" :key="ability.key" class="ability">
   150	        <span class="muted">{{ ability.label }}</span>
   151	        <strong>{{ ability.score }}</strong
   152	        >{{ ability.modifierText }}
   153	      </span>
   154	    </div>
   155	    <p v-if="combatLine" class="mono">{{ combatLine }}</p>
   156	    <p v-if="skills.length > 0" class="mono">{{ skills.map(skillLine).join(', ') }}</p>
   157	    <dl v-if="actions.length > 0">
   158	      <dt>Actions</dt>
   159	      <dd v-for="action in actions" :key="String(action.name)">
   160	        <strong>{{ action.name }}</strong> — {{ action.description }}
   161	      </dd>
   162	    </dl>
   163	    <dl v-if="traits.length > 0">
   164	      <dt>Traits</dt>
   165	      <dd v-for="trait in traits" :key="String(trait.name)">
   166	        <strong>{{ trait.name }}</strong> — {{ trait.description }}
   167	      </dd>
   168	    </dl>
   169	    <dl v-if="spells.length > 0">
   170	      <dt>Spells</dt>
   171	      <dd class="mono">{{ spells.join(', ') }}</dd>
   172	    </dl>
   173	  </section>
   174	</template>
   175	
   176	<style scoped>
   177	.stat-block {
   178	  border-left: 3px solid #2c3038;
   179	  padding-left: 0.75rem;
   180	  margin-top: 0.5rem;
   181	}
   182	.stat-block h4 {
   183	  margin: 0.25rem 0;
   184	  font-size: 0.85rem;
   185	  text-transform: uppercase;
   186	  letter-spacing: 0.05em;
   187	  color: #9aa0a6;
   188	}
   189	.identity {
   190	  margin: 0.25rem 0;
   191	}
   192	.ability {
   193	  display: inline-block;
   194	  margin-right: 1rem;
   195	}
   196	.mono,
   197	.ability strong {
   198	  font-family: ui-monospace, monospace;
   199	}
   200	dl {
   201	  margin: 0.25rem 0;
   202	}
   203	dt {
   204	  color: #9aa0a6;
   205	  font-size: 0.85rem;
   206	}
   207	dd {
   208	  margin: 0.1rem 0 0.1rem 0;
   209	}
   210	</style>
