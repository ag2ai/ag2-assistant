<script lang="ts">
  // Settings → Integrations → ACP listeners:
  // an install-level, one-Profile-per-listener server an external ACP client (AG2
  // Space, an editor) reaches directly over its own port. Deliberately its own
  // list rather than folded into the channel Connections above it — a listener
  // has no tokens dict, no exposure table and no default-profile control, all of
  // which the channel machinery in lib/integrations.ts assumes.
  //
  // A listener's token is never shown once it exists — `has_token` says only
  // whether one is set. The raw value rides the create and rotate-token
  // responses exactly once; this section is where the owner has to copy it.
  import { onMount } from 'svelte'
  import { profiles } from '../../store.ts'
  import { api } from '../../transport/api/index.ts'
  import { errText } from '../../lib/errors.ts'
  import Icon from '../Icon.svelte'
  import IntegrationStatus from './IntegrationStatus.svelte'
  import type { IntegrationStatus as Status } from '../../lib/integrations.ts'
  import type { AcpListener } from '../../schemas/index.ts'

  let listeners = $state<AcpListener[]>([])
  let loaded = $state(false)
  let err = $state('')

  let adding = $state(false)
  let draftProfile = $state('')
  let draftName = $state('')
  let draftPort = $state('')
  let draftToken = $state('')
  let createBusy = $state(false)
  let createErr = $state('')

  let expandedId = $state<string | null>(null)
  let confirmDeleteId = $state<string | null>(null)
  let busyId = $state('')

  // The one-time reveal: the token a create or rotate just minted. Cleared the
  // moment the owner says they are done with it — nothing re-shows it.
  let reveal = $state<{ id: string; name: string; token: string } | null>(null)
  let copied = $state(false)

  onMount(load)

  async function load() {
    try {
      listeners = await api.acpListeners()
      err = ''
    } catch (e) { err = errText(e) }
    loaded = true
  }

  const profileName = (pid: string) => $profiles.list.find((p) => p.id === pid)?.name || pid

  // running / not running / unreachable — the same honesty pattern
  // connectionStatus() uses for a bad bot token.
  function statusFor(l: AcpListener): Status {
    if (l.error) return { kind: 'err', text: l.error }
    return l.running ? { kind: 'ok', text: 'Running' } : { kind: 'wait', text: 'Not running' }
  }

  function toggle(id: string) {
    expandedId = expandedId === id ? null : id
    confirmDeleteId = null
  }

  function cancelAdd() {
    adding = false
    draftProfile = ''; draftName = ''; draftPort = ''; draftToken = ''
    createErr = ''
  }

  async function create() {
    if (!draftProfile || createBusy) return
    createErr = ''; createBusy = true
    try {
      const port = Number(draftPort.trim()) || 8802
      const created = await api.createAcpListener(
        draftProfile, port, draftName.trim(), draftToken.trim(),
      )
      await load()
      cancelAdd()
      reveal = { id: created.listener.id, name: created.listener.name, token: created.token }
      expandedId = created.listener.id
    } catch (e) {
      createErr = errText(e)
    }
    createBusy = false
  }

  async function stopL(id: string) {
    busyId = id; err = ''
    try {
      const row = await api.stopAcpListener(id)
      listeners = listeners.map((l) => (l.id === id ? row : l))
    } catch (e) { err = errText(e) }
    busyId = ''
  }

  async function startL(id: string) {
    busyId = id; err = ''
    try {
      const row = await api.startAcpListener(id)
      listeners = listeners.map((l) => (l.id === id ? row : l))
    } catch (e) { err = errText(e) }
    busyId = ''
  }

  async function rotate(id: string) {
    busyId = id; err = ''
    try {
      const rotated = await api.rotateAcpListenerToken(id)
      listeners = listeners.map((l) => (l.id === id ? rotated.listener : l))
      reveal = { id, name: rotated.listener.name, token: rotated.token }
    } catch (e) { err = errText(e) }
    busyId = ''
  }

  async function remove(id: string) {
    busyId = id; err = ''
    try {
      await api.deleteAcpListener(id)
      listeners = listeners.filter((l) => l.id !== id)
      if (reveal?.id === id) reveal = null
      expandedId = null
      confirmDeleteId = null
    } catch (e) { err = errText(e) }
    busyId = ''
  }

  async function copyReveal() {
    if (!reveal) return
    try {
      await navigator.clipboard.writeText(reveal.token)
      copied = true
      setTimeout(() => (copied = false), 2000)
    } catch {
      // Clipboard access can be refused; the token stays selectable in the field.
    }
  }

  function dismissReveal() { reveal = null; copied = false }
</script>

