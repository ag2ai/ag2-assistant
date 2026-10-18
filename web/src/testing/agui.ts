// AG-UI events as the gateway projects them, for tests that fold or replay a thread.
import type { BaseEvent } from '@ag-ui/client'

const ev = (type: string, fields: Record<string, unknown>): BaseEvent => ({ type, ...fields }) as BaseEvent

// A whole text message: START, CONTENT, END. `stamp` is the log time an assistant END carries.
export function say(role: 'user' | 'assistant', messageId: string, text: string, seconds: number): BaseEvent[] {
  const timestamp = seconds * 1000
  return [
    ev('TEXT_MESSAGE_START', { messageId, role, timestamp }),
    ev('TEXT_MESSAGE_CONTENT', { messageId, delta: text, timestamp }),
    ev('TEXT_MESSAGE_END', { messageId, timestamp, ...(role === 'assistant' ? { rawEvent: { created_at: seconds } } : {}) }),
  ]
}

export function custom(name: string, value: Record<string, unknown>): BaseEvent {
  return ev('CUSTOM', { name, value })
}

export function toolCall(toolCallId: string, toolCallName: string, args: unknown, seconds = 0): BaseEvent[] {
  const timestamp = seconds * 1000
  return [
    ev('TOOL_CALL_START', { toolCallId, toolCallName, timestamp }),
    ev('TOOL_CALL_ARGS', { toolCallId, delta: JSON.stringify(args), timestamp }),
    ev('TOOL_CALL_END', { toolCallId, timestamp }),
  ]
}

export const activity = (content: Record<string, unknown>, messageId = 'surface-s'): BaseEvent =>
  ev('ACTIVITY_SNAPSHOT', { messageId, activityType: 'a2ui-surface', content, replace: true })

export const runStarted = (): BaseEvent => ev('RUN_STARTED', { threadId: 'c', runId: 'r' })

export const runFinished = (outcome: Record<string, unknown> = { type: 'success' }): BaseEvent =>
  ev('RUN_FINISHED', { threadId: 'c', runId: 'r', outcome })

export const runError = (message: string): BaseEvent => ev('RUN_ERROR', { message })

export const raw = ev
