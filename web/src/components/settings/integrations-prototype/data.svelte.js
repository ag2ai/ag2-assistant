// PROTOTYPE — throwaway. Delete with the rest of integrations-prototype/.
//
// v2 model, after the first round of feedback. Three things moved:
//
//  1. A connection is an INSTANCE, not a platform. You can have two Telegram bots.
//     Everything below is keyed by instance id, not by 'telegram'.
//  2. The token is only ever typed in the CONNECT form. A connected instance has no
//     token field — a new token means a new connection, so replacing one is
//     "connect the new bot, disconnect the old one", never an invisible swap.
//  3. Per-profile exposure (today: Profile editor → Channels, a profile-major matrix
//     of surface switches) moves HERE, integration-major: each connection lists the
//     profiles reachable through it.
//
// The real backend is still one-connection-per-platform, so (1) has no API to talk
// to. Everything after the initial read is therefore IN-MEMORY AND FAKE — that's the
// point of a prototype: judge the shape before paying for the migration. Reloading
// the page throws it all away and re-seeds from the real install.
import { api } from '../../../transport/api.js'

// Telegram's DMs and groups are exposed independently (backend CHANNEL_SURFACES);
// Discord and Slack are one surface each.
export const CATALOG = [
  {
    id: 'telegram', kind: 'channel', label: 'Telegram', tag: 'Messaging', multiple: true,
    blurb: 'DM the assistant from Telegram. Groups get their own pinned profile.',
    setup: 'Create a bot with @BotFather and paste the token it gives you.',
    handles: true,
    fields: [{ key: 'token', label: 'Bot token', placeholder: '123456:AA…' }],
    surfaces: [{ id: 'dm', label: 'Direct messages' }, { id: 'group', label: 'Groups' }],
  },
  {
    id: 'discord', kind: 'channel', label: 'Discord', tag: 'Messaging', multiple: true,
    blurb: 'DM the assistant from Discord, or point a server channel at a profile.',
    setup: 'Discord Developer Portal → New Application → Bot → Reset Token.',
    handles: true,
    fields: [{ key: 'token', label: 'Bot token', placeholder: 'MTIz…' }],
    surfaces: [{ id: 'all', label: 'Servers and direct messages' }],
  },
  {
    id: 'slack', kind: 'channel', label: 'Slack', tag: 'Messaging', multiple: true,
    blurb: 'Slack DMs and channels. Needs both a bot token and an app token.',
    setup: 'Slack app → OAuth (xoxb-…) and Socket Mode (xapp-…).',
    handles: false,
    fields: [
      { key: 'bot_token', label: 'Bot token', placeholder: 'xoxb-…' },
      { key: 'app_token', label: 'App token', placeholder: 'xapp-…' },
    ],
    surfaces: [{ id: 'all', label: 'Channels and direct messages' }],
  },
  {
    id: 'google', kind: 'google', label: 'Google', tag: 'Workspace', multiple: false,
    blurb: 'Gmail, Calendar and Drive, signed in as you.',
    setup: 'Sign in through the Google flow — no key to paste.',
  },
  {
    id: 'github', kind: 'github', label: 'GitHub', tag: 'Developer', multiple: false,
    blurb: 'Skills registry — raises the download rate limit. Optional.',
    setup: 'Any fine-grained personal access token with public read.',
    fields: [{ key: 'token', label: 'Token', placeholder: 'github_pat_…' }],
  },
]

export const byId = Object.fromEntries(CATALOG.map((e) => [e.id, e]))

// Single-letter mark where a provider logo would go — we ship no Telegram / Discord
// / Slack SVGs, and drawing them is not the question here.
export const MARK_TINT = {
  telegram: '#2aabee', discord: '#5865f2', slack: '#611f69',
  google: '#ea4335', github: '#8b949e',
}

let seq = 0
const nextId = (platform) => `${platform}-${++seq}`

