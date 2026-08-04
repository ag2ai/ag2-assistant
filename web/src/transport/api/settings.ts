// Per-profile settings, health, voice and the persona memory doc (app.py
// 2415-2775, 2910-2922, 3241-3252).
import { api as P, pidApi as PID } from '../../lib/profile.ts'
import { del, get, post } from '../http.ts'
import {
  FocusesSaved,
  LiveOverrideSaved,
  LlmOverrideSaved,
  McpHealth,
  McpServerSaved,
  McpServersSnapshot,
  MemoryDoc,
  Ok,
  ProfileHealth,
  ProfileSettings,
  ReplyTimeoutSaved,
  Usage,
  VoiceCatalog,
  VoiceSelected,
} from '../../schemas/index.ts'

// MCPServerRequest — the list fields also accept a raw string the server splits,
// which is what the manual add form posts (lib/mcp.ts owns the parsed shape).
export type McpServerRequest = {
  name: string
  command: string
  args?: string[] | string
  env?: Record<string, string> | string | null
  cwd?: string | null
  allowed_tools?: string[] | string
  blocked_tools?: string[] | string
  enabled?: boolean
}

export const settingsApi = {
  // Cheap subsystem health for the status dot. MCP is listed but probed on demand
  // via healthMcpServer — this route never spawns a server.
  health: () => get(P('/health'), ProfileHealth),

  // The voice catalogue + selection. A live-config id scopes it to that config's
  // provider/voice; else the profile's legacy voice-provider setting.
  voices: (configId?: string | null) =>
    get(
      P('/voice/voices' + (configId ? '?config_id=' + encodeURIComponent(configId) : '')),
      VoiceCatalog,
    ),

  settings: () => get(P('/settings'), ProfileSettings),

  // Focus areas are a per-profile persona attribute injected into the agent's context.
  setFocuses: (focuses: string[]) => post(P('/settings/focuses'), { focuses }, FocusesSaved),

  // Per-profile model Active override (ADR 0015): point THIS profile at a shared
  // install-wide config id; an empty string clears the override. Distinct from the
  // install-wide useLlmConfig / useLiveConfig.
  setLlmOverride: (configId = '') =>
    post(P('/settings/llm-override'), { config_id: configId }, LlmOverrideSaved),
  setLiveOverride: (configId = '') =>
    post(P('/settings/live-override'), { config_id: configId }, LiveOverrideSaved),

  setReplyTimeout: (replyTimeoutS: number) =>
    post(P('/settings/reply-timeout'), { reply_timeout_s: replyTimeoutS }, ReplyTimeoutSaved),

  setVoiceProvider: (provider: string) => post(P('/settings/voice_provider'), { provider }, Ok),

  addMcpServer: (server: McpServerRequest) => post(P('/settings/mcp'), server, McpServerSaved),
  deleteMcpServer: (name: string) =>
    del(P(`/settings/mcp/${encodeURIComponent(name)}`), McpServersSnapshot),
  healthMcpServer: (name: string) =>
    post(P(`/settings/mcp/${encodeURIComponent(name)}/health`), undefined, McpHealth),

  getMemory: () => get(P('/memory'), MemoryDoc),
  setMemory: (text: string) => post(P('/memory'), { text }, Ok),

  usage: () => get(P('/usage'), Usage),

  selectVoice: (voice: string, configId?: string | null) =>
    post(P('/voice/select'), { voice, config_id: configId || null }, VoiceSelected),

  // Synthesises a sample clip — audio bytes, so no JSON envelope to validate.
  previewVoice: async (voice: string, configId?: string | null): Promise<Blob> => {
    const r = await fetch(P('/voice/preview'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ voice, config_id: configId || null }),
    })
    if (!r.ok) throw new Error('preview failed (' + r.status + ')')
    return r.blob()
  },

  // Explicit-pid scoped: targets a SPECIFIC profile, not the active one. The
  // onboarding per-profile setup page iterates several freshly-created profiles.
  forProfile: (pid: string) => ({
    settings: () => get(PID(pid, '/settings'), ProfileSettings),
    setFocuses: (focuses: string[]) =>
      post(PID(pid, '/settings/focuses'), { focuses }, FocusesSaved),
  }),
}
