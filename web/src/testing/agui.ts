// AG-UI events as the gateway projects them, for tests that fold or replay a thread.
import { AguiEvent } from '../schemas/agui.ts'

const ev = (type: string, fields: Record<string, unknown>): AguiEvent => AguiEvent.parse({ type, ...fields })

// A whole text message: START, CONTENT, END. `stamp` is the log time an assistant END carries.
export function say(role: 'user' | 'assistant', messageId: string, text: string, seconds: number): AguiEvent[] {
  const timestamp = seconds * 1000
  return [
    ev('TEXT_MESSAGE_START', { messageId, role, timestamp }),
    ev('TEXT_MESSAGE_CONTENT', { messageId, delta: text, timestamp }),
    ev('TEXT_MESSAGE_END', { messageId, timestamp, ...(role === 'assistant' ? { rawEvent: { created_at: seconds } } : {}) }),
  ]
}

export function custom(name: string, value: Record<string, unknown>): AguiEvent {
  return ev('CUSTOM', { name, value })
}

export function toolCall(toolCallId: string, toolCallName: string, args: unknown, seconds = 0): AguiEvent[] {
  const timestamp = seconds * 1000
  return [
    ev('TOOL_CALL_START', { toolCallId, toolCallName, timestamp }),
    ev('TOOL_CALL_ARGS', { toolCallId, delta: JSON.stringify(args), timestamp }),
    ev('TOOL_CALL_END', { toolCallId, timestamp }),
  ]
}

export const activity = (content: Record<string, unknown>, messageId = 'surface-s'): AguiEvent =>
  ev('ACTIVITY_SNAPSHOT', { messageId, activityType: 'a2ui-surface', content, replace: true })

export const runStarted = (): AguiEvent => ev('RUN_STARTED', { threadId: 'c', runId: 'r' })

export const runFinished = (outcome: Record<string, unknown> = { type: 'success' }): AguiEvent =>
  ev('RUN_FINISHED', { threadId: 'c', runId: 'r', outcome })

export const runError = (message: string): AguiEvent => ev('RUN_ERROR', { message })

export const raw = ev
