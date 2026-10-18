// Fold AG-UI events into thread items. Assistant events ride CUSTOM as their {type, data}
// and fold through project.ts; everything AG-UI names natively folds here.

import { EventType } from '@ag-ui/client'
import type { BaseEvent } from '@ag-ui/client'
import { addTool, clearA2UIActionStatus, ensureSubagent, errorText, foldEvent, resultPreview, stopCompaction } from './project.ts'
import { nextItemId as nid } from './lib/ids.ts'
import type { ThreadItem } from './schemas/events.ts'

type Msg = Extract<ThreadItem, { kind: 'user' | 'agent' }>
type Fields = Record<string, any>

// What a fold carries between events: messages being built and tool calls being spoken.
export class AguiState {
  readonly open = new Set<string>()
  readonly calls = new Map<string, { name: string; args: string }>()
}

const ROOT_AGENT_NAMES = new Set(['ag2-assistant'])

function seconds(ev: Fields): number | undefined {
  return typeof ev.timestamp === 'number' ? ev.timestamp / 1000 : undefined
}

function message(items: ThreadItem[], id: string): Msg | undefined {
  return items.find((i): i is Msg => (i.kind === 'user' || i.kind === 'agent') && i.msg === id)
}

function subagent(items: ThreadItem[], taskId: string) {
  return items.find((i) => i.kind === 'subagent' && i.taskId === taskId)
}

// Whether the run is in flight after this event; undefined when it says nothing about it.
export function runningAfter(ev: BaseEvent): boolean | undefined {
  if (ev.type === EventType.RUN_STARTED) return true
  if (ev.type === EventType.RUN_FINISHED || ev.type === EventType.RUN_ERROR) return false
  return undefined
}

export function foldAgui(items: ThreadItem[], event: BaseEvent, state: AguiState): ThreadItem[] {
  const ev = event as Fields
  const at = seconds(ev)
  const before = items.length
  try {
    fold(items, ev, state)
  } catch (err) {
    console.warn(`[project] ${ev.type}`, err)
  }
  for (let i = before; i < items.length; i++) items[i].at ??= at
  return items
}

function fold(items: ThreadItem[], ev: Fields, state: AguiState): void {
  switch (ev.type) {
    case EventType.TEXT_MESSAGE_START: {
      const id: string = ev.messageId
      if (message(items, id)) break            // already drawn (history met the live stream)
      state.open.add(id)
      if (ev.role === 'user') {
        stopCompaction(items)
        items.push({ id: nid(), kind: 'user', text: '', msg: id })
      } else {
        clearA2UIActionStatus(items)
        items.push({ id: nid(), kind: 'agent', text: '', streaming: true, msg: id })
      }
      break
    }
    case EventType.TEXT_MESSAGE_CONTENT: {
      if (!state.open.has(ev.messageId)) break
      const item = message(items, ev.messageId)
      if (item) item.text += ev.delta
      break
    }
    case EventType.TEXT_MESSAGE_END: {
      if (!state.open.delete(ev.messageId)) break
      const item = message(items, ev.messageId)
      if (!item) break
      if (item.kind === 'agent') {
        item.streaming = false
        const stamp = ev.rawEvent?.created_at
        if (typeof stamp === 'number') item.at = stamp
        break
      }
      // A message fed to the running Turn was shown queued; its echo resolves that bubble.
      const queued = items.find((i): i is Msg & { kind: 'user' } =>
        i.kind === 'user' && !!i.queued && i.text === item.text)
      if (queued) {
        queued.queued = false
        queued.msg = ev.messageId
        items.splice(items.indexOf(item), 1)
      }
      break
    }
    case EventType.TOOL_CALL_START:
      state.calls.set(ev.toolCallId, { name: ev.toolCallName, args: '' })
      break
    case EventType.TOOL_CALL_ARGS: {
      const call = state.calls.get(ev.toolCallId)
      if (call) call.args += ev.delta
      break
    }
    case EventType.TOOL_CALL_END: {
      const call = state.calls.get(ev.toolCallId)
      if (!call) break
      state.calls.delete(ev.toolCallId)
      addTool(items, call.name, call.args, seconds(ev))
      break
    }
    case EventType.SUBAGENT_STARTED: {
      if (!ev.name || ROOT_AGENT_NAMES.has(ev.name)) {
        items.push({ id: nid(), kind: 'note', icon: 'zap', text: 'Task started' })
        break
      }
      const card = ensureSubagent(items, ev.subagentRunId, ev.name)
      card.status = 'running'
      card.agent = ev.name
      if (ev.description) card.objective = ev.description
      break
    }
    case EventType.SUBAGENT_FINISHED: {
      const card = subagent(items, ev.subagentRunId)
      if (!card || card.kind !== 'subagent') {
        items.push({ id: nid(), kind: 'note', icon: 'check', text: 'Task completed' })
        break
      }
      card.status = 'completed'
      card.result = resultPreview(ev.result)
      break
    }
    case EventType.SUBAGENT_ERROR: {
      const cancelled = ev.rawEvent?.status === 'cancelled'
      const card = subagent(items, ev.subagentRunId)
      if (!card || card.kind !== 'subagent') {
        items.push({ id: nid(), kind: 'note', icon: 'x', text: cancelled ? 'Task cancelled' : 'Task failed' })
        break
      }
      card.status = cancelled ? 'cancelled' : 'failed'
      card.error = cancelled ? ev.message || 'cancelled' : errorText(ev.message)
      break
    }
    case EventType.ACTIVITY_SNAPSHOT: {
      if (ev.activityType !== 'a2ui-surface') break
      const content: Fields = ev.content ?? {}
      const type = typeof content.file_path === 'string' ? 'CardInstanceReference'
        : typeof content.draft_id === 'string' ? 'EphemeralCard' : 'A2UISurface'
      foldEvent(items, { type, data: content })
      break
    }
    case EventType.CUSTOM:
      foldEvent(items, { type: ev.name, data: ev.value ?? {} })
      break
    case EventType.RUN_FINISHED: {
      for (const interrupt of ev.outcome?.interrupts ?? []) {
        foldEvent(items, {
          type: 'InquiryRaised',
          data: {
            inquiry_id: interrupt.id,
            question: interrupt.message ?? '',
            detail: interrupt.metadata?.detail ?? '',
            options: interrupt.metadata?.options ?? [],
            kind: interrupt.reason,
            expires_at: interrupt.metadata?.expires_at ?? undefined,
          },
        })
      }
      break
    }
    case EventType.RUN_ERROR:
      items.push({ id: nid(), kind: 'note', icon: 'x', text: ev.message || 'error', alert: true })
      break
  }
}
