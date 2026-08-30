/**
 * WebSocket client for job broadcasts (AD-17).
 *
 * One reconnecting socket per open campaign to `/api/ws/jobs?campaign_id=`.
 * Reconnect with bounded exponential backoff on any close; teardown via
 * the returned function stops reconnects.
 */

export type WsMessageType =
  'job_progress' | 'job_done' | 'job_failed' | 'job_cancelled' | 'queue_changed'

export interface WsMessage {
  type: WsMessageType
  job_id: string
  state: string
  queue_position?: number | null
  progress?: number | null
}

const MAX_BACKOFF_MS = 15_000
const BASE_BACKOFF_MS = 1_000

export function connectJobSocket(
  campaignId: string,
  onMessage: (message: WsMessage) => void,
): () => void {
  let closed = false
  let socket: WebSocket | null = null
  let retry = 0

  const url = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/api/ws/jobs?campaign_id=${encodeURIComponent(campaignId)}`

  const connect = () => {
    socket = new WebSocket(url)
    socket.onopen = () => {
      retry = 0
    }
    socket.onmessage = (event) => {
      try {
        onMessage(JSON.parse(event.data as string) as WsMessage)
      } catch {
        // Malformed frames are dropped — the store re-syncs via REST on
        // reconnect and on queue_changed anyway.
      }
    }
    socket.onclose = () => {
      if (!closed) {
        retry += 1
        setTimeout(connect, Math.min(BASE_BACKOFF_MS * 2 ** retry, MAX_BACKOFF_MS))
      }
    }
  }

  connect()
  return () => {
    closed = true
    socket?.close()
  }
}
