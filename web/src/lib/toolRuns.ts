import type { ToolRun } from '../schemas/events.ts'

type RunGroup = { run: ToolRun; count: number; firstAt?: number; lastAt?: number }

// Group identical details in first-use order, retaining the complete time range.
export function groupToolRuns(runs: ToolRun[]): RunGroup[] {
  const groups = new Map<string, RunGroup>()
  for (const run of runs) {
    const group = groups.get(run.detail)
    if (!group) {
      groups.set(run.detail, { run, count: 1, firstAt: run.at, lastAt: run.at })
      continue
    }
    group.count++
    if (run.at !== undefined) {
      group.firstAt = Math.min(group.firstAt ?? run.at, run.at)
      group.lastAt = Math.max(group.lastAt ?? run.at, run.at)
    }
  }
  return [...groups.values()]
}
