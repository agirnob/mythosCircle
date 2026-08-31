import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { connectJobSocket } from './ws'
import type { WsMessage } from './ws'

const MAX_BACKOFF_MS = 15_000

/** Minimal WebSocket double: records instances; tests drive open/close. */
class FakeWebSocket {
  static instances: FakeWebSocket[] = []
  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSING = 2
  static readonly CLOSED = 3

  url: string
  closeCalls = 0
  onopen: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  onclose: ((event: { code: number }) => void) | null = null
  onerror: (() => void) | null = null

  constructor(url: string) {
    this.url = url
    FakeWebSocket.instances.push(this)
  }

  close() {
    this.closeCalls += 1
    this.onclose?.({ code: 1000 })
  }

  /** Simulate the server accepting the connection (fires onopen). */
  open() {
    this.onopen?.()
  }

  /** Simulate the server closing with a specific code. */
  serverClose(code: number) {
    this.onclose?.({ code })
  }
}

describe('connectJobSocket', () => {
  beforeEach(() => {
    vi.stubGlobal('WebSocket', FakeWebSocket)
    vi.stubGlobal('location', { protocol: 'https:', host: 'test.local' })
  })

  afterEach(() => {
    FakeWebSocket.instances = []
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('calls onReconnect when the socket reopens after a drop', async () => {
    vi.useFakeTimers()
    const onReconnect = vi.fn()
    const disconnect = connectJobSocket('C1', vi.fn(), { onReconnect })
    expect(FakeWebSocket.instances).toHaveLength(1)

    FakeWebSocket.instances[0].serverClose(1000) // drop -> backoff timer
    expect(onReconnect).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(2_000) // first backoff: 1s * 2^1
    expect(FakeWebSocket.instances).toHaveLength(2)

    FakeWebSocket.instances[1].open() // reconnection lands
    expect(onReconnect).toHaveBeenCalledOnce()

    disconnect()
  })

  it('teardown cancels a pending reconnect', async () => {
    vi.useFakeTimers()
    const disconnect = connectJobSocket('C1', vi.fn())
    FakeWebSocket.instances[0].serverClose(1000) // schedules a reconnect
    expect(FakeWebSocket.instances).toHaveLength(1)

    disconnect()
    await vi.advanceTimersByTimeAsync(MAX_BACKOFF_MS)
    expect(FakeWebSocket.instances).toHaveLength(1) // no new socket ever
  })

  it('a 4401 close fires onAuthFailure and never reconnects', async () => {
    vi.useFakeTimers()
    const onAuthFailure = vi.fn()
    const disconnect = connectJobSocket('C1', vi.fn(), { onAuthFailure })

    FakeWebSocket.instances[0].serverClose(4401)
    expect(onAuthFailure).toHaveBeenCalledOnce()
    await vi.advanceTimersByTimeAsync(MAX_BACKOFF_MS)
    expect(FakeWebSocket.instances).toHaveLength(1) // no reconnect

    disconnect()
  })

  it('delivers parsed frames to onMessage', () => {
    const onMessage = vi.fn()
    const disconnect = connectJobSocket('C1', onMessage)
    const frame: WsMessage = { type: 'job_progress', job_id: 'J1', state: 'running', progress: 0.5 }
    FakeWebSocket.instances[0].onmessage?.({ data: JSON.stringify(frame) })
    expect(onMessage).toHaveBeenCalledWith(frame)
    disconnect()
  })
})
