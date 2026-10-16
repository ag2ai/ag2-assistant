import type { Task } from '../schemas/index.ts'

// Finished runs the user has not opened, summed over every task.
export function unreadRuns(tasks: readonly Pick<Task, 'unread'>[]): number {
  return tasks.reduce((sum, task) => sum + Math.max(0, task.unread || 0), 0)
}

// A badge's text for `count`, capped so it fits the rail.
export function badgeText(count: number): string {
  return count > 99 ? '99+' : String(count)
}
