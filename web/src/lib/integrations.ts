// Settings → Integrations: the catalogue of what this install can connect to, and
// the one vocabulary for a connection's status.
//
// `multiple` marks the platforms that can be connected more than once — the ones the
// Connect form asks a name for. `fields` are the token inputs, each carrying the env
// name POST /api/connections keys its `tokens` map by (assistant/profiles.py
// CHANNEL_TOKEN_ENVS). Google has no field: its own connect flow owns sign-in.
// GitHub has one, written to the shared registry key (POST /api/secrets/key).
// `handles` mirrors the backend HANDLE_PLATFORMS: Slack messages carry no handle, so
// an invitation by handle could never be presented there and is refused.
import { m } from '../paraglide/messages.js'
import type { Connection, ConnectionExposure, GoogleStatus, ProviderKey } from '../schemas/index.ts'

// One token input on the Connect form. `label` is a message function, not a
// string: it is read at render time so it follows the UI language, while `env` and
// `placeholder` (a literal token shape) stay verbatim.
export type IntegrationField = { env: string; label: () => string; placeholder: string }

// How a platform is connected: a messaging `channel` with token(s) of its own,
// Google's own sign-in flow, or GitHub's single registry key.
export type IntegrationKind = 'channel' | 'google' | 'github'

export type Integration = {
  id: string
  kind: IntegrationKind
  label: string
  multiple: boolean
  // Only a channel is paired with, so only a channel says whether it carries handles.
  handles?: boolean
  // Prose, so read at render time like IntegrationField.label. `label` above is the
  // platform's own name — data, never translated.
  blurb: () => string
  setup: () => string
  fields: IntegrationField[]
}

// What the status line says and how it reads: a failure, something still to do,
// or a connection doing its job.
export type IntegrationStatus = { kind: 'ok' | 'wait' | 'err'; text: string }

export const CATALOG: Integration[] = [
  {
    id: 'telegram', kind: 'channel', label: 'Telegram', multiple: true, handles: true,
    blurb: m.cn_telegram_blurb,
    setup: m.cn_telegram_setup,
    fields: [{ env: 'TELEGRAM_BOT_TOKEN', label: m.cn_field_bot_token, placeholder: '123456:AA…' }],
  },
  {
    id: 'discord', kind: 'channel', label: 'Discord', multiple: true, handles: true,
    blurb: m.cn_discord_blurb,
    setup: m.cn_discord_setup,
    fields: [{ env: 'DISCORD_BOT_TOKEN', label: m.cn_field_bot_token, placeholder: 'MTIz…' }],
  },
  {
    id: 'slack', kind: 'channel', label: 'Slack', multiple: true, handles: false,
    blurb: m.cn_slack_blurb,
    setup: m.cn_slack_setup,
    fields: [
      { env: 'SLACK_BOT_TOKEN', label: m.cn_field_bot_token, placeholder: 'xoxb-…' },
      { env: 'SLACK_APP_TOKEN', label: m.cn_field_app_token, placeholder: 'xapp-…' },
    ],
  },
  {
    id: 'google', kind: 'google', label: 'Google', multiple: false,
    blurb: m.cn_google_blurb,
    setup: m.cn_google_setup,
    fields: [],
  },
  {
    id: 'github', kind: 'github', label: 'GitHub', multiple: false,
    blurb: m.cn_github_blurb,
    setup: m.cn_github_setup,
    fields: [{ env: 'token', label: m.integrations_token, placeholder: 'github_pat_…' }],
  },
]

export const byId: Record<string, Integration | undefined> =
  Object.fromEntries(CATALOG.map((e) => [e.id, e]))

// A platform's display label, or its raw id when this build does not know it.
export function platformLabel(id: string): string {
  return byId[id]?.label || id
}

// A surface's column heading in the Profiles table. The kinds come from
// GET /api/connections/{cid}/exposure (assistant/connections.py surfaces()): `dm` and
// `group` where the two switch independently, one `all` where they do not.
const SURFACE_LABEL: Record<string, (() => string) | Record<string, () => string>> = {
  dm: m.cn_surface_dm,
  group: m.cn_surface_groups,
  all: { discord: m.cn_surface_discord_all, slack: m.cn_surface_slack_all },
}

export function surfaceLabel(platform: string, kind: string): string {
  const label = SURFACE_LABEL[kind]
  const text = typeof label === 'function' ? label : label?.[platform]
  return text ? text() : m.cn_surface_reachable()
}

// Can this connection reach the profile at all? A profile withdrawn from every surface
// cannot be the one conversations land in, so its default radio is refused — the same
// invariant the server enforces. `exposure` is the response's {pid: {surface: bool}}.
export function reachableAnywhere(
  exposure: ConnectionExposure['exposure'] | null | undefined,
  pid: string,
): boolean {
  return Object.values(exposure?.[pid] || {}).some(Boolean)
}

// One connection's status, resolved in this order: failed to start → not started →
// nobody paired → no default profile → healthy, naming where its messages land.
// `profileName` is the default profile's display name (only read in the last case).
export function connectionStatus(c: Connection, profileName?: string): IntegrationStatus {
  // c.error is the gateway's own words — passed through verbatim (ADR 0031).
  if (c.error) return { kind: 'err', text: c.error }
  if (!c.active) return { kind: 'wait', text: m.cn_status_not_started() }
  if (!c.paired_accounts) return { kind: 'err', text: m.cn_status_nobody_paired() }
  if (c.default_profile == null) return { kind: 'wait', text: m.cn_status_no_default() }
  return { kind: 'ok', text: m.cn_status_messages_go_to({ name: profileName || c.default_profile }) }
}

export function googleStatus(g: GoogleStatus | null | undefined): IntegrationStatus {
  if (g == null) return { kind: 'wait', text: '…' }
  if (g.signed_in && g.libs_available === false) return { kind: 'err', text: m.cn_google_needs_libs() }
  if (g.signed_in) return { kind: 'ok', text: m.cn_google_connected({ account: g.email || m.cn_google_account() }) }
  return { kind: 'wait', text: m.cn_google_not_connected() }
}

export function githubStatus(
  keys: Record<string, ProviderKey> | null | undefined,
): IntegrationStatus {
  const k = keys?.github
  return k?.set
    ? { kind: 'ok', text: m.cn_github_token_set({ hint: k.hint || '' }) }
    : { kind: 'wait', text: m.cn_github_no_token() }
}

// "Telegram", then "Telegram 2", "Telegram 3" — what the server would pick for a
// blank name, prefilled so the Connect form shows it before it is created.
export function nextConnectionName(list: Connection[], entry: Integration): string {
  const n = list.filter((c) => c.platform === entry.id).length
  return n ? `${entry.label} ${n + 1}` : entry.label
}
