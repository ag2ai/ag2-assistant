import type { ThreadItem } from '../schemas/events.ts'

// The user's intent that produced a thread item — passed as `request` context to the
// feedback learner so it generalises correctly (topic vs format vs instruction-following).
// Nearest preceding user message in the thread; on a run page (no preceding user
// turn — the run started unattended off its task's prompt), falls back to the
// owning task's name.
export function requestContext(
  items: readonly ThreadItem[],
  item: Pick<ThreadItem, 'id'>,
  runInfo: { task_name?: string } | null | undefined,
): string {
  const idx = items.findIndex((i) => i.id === item.id)
  for (let j = idx - 1; j >= 0; j--) {
    const prev = items[j]
    if (prev.kind === 'user') return prev.text || ''
  }
  return (runInfo && runInfo.task_name) || ''
}
