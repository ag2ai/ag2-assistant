<script lang="ts">
  // One connection's settings: the header (mark, name renamed in place, status) and,
  // at the bottom, the Connection section — replace token and disconnect, each folded
  // behind its own button and each confirming or cancelling in place, no modals.
  // The Profiles table and the Paired accounts / Groups sections slot in between.
  import { profiles } from '../../store.ts'
  import { api } from '../../transport/api/index.ts'
  import { byId, platformLabel, connectionStatus } from '../../lib/integrations.ts'
  import { errText } from '../../lib/errors.ts'
  import { m } from '../../paraglide/messages.js'
  import type { Connection, Profile } from '../../schemas/index.ts'
  import IntegrationHeader from './IntegrationHeader.svelte'
  import ConnectionProfiles from './ConnectionProfiles.svelte'
  import ConnectionPairing from './ConnectionPairing.svelte'
  import ConnectionGroups from './ConnectionGroups.svelte'

  // connection: one entry from GET /api/connections. tag: the platform label, shown
  // only when the platform has more than one connection. reload: re-fetch the list
  // (this pane always renders the list's copy). onDisconnected: back to the list.
  type Props = {
    connection: Connection
    tag?: string
    reload: () => Promise<void>
    onDisconnected: () => void
  }
  let { connection, tag = '', reload, onDisconnected }: Props = $props()

  const entry = $derived(byId[connection.platform])
  const profById: Record<string, Profile | undefined> =
    $derived(Object.fromEntries($profiles.list.map((p) => [p.id, p])))
  const status = $derived(connectionStatus(
    connection,
    connection.default_profile ? profById[connection.default_profile]?.name : '',
  ))

  let busy = $state(false)
  let err = $state('')
  let note = $state('')
  let replacing = $state(false)
  let confirmOff = $state(false)
  let drafts = $state<Record<string, string>>({})

  // Empty for a platform this build does not know, so the pane still opens on its
  // name and status and the disconnect button stays reachable.
  const fields = $derived(entry?.fields || [])
  const ready = $derived(fields.length > 0 && fields.every((f) => (drafts[f.env] || '').trim()))

  async function rename(name: string): Promise<boolean> {
    err = ''; busy = true
    let ok = false
    try {
      await api.renameConnection(connection.id, name)
      await reload()
      ok = true
    } catch (e) { err = errText(e) }
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
      note = m.integrations_token_replaced()
    } catch (e) {
      // A refused replacement is rolled back server-side: the previous token is
      // restored and the old bot stays live, so only the message changes here.
      err = errText(e)
    }
    busy = false
  }

  async function disconnect() {
    err = ''; busy = true
    try {
      await api.deleteConnection(connection.id)
      await reload()
      onDisconnected()
    } catch (e) { err = errText(e) }
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

<div class="setgroup">{m.integrations_connection()}</div>
<p class="setsub">{platformLabel(connection.platform)} · {tokenHints || m.integrations_token_set_when()}</p>

{#if err}<p class="cnerr">{err}</p>{/if}
{#if note}<p class="cnnote">{note}</p>{/if}

{#if replacing}
  <p class="cnhint">
    {m.integrations_replace_hint()}
  </p>
  {#each fields as f (f.env)}
    <div class="keyrow">
      <span class="kp">{f.label}</span>
      <input
        type="password" placeholder={m.integrations_paste_to_replace()} disabled={busy}
        aria-label={m.integrations_new_token_aria({ name: f.label.toLowerCase() })} bind:value={drafts[f.env]}
        onkeydown={(e) => { if (e.key === 'Enter') replace() }}
      />
    </div>
  {/each}
  <div class="keyrow">
    <button class="open primary" disabled={!ready || busy} onclick={replace}>
      {busy ? m.integrations_replacing() : m.integrations_replace_token()}
    </button>
    <button class="open" disabled={busy} onclick={() => { replacing = false; drafts = {} }}>{m.action_cancel()}</button>
  </div>
{:else if confirmOff}
  <div class="keyrow">
    <span class="cnwarn">
      {m.integrations_disconnect_confirm({ name: connection.name })}
    </span>
  </div>
  <div class="keyrow">
    <button class="open danger" disabled={busy} onclick={disconnect}>
      {busy ? m.integrations_disconnecting() : m.integrations_disconnect()}
    </button>
    <button class="open" disabled={busy} onclick={() => (confirmOff = false)}>{m.action_cancel()}</button>
  </div>
{:else}
  <div class="keyrow">
    <button class="open" disabled={busy} onclick={() => { replacing = true; note = '' }}>{m.integrations_replace_token()}</button>
    <button class="open danger" disabled={busy} onclick={() => (confirmOff = true)}>{m.integrations_disconnect()}</button>
  </div>
{/if}
