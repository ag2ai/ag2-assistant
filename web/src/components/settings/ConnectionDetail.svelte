<script>
  // One connection's settings: the header (mark, name renamed in place, status) and,
  // at the bottom, the Connection section — replace token and disconnect, each folded
  // behind its own button and each confirming or cancelling in place, no modals.
  // The Profiles table and the Paired accounts / Groups sections slot in between.
  import { profiles } from '../../store.js'
  import { api } from '../../transport/api.js'
  import { byId, platformLabel, connectionStatus } from '../../lib/integrations.js'
  import IntegrationHeader from './IntegrationHeader.svelte'
  import ConnectionProfiles from './ConnectionProfiles.svelte'
  import ConnectionPairing from './ConnectionPairing.svelte'
  import ConnectionGroups from './ConnectionGroups.svelte'

  // connection: one entry from GET /api/connections. tag: the platform label, shown
  // only when the platform has more than one connection. reload: re-fetch the list
  // (this pane always renders the list's copy). onDisconnected: back to the list.
  let { connection, tag = '', reload, onDisconnected } = $props()

  const entry = $derived(byId[connection.platform])
  const profById = $derived(Object.fromEntries(($profiles.list || []).map((p) => [p.id, p])))
  const status = $derived(
    connectionStatus(connection, profById[connection.default_profile]?.name),
  )

  let busy = $state(false)
  let err = $state('')
  let note = $state('')
  let replacing = $state(false)
  let confirmOff = $state(false)
  let drafts = $state({})

  // Empty for a platform this build does not know, so the pane still opens on its
  // name and status and the disconnect button stays reachable.
  const fields = $derived(entry?.fields || [])
  const ready = $derived(fields.length > 0 && fields.every((f) => (drafts[f.env] || '').trim()))

  async function rename(name) {
    err = ''; busy = true
    let ok = false
    try {
      await api.renameConnection(connection.id, name)
      await reload()
      ok = true
    } catch (e) { err = String(e.message || e) }
    busy = false
    return ok
  }

  async function replace() {
    if (!ready || busy) return
    err = ''; note = ''; busy = true
    try {
      await api.replaceConnectionTokens(connection.id, Object.fromEntries(
        fields.map((f) => [f.env, (drafts[f.env] || '').trim()]),
      ))
      await reload()
      drafts = {}
      replacing = false
      note = 'Token replaced — the connection restarted on it.'
    } catch (e) {
      // A refused replacement is rolled back server-side: the previous token is
      // restored and the old bot stays live, so only the message changes here.
      err = String(e.message || e)
    }
    busy = false
  }

  async function disconnect() {
    err = ''; busy = true
    try {
      await api.deleteConnection(connection.id)
      await reload()
      onDisconnected()
    } catch (e) { err = String(e.message || e) }
    busy = false
  }

  // "TELEGRAM_BOT_TOKEN …9f2c" — what is actually set, never the value.
  const tokenHints = $derived(
    fields
      .map((f) => `${f.label.toLowerCase()} ${connection.tokens?.[f.env]?.hint || ''}`.trim())
      .join(' · '),
  )
</script>

<IntegrationHeader
  platform={connection.platform} label={connection.name} {tag} {status}
  onRename={rename} {busy}
/>

<ConnectionProfiles {connection} {profById} {reload} />
<ConnectionPairing {connection} {reload} />
<ConnectionGroups {connection} />

<div class="setgroup">Connection</div>
<p class="setsub">{platformLabel(connection.platform)} · {tokenHints || 'token set when this connection was made'}</p>

{#if err}<p class="cnerr">{err}</p>{/if}
{#if note}<p class="cnnote">{note}</p>{/if}

{#if replacing}
  <p class="cnhint">
    Replacing keeps this connection's paired accounts, group pins and profile settings —
    right for a rotated token, wrong for a different bot, which belongs in its own
    integration.
  </p>
  {#each fields as f (f.env)}
    <div class="keyrow">
      <span class="kp">{f.label}</span>
      <input
        type="password" placeholder="paste to replace" disabled={busy}
        aria-label="New {f.label.toLowerCase()}" bind:value={drafts[f.env]}
        onkeydown={(e) => { if (e.key === 'Enter') replace() }}
      />
    </div>
  {/each}
  <div class="keyrow">
    <button class="open primary" disabled={!ready || busy} onclick={replace}>
      {busy ? 'Replacing…' : 'Replace token'}
    </button>
    <button class="open" disabled={busy} onclick={() => { replacing = false; drafts = {} }}>Cancel</button>
  </div>
{:else if confirmOff}
  <div class="keyrow">
    <span class="cnwarn">
      Disconnect {connection.name}? Its paired accounts, group pins and default profile go
      with it. Every other connection is left running.
    </span>
  </div>
  <div class="keyrow">
    <button class="open danger" disabled={busy} onclick={disconnect}>
      {busy ? 'Disconnecting…' : 'Disconnect'}
    </button>
    <button class="open" disabled={busy} onclick={() => (confirmOff = false)}>Cancel</button>
  </div>
{:else}
  <div class="keyrow">
    <button class="open" disabled={busy} onclick={() => { replacing = true; note = '' }}>Replace token</button>
    <button class="open danger" disabled={busy} onclick={() => (confirmOff = true)}>Disconnect</button>
  </div>
{/if}
