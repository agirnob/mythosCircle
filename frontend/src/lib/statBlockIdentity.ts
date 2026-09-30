/** Keep the stat-block identity synchronized with the record-level fields. */
export function statBlockWithRecordIdentity(
  block: Record<string, unknown> | null,
  recordIdentity: Record<string, string>,
): Record<string, unknown> | null {
  if (block === null) return null

  const result = { ...block }
  const previous = result['identity']
  const identity: Record<string, unknown> =
    typeof previous === 'object' && previous !== null && !Array.isArray(previous)
      ? { ...(previous as Record<string, unknown>) }
      : {}

  for (const [recordKey, blockKey] of [
    ['role', 'role'],
    ['race_type', 'race'],
    ['class_profession', 'class'],
    ['alignment', 'alignment'],
  ]) {
    const value = recordIdentity[recordKey] ?? ''
    if (value.trim()) identity[blockKey] = value.trim()
    else delete identity[blockKey]
  }

  delete identity['level']
  delete identity['cr']
  const power = (recordIdentity['level_cr'] ?? '').trim()
  const crMatch = /^cr\s+(.+)$/i.exec(power)
  const levelMatch = /^level\s+(\d+)$/i.exec(power)
  if (crMatch) {
    const cr = crMatch[1]!.trim()
    identity['cr'] = /^\d+$/.test(cr) ? Number(cr) : cr
  } else if (levelMatch) {
    identity['level'] = Number(levelMatch[1])
  }

  if (Object.keys(identity).length > 0) result['identity'] = identity
  else delete result['identity']
  return result
}
