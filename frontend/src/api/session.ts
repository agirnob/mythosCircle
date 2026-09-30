/** Invalidates REST work and socket subscriptions across account boundaries. */
let generation = 0
const listeners = new Set<() => void>()
export const sessionGeneration = () => generation
export class SessionChangedError extends Error {
  constructor() {
    super('The account changed while this request was running.')
  }
}
export function invalidateSession() {
  generation += 1
  for (const listener of [...listeners]) listener()
}
export function onSessionChange(listener: () => void) {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}
