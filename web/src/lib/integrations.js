// Settings → Integrations: the catalogue of what this install can connect to, and
// the one vocabulary for a connection's status.
//
// `multiple` marks the platforms that can be connected more than once — the ones the
// Connect form asks a name for. `fields` are the token inputs, each carrying the env
// name POST /api/connections keys its `tokens` map by (assistant/profiles.py
// CHANNEL_TOKEN_ENVS). Google has no field: its own connect flow owns sign-in.
// GitHub has one, written to the shared registry key (POST /api/secrets/key).

export const CATALOG = [
  {
    id: 'telegram', kind: 'channel', label: 'Telegram', multiple: true,
    blurb: 'DM the assistant from Telegram. Groups get their own pinned profile.',
    setup: 'Create a bot with @BotFather and paste the token it gives you.',
    fields: [{ env: 'TELEGRAM_BOT_TOKEN', label: 'Bot token', placeholder: '123456:AA…' }],
  },
  {
    id: 'discord', kind: 'channel', label: 'Discord', multiple: true,
    blurb: 'DM the assistant from Discord, or point a server channel at a profile.',
    setup: 'Discord Developer Portal → New Application → Bot → Reset Token.',
    fields: [{ env: 'DISCORD_BOT_TOKEN', label: 'Bot token', placeholder: 'MTIz…' }],
  },
  {
    id: 'slack', kind: 'channel', label: 'Slack', multiple: true,
    blurb: 'Slack DMs and channels. Needs both a bot token and an app token.',
    setup: 'Slack app → OAuth (xoxb-…) and Socket Mode (xapp-…).',
    fields: [
      { env: 'SLACK_BOT_TOKEN', label: 'Bot token', placeholder: 'xoxb-…' },
      { env: 'SLACK_APP_TOKEN', label: 'App token', placeholder: 'xapp-…' },
    ],
  },
  {
    id: 'google', kind: 'google', label: 'Google', multiple: false,
    blurb: 'Gmail, Calendar and Drive, signed in as you.',
    setup: 'Sign in through the Google flow — no key to paste.',
    fields: [],
  },
  {
    id: 'github', kind: 'github', label: 'GitHub', multiple: false,
    blurb: 'Skills registry — raises the download rate limit. Optional.',
    setup: 'Any fine-grained personal access token with public read.',
    fields: [{ env: 'token', label: 'Token', placeholder: 'github_pat_…' }],
  },
]

export const byId = Object.fromEntries(CATALOG.map((e) => [e.id, e]))

// Single-letter mark where a provider logo would go — the repo ships no Telegram /
// Discord / Slack SVGs.
export const MARK_TINT = {
  telegram: '#2aabee', discord: '#5865f2', slack: '#611f69',
  google: '#ea4335', github: '#8b949e',
}

// One connection's status, resolved in this order: failed to start → not started →
// nobody paired → no default profile → healthy, naming where its messages land.
// `profileName` is the default profile's display name (only read in the last case).
export function connectionStatus(c, profileName) {
  if (c.error) return { kind: 'err', text: c.error }
  if (!c.active) return { kind: 'wait', text: 'not started' }
  if (!c.paired_accounts) return { kind: 'err', text: 'nobody paired — it answers nobody' }
  if (c.default_profile == null) return { kind: 'wait', text: 'no default profile' }
  return { kind: 'ok', text: `messages go to ${profileName || c.default_profile}` }
}

export function googleStatus(g) {
  if (g == null) return { kind: 'wait', text: '…' }
  if (g.signed_in && g.libs_available === false) return { kind: 'err', text: 'needs libraries · not usable' }
  if (g.signed_in) return { kind: 'ok', text: 'connected · ' + (g.email || 'account') }
  return { kind: 'wait', text: 'not connected' }
}

export function githubStatus(keys) {
  const k = keys?.github
  return k?.set
    ? { kind: 'ok', text: 'token set · ' + (k.hint || '') }
    : { kind: 'wait', text: 'no token — using the anonymous rate limit' }
}

// "Telegram", then "Telegram 2", "Telegram 3" — what the server would pick for a
// blank name, prefilled so the Connect form shows it before it is created.
export function nextConnectionName(list, entry) {
  const n = list.filter((c) => c.platform === entry.id).length
  return n ? `${entry.label} ${n + 1}` : entry.label
}