export function createIntegrations() {
  const d = $state({
    ready: false,
    err: '',
    // [{id, platform, name, default_profile, accounts, groups, exposure, code}]
    instances: [],
    busy: '',
  })

  // Seed from the real install so the prototype shows YOUR bots, YOUR paired
  // accounts, YOUR groups — then never talks to the channel API again.
  d.load = async (profileList) => {
    try {
      const channels = await api.channels()
      const seeded = []
      for (const e of CATALOG) {
        if (e.kind !== 'channel') continue
        const c = channels[e.id]
        if (!c?.token_present) continue
        const [pairing, groups] = await Promise.all([
          api.channelPairing(e.id), api.channelGroups(e.id),
        ])
        seeded.push({
          id: nextId(e.id),
          platform: e.id,
          name: e.label,
          default_profile: c.default_profile ?? null,
          error: c.error || '',
          active: !!c.active,
          accounts: pairing.accounts || [],
          groups: (groups.groups || []).map((g) => ({ ...g })),
          groupProfiles: groups.profiles || [],
          // Exposure starts from the profile-major matrix it replaces: a profile is
          // reachable on a surface unless it has withdrawn from it.
          exposure: exposureFrom(profileList, e),
          code: null,
        })
      }
      d.instances = seeded
    } catch (e) { d.err = String(e.message || e) }
    d.ready = true
  }

  // ── everything below is a local stub ────────────────────────────────────────
  const find = (id) => d.instances.find((i) => i.id === id)

  d.connect = (platform, name, profileList) => {
    const e = byId[platform]
    const inst = {
      id: nextId(platform),
      platform,
      name: name || defaultName(d.instances, e),
      default_profile: null,
      error: '', active: true,
      accounts: [], groups: [], groupProfiles: profileList,
      exposure: exposureFrom(profileList, e),
      code: null,
    }
    d.instances = [...d.instances, inst]
    return inst.id
  }

  d.disconnect = (id) => { d.instances = d.instances.filter((i) => i.id !== id) }

  // Re-token in place: same connection, new credentials. Its paired accounts, group
  // pins and exposure all stay attached — which is only right when the new token is
  // the SAME bot (rotated / regenerated). Pointing it at a different bot silently
  // inherits the old bot's roster, so the UI says so before you do it.
  d.replaceToken = (id) => {
    const i = find(id)
    if (!i) return
    i.error = ''
    i.active = true
    i.tokenNote = 'token replaced'
  }
  d.rename = (id, name) => { const i = find(id); if (i) i.name = name }
  d.setDefault = (id, profile) => { const i = find(id); if (i) i.default_profile = profile || null }

  d.addAccount = (id, value) => {
    const i = find(id)
    if (!i || !value.trim()) return
    const v = value.trim()
    const handle = v.startsWith('@')
    i.accounts = [...i.accounts, {
      key: `${v}-${i.accounts.length}`,
      account_id: handle ? '' : v,
      handle: handle ? v.slice(1) : '',
      pending: handle,
    }]
  }
  d.revoke = (id, key) => { const i = find(id); if (i) i.accounts = i.accounts.filter((a) => a.key !== key) }
  d.issueCode = (id) => {
    const i = find(id)
    if (i) i.code = { code: String(Math.floor(100000 + Math.random() * 899999)) }
  }

  d.setGroupProfile = (id, chatId, profile) => {
    const i = find(id)
    const g = i?.groups.find((x) => x.chat_id === chatId)
    if (g) g.profile = profile
  }

  // The migrated switch: is `profile` reachable through THIS connection, on this
  // surface. Withdrawing the default profile from its last surface would leave the
  // connection pointing at somewhere it can't reach, so that also clears the default
  // — the two live in one table now, and the table shouldn't be able to lie.
  d.toggleExposure = (id, profileId, surface) => {
    const i = find(id)
    if (!i) return
    const cur = i.exposure[profileId] || {}
    const next = { ...cur, [surface]: cur[surface] === false }
    i.exposure = { ...i.exposure, [profileId]: next }
    if (i.default_profile === profileId && !reachableAnywhere(next, byId[i.platform])) {
      i.default_profile = null
    }
  }

  return d
}

// Can this connection reach the profile at all? A profile withdrawn from every
// surface can't be the one conversations land in.
export function reachableAnywhere(profileExposure, entry) {
  return entry.surfaces.some((s) => (profileExposure || {})[s.id] !== false)
}

// A profile is reachable everywhere unless it withdrew — mirrors the real default-allow.
function exposureFrom(profileList, entry) {
  const out = {}
  for (const p of profileList || []) {
    const ex = p.exposure || {}
    out[p.id] = Object.fromEntries(entry.surfaces.map((s) => [
      s.id,
      ex[s.id === 'all' ? entry.id : `${entry.id}:${s.id}`] !== false,
    ]))
  }
  return out
}

// "Telegram", then "Telegram 2", "Telegram 3" — a name you can edit later.
function defaultName(instances, entry) {
  const n = instances.filter((i) => i.platform === entry.id).length
  return n ? `${entry.label} ${n + 1}` : entry.label
}

// ── Status, one vocabulary ───────────────────────────────────────────────────
// A connection exists because a token was accepted, so "not connected" is no longer
// a state a row can be in — the questions left are healthy, and heard by anyone.
export function instanceStatus(inst, profName) {
  if (inst.error) return { kind: 'err', text: inst.error }
  if (!inst.active) return { kind: 'wait', text: 'connecting…' }
  if (!inst.accounts.length) return { kind: 'err', text: 'nobody paired — it answers nobody' }
  if (inst.default_profile == null) return { kind: 'wait', text: 'no default profile' }
  return { kind: 'ok', text: `messages go to ${profName}` }
}

export function googleStatus(g) {
  if (g == null) return { kind: 'wait', text: '…' }
  if (g.signed_in && g.libs_available === false) return { kind: 'err', text: 'needs libraries · not usable' }
  if (g.signed_in) return { kind: 'ok', text: 'connected · ' + (g.email || 'account') }
  return { kind: 'off', text: 'not connected' }
}

export function githubStatus(keys) {
  const k = keys?.github
  return k?.set
    ? { kind: 'ok', text: 'token set · ' + (k.hint || '') }
    : { kind: 'off', text: 'no token — using the anonymous rate limit' }
}

// The connected list: channel instances (0..n per platform) + the two singletons,
// which are rows only once they're actually connected.
export function connectedRows(d, ctx, profById) {
  const rows = d.instances.map((inst) => {
    const name = inst.default_profile != null
      ? (profById[inst.default_profile]?.name || inst.default_profile) : ''
    return {
      key: inst.id, kind: 'channel', entry: byId[inst.platform], inst,
      label: inst.name, mark: inst.platform,
      status: instanceStatus(inst, name),
    }
  })
  if (ctx.google?.signed_in) {
    rows.push({ key: 'google', kind: 'google', entry: byId.google, label: 'Google', mark: 'google', status: googleStatus(ctx.google) })
  }
  if (ctx.s?.keys?.github?.set) {
    rows.push({ key: 'github', kind: 'github', entry: byId.github, label: 'GitHub', mark: 'github', status: githubStatus(ctx.s.keys) })
  }
  return rows
}

// What the "Add integration" grid offers: every multi-instance platform always, plus
// the singletons that aren't connected yet.
export function addableEntries(d, ctx) {
  return CATALOG.filter((e) => e.multiple
    || (e.kind === 'google' ? !ctx.google?.signed_in : !ctx.s?.keys?.github?.set))
}