<div class="setgroup">
  ACP listeners
  <span class="setwide" title="Lets an external ACP client (AG2 Space, an editor) drive a profile directly">Experimental</span>
</div>
<p class="setsub">
  Serve one profile to an Agent Client Protocol client over its own port. One listener drives
  exactly one profile — add another listener for another.
</p>

{#if err}<p class="cnerr">{err}</p>{/if}

{#if loaded && !listeners.length && !adding}
  <p class="setsub">No listeners yet — add one below.</p>
{/if}

{#if listeners.length}
  <ul class="cnlist">
    {#each listeners as l (l.id)}
      <li class="cnitem acplistitem">
        <div class="cnitem">
          <span class="cnid">{l.name}</span>
          <span class="cnhint">{profileName(l.profile)} · port {l.port ?? '—'}</span>
          <IntegrationStatus status={statusFor(l)} />
          <button
            class="iconbtn sm" aria-label={expandedId === l.id ? `Collapse ${l.name}` : `Expand ${l.name}`}
            onclick={() => toggle(l.id)}
          >
            <Icon name={expandedId === l.id ? 'chevron-down' : 'chevron-right'} size={13} />
          </button>
        </div>

        {#if reveal?.id === l.id}
          <div class="cnform">
            <p class="cnnote">
              Token for "{l.name}" — copy it now and hand it to the client. It will not be shown again.
            </p>
            <div class="keyrow">
              <span class="kp">Token</span>
              <input
                readonly value={reveal.token} aria-label="Listener token"
                onclick={(e) => e.currentTarget.select()}
              />
              <button class="open" onclick={copyReveal}>{copied ? 'Copied' : 'Copy'}</button>
            </div>
            <div class="keyrow" style="justify-content:flex-end">
              <button class="open primary" onclick={dismissReveal}>Done</button>
            </div>
          </div>
        {:else if expandedId === l.id}
          <div class="keyrow">
            {#if l.running}
              <button class="open" disabled={busyId === l.id} onclick={() => stopL(l.id)}>
                {busyId === l.id ? 'Stopping…' : 'Stop'}
              </button>
            {:else}
              <button class="open" disabled={busyId === l.id} onclick={() => startL(l.id)}>
                {busyId === l.id ? 'Starting…' : 'Start'}
              </button>
            {/if}
            <button class="open" disabled={busyId === l.id} onclick={() => rotate(l.id)}>
              Rotate token
            </button>
            {#if confirmDeleteId === l.id}
              <span class="cnwarn">Delete "{l.name}"? Its connected clients are dropped.</span>
              <button class="open danger" disabled={busyId === l.id} onclick={() => remove(l.id)}>
                Delete
              </button>
              <button class="open" disabled={busyId === l.id} onclick={() => (confirmDeleteId = null)}>
                Cancel
              </button>
            {:else}
              <button class="open danger" disabled={busyId === l.id} onclick={() => (confirmDeleteId = l.id)}>
                Delete
              </button>
            {/if}
          </div>
        {/if}
      </li>
    {/each}
  </ul>
{/if}

{#if !adding}
  <button class="addbtn" onclick={() => (adding = true)}>
    <Icon name="plus" size={14} /> Add listener
  </button>
{:else}
  <div class="cnform">
    <div class="keyrow">
      <span class="kp">Profile</span>
      <select bind:value={draftProfile} aria-label="Profile" disabled={createBusy}>
        <option value="" disabled>Pick a profile</option>
        {#each $profiles.list as p (p.id)}<option value={p.id}>{p.name}</option>{/each}
      </select>
    </div>
    <div class="keyrow">
      <span class="kp">Name</span>
      <input
        placeholder="Space · work" aria-label="Listener name" disabled={createBusy}
        bind:value={draftName}
      />
    </div>
    <div class="keyrow">
      <span class="kp">Port</span>
      <input
        type="number" placeholder="8802" aria-label="Port" disabled={createBusy}
        bind:value={draftPort}
      />
    </div>
    <p class="cnhint">
      A token is generated for you and shown once the listener is created — paste your own
      below instead if you already have one.
    </p>
    <div class="keyrow">
      <span class="kp">Token</span>
      <input
        type="password" placeholder="generate one for me" aria-label="Listener token"
        disabled={createBusy} bind:value={draftToken}
      />
    </div>

    {#if createErr}<p class="cnerr">{createErr}</p>{/if}

    <div class="keyrow">
      <button class="open primary" disabled={!draftProfile || createBusy} onclick={create}>
        {createBusy ? 'Creating…' : 'Create listener'}
      </button>
      <button class="open" disabled={createBusy} onclick={cancelAdd}>Cancel</button>
    </div>
  </div>
{/if}

<style>
  .acplistitem { flex-direction: column; align-items: stretch; gap: 6px; }
</style>
