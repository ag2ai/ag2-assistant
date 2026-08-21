// The CLI-login (ACP) options on the onboarding Connect step: Claude Code and
// Codex run the assistant's main agent on the user's own coding CLI over ACP, so
// there is no API key to paste. What has to be true instead is that the CLI's ACP
// adapter is reachable — locally on PATH, or on the host through the ACP bridge in
// Docker. These are the decisions the step makes about that, kept out of the
// component so they can be tested: is the option offered, may the flow continue on
// it, and what does the panel say when it can't.
//
// Two backend reads feed this (see gateway/app.py):
//   GET /api/coding/agents        -> {mode:'local'|'bridge', connected, agents:[{name,available}]}
//   GET /api/coding/{agent}/models -> {models, current, reason}
// In bridge mode there is deliberately NO catalog: reading it spawns the adapter
// locally and the adapter lives on the host (see coding/model_catalog.py), so the
// bridge inventory is the only availability signal there.

import { m } from '../paraglide/messages.js'

// agent name (coding/detect.py's) -> the npm package that installs its ACP adapter.
// What the coding-agents read says about one agent.
export type Availability = { loaded: boolean; mode: string; connected: boolean; available: boolean }

// GET /api/coding/agents, as much of it as these decisions read.
export type AgentsRead = {
  mode?: string
  connected?: boolean
  agents?: readonly { name: string; available?: boolean }[]
}

// GET /api/coding/{agent}/models: 'loading' while the probe is in flight,
// undefined before it is asked for at all.
export type CatalogRead =
  | { models?: unknown; current?: string; reason?: string }
  | 'loading'
  | null
  | undefined

export const ADAPTER_PKG: Record<string, string> = {
  claude: '@agentclientprotocol/claude-agent-acp',
  codex: '@agentclientprotocol/codex-acp',
}

// agent name -> the llm-config type it is saved as (llm_configs.CLI_LOGIN_TYPES).
export const CLI_TYPE: Record<string, string> = { claude: 'claude_code', codex: 'codex' }

/**
 * What the coding-agents read says about one agent.
 * `loaded` is false until the read lands, so the panel can wait instead of
 * flashing an "install it" hint at someone who has it installed.
 */
export function agentAvailability(agents: AgentsRead | null | undefined, agent: string): Availability {
  const loaded = !!agents && typeof agents === 'object'
  const mode = (loaded && agents?.mode) || 'local'
  // Only bridge mode can be disconnected; a local read always reached its answer.
  const connected = mode === 'bridge' ? agents?.connected !== false : true
  const row = loaded ? (agents?.agents || []).find((a) => a.name === agent) : null
  return { loaded, mode, connected, available: loaded && connected && !!row?.available }
}

/**
 * Whether the flow may continue on this CLI login — the Connect gate.
 *
 * Locally the bar is a catalog that actually came back: that means the adapter
 * spawned and answered over ACP, which is a far stronger signal than an
 * executable sitting on PATH. In bridge mode no catalog can exist by design, so
 * the bridge's own inventory is the signal.
 */
export function canUseCliLogin(availability: Availability | null | undefined, catalog: CatalogRead): boolean {
  if (!availability?.available) return false
  if (availability.mode === 'bridge') return true
  return !!catalog && typeof catalog === 'object' && !catalog.reason
}

/**
 * What the panel says when the option isn't (yet) usable, or when it is usable
 * but the model list isn't. Empty string = nothing to say.
 */
export function cliNote(
  agent: string,
  availability: Availability | null | undefined,
  catalog: CatalogRead,
): string {
  if (!availability?.loaded) return ''
  if (availability.mode === 'bridge' && !availability.connected) return m.cli_bridge_unreachable()
  if (!availability.available) return m.cli_not_installed({ pkg: ADAPTER_PKG[agent] })
  if (availability.mode === 'bridge') return m.cli_bridge_note()
  if (catalog === undefined || catalog === 'loading') return ''
  if (catalog?.reason === 'adapter_missing') return m.cli_adapter_missing({ pkg: ADAPTER_PKG[agent] })
  if (catalog?.reason) return m.cli_adapter_silent()
  return ''
}

/**
 * The label for "leave the model to the CLI", naming the CLI's own current
 * selection when the adapter reported one — so an empty model is a legible
 * choice rather than a blind one (same wording as Settings → Models).
 */
export function cliDefaultLabel(catalog: CatalogRead): string {
  const current = typeof catalog === 'object' ? catalog?.current : ''
  return current ? m.cli_default_named({ model: current }) : m.llm_cli_default()
}
