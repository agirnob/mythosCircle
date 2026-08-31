/**
 * WebSocket client for job broadcasts (AD-17).
 *
 * One reconnecting socket per open campaign to `/api/ws/jobs?campaign_id=`.
 * Any drop triggers a bounded exponential-backoff reconnect; after a
 * reconnect the caller should re-sync the job list via REST (positions
 * shift, frames only carry deltas). Close code 4401 means the session is
 * no longer authorized — fatal: `onAuthFailure` fires and the socket never
 * reconnects. Teardown via the returned function stops reconnects.
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

export interface JobSocketOptions {
  /** Fired when the socket reopens after a drop — re-sync via REST. */
  onReconnect?: () => void
  /** Fired on a 4401 close — the session is invalid (fatal, no reconnect). */
  onAuthFailure?: () => void
}

const MAX_BACKOFF_MS = 15_000
const BASE_BACKOFF_MS = 1_000

export function connectJobSocket(
  campaignId: string,
  onMessage: (message: WsMessage) => void,
  options: JobSocketOptions = {},
): () => void {
  let closed = false
  let socket: WebSocket | null = null
  let retry = 0
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null

  const url = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/api/ws/jobs?campaign_id=${encodeURIComponent(campaignId)}`

  const connect = () => {
    if (closed) return
    reconnectTimer = null
    socket = new WebSocket(url)
    socket.onopen = () => {
      if (retry > 0) {
        options.onReconnect?.()
      }
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
    socket.onerror = () => {
      // Error carries no close info — close the socket so `onclose` (which
      // knows the code) drives the reconnect decision.
      socket?.close()
    }
    socket.onclose = (event) => {
      if (closed) return
      if (event.code === 4401) {
        closed = true
        options.onAuthFailure?.()
        return
      }
      retry += 1
      reconnectTimer = setTimeout(connect, Math.min(BASE_BACKOFF_MS * 2 ** retry, MAX_BACKOFF_MS))
    }
  }

  connect()
  return () => {
    closed = true
    if (reconnectTimer !== null) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    socket?.close()
  }
}
