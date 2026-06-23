// The user's intent that produced a thread item — passed as `request` context to the
// feedback learner so it generalises correctly (topic vs format vs instruction-following).
// Nearest preceding user message in the thread; on a task page, falls back to the task
// objective.
export function requestContext(items, item, taskPanel) {
  const idx = items.findIndex((i) => i.id === item.id)
  for (let j = idx - 1; j >= 0; j--) {
    if (items[j].kind === 'user') return items[j].text || ''
  }
  return (taskPanel && taskPanel.objective) || ''
}
