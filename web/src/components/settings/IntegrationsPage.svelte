<script>
  // Settings → Integrations (install-wide, ADR 0019): the list of Connections this
  // install has, and the grid of what it can add — deliberately the same two-part
  // shape as Settings → Models.
  //
  // Three exclusive states, never two at once: the list, one integration's settings,
  // or the Connect form. A Connection is one configured instance of a platform, so a
  // second Telegram bot is an ordinary "add" — rows are keyed by connection id and
  // the platform shows as a tag only when there is more than one of it.
  //
  // Google and GitHub live in the same list: Google's card routes into its own
  // connect flow, GitHub's writes the one shared registry key, and both are offered
  // in the Add grid only while unconnected.
  import { onMount } from 'svelte'
  import { profiles } from '../../store.js'
  import { getSettings } from './context.svelte.js'
  import { api } from '../../transport/api.js'
  import Icon from '../Icon.svelte'
  import ConnectForm from './ConnectForm.svelte'
  import ConnectionDetail from './ConnectionDetail.svelte'
  import IntegrationHeader from './IntegrationHeader.svelte'
  import IntegrationStatus from './IntegrationStatus.svelte'
  import {
    CATALOG, MARK_TINT, byId, connectionStatus, googleStatus, githubStatus,
  } from '../../lib/integrations.js'

  const ctx = getSettings()

  let list = $state([])          // GET /api/connections → [entry, …], creation order
  let err = $state('')
  let loaded = $state(false)
  let openKey = $state(null)     // connection id, 'google' or 'github' — null = list
  let connecting = $state(null)  // CATALOG entry being connected — null = not connecting
  let adding = $state(false)     // the card grid
  let clearingGithub = $state(false)

  onMount(load)

  async function load() {
    try {
      list = (await api.connections()).connections || []
      err = ''
    } catch (e) { err = String(e.message || e) }
    loaded = true
  }

  const profById = $derived(Object.fromEntries(($profiles.list || []).map((p) => [p.id, p])))
  const count = (platform) => list.filter((c) => c.platform === platform).length
  const open = $derived(list.find((c) => c.id === openKey) || null)
  const googleOn = $derived(!!ctx.google?.signed_in)
  const githubOn = $derived(!!ctx.s?.keys?.github?.set)

  // What the grid offers: a platform that can be connected more than once is always
  // there (that is how a second bot is added), the singletons only while unconnected.
  const addable = $derived(CATALOG.filter((e) => e.multiple
    || (e.kind === 'google' ? !googleOn : !githubOn)))

  function pick(entry) {
    adding = false
    // Google has no token to type — its own flow owns sign-in.
    if (entry.kind === 'google') { ctx.openGoogle(); return }
    connecting = entry
  }

  // Throws on failure; ConnectForm shows the message and stays open. A connection
  // that is created but will not start comes back 200 with its reason — the new
  // settings pane opens on it and the status line says so.
  async function connect(name, tokens) {
    if (connecting.kind === 'github') {
      await api.setKey('github', tokens.token)
      await ctx.load()
      connecting = null
      openKey = 'github'
      return
    }
    const created = await api.createConnection(connecting.id, name, tokens)
    await load()
    connecting = null
    openKey = created.id
  }

  function back() {
    openKey = null
    connecting = null
    clearingGithub = false
  }

  const saveGithub = () =>
    ctx.run(() => api.setKey('github', ctx.drafts.github || '').then(() => { ctx.drafts.github = '' }))
  const clearGithub = () =>
    ctx.run(() => api.setKey('github', '').then(() => { clearingGithub = false; openKey = null }))
</script>

{#if connecting}
  <button class="cnback" onclick={() => { connecting = null; adding = true }}>
    <Icon name="chevron-left" size={13} /> All integrations
  </button>
  <ConnectForm
    entry={connecting} connections={list}
    onConnect={connect} onCancel={() => { connecting = null; adding = true }}
  />
{:else if open}
  <button class="cnback" onclick={back}><Icon name="chevron-left" size={13} /> All integrations</button>
  <ConnectionDetail
    connection={open}
    tag={count(open.platform) > 1 ? byId[open.platform].label : ''}
    reload={load} onDisconnected={back}
  />
{:else if openKey === 'google'}
  <button class="cnback" onclick={back}><Icon name="chevron-left" size={13} /> All integrations</button>
  <IntegrationHeader tint={MARK_TINT.google} mark="G" label="Google" status={googleStatus(ctx.google)} />
  <div class="setgroup">Account</div>
  <p class="setsub">
    {byId.google.setup} Gmail, Calendar and Drive tools appear once you are signed in.
    Managing it opens the Google flow — Settings closes behind it.
  </p>
  <div class="keyrow">
    <button class="open" onclick={ctx.openGoogle}>Manage</button>
  </div>
{:else if openKey === 'github'}
  <button class="cnback" onclick={back}><Icon name="chevron-left" size={13} /> All integrations</button>
  <IntegrationHeader tint={MARK_TINT.github} mark="G" label="GitHub" status={githubStatus(ctx.s?.keys)} />
  <div class="setgroup">Token</div>
  <p class="setsub">{byId.github.blurb} {byId.github.setup}</p>
  {#if ctx.err}<p class="cnerr">{ctx.err}</p>{/if}
  <div class="keyrow">
    <span class="kp">Token</span>
    <input
      type="password" aria-label="GitHub token"
      placeholder={githubOn ? '•••• ' + ctx.s.keys.github.hint : 'paste token'}
      bind:value={ctx.drafts.github}
    />
    <button class="open primary" disabled={ctx.busy} onclick={saveGithub}>Save</button>
  </div>
  {#if githubOn}
    {#if clearingGithub}
      <div class="keyrow">
        <span class="cnwarn">Disconnect GitHub? Skill downloads fall back to the anonymous rate limit.</span>
      </div>
      <div class="keyrow">
        <button class="open danger" disabled={ctx.busy} onclick={clearGithub}>Disconnect</button>
        <button class="open" disabled={ctx.busy} onclick={() => (clearingGithub = false)}>Cancel</button>
      </div>
    {:else}
      <div class="keyrow">
        <button class="open danger" disabled={ctx.busy} onclick={() => (clearingGithub = true)}>Disconnect</button>
      </div>
    {/if}
  {/if}
{:else}
  <div class="setgroup">
    Connected <span class="setwide" title="Shared across every profile in this install">install-wide</span>
  </div>

  {#if err}<p class="cnerr">{err}</p>{/if}

  {#if loaded && !list.length && !googleOn && !githubOn}
    <p class="setsub">Nothing connected yet — add one below.</p>
  {/if}

  {#each list as c (c.id)}
    <button class="cnrow" onclick={() => (openKey = c.id)}>
      <span class="cnmark" style="--tint:{MARK_TINT[c.platform]}">{byId[c.platform].label[0]}</span>
      <span class="cnmeta">
        <span class="cnname">
          {c.name}
          <!-- The platform is only worth saying once it stops being the row's identity. -->
          {#if count(c.platform) > 1}<span class="cntag">{byId[c.platform].label}</span>{/if}
        </span>
        <IntegrationStatus status={connectionStatus(c, profById[c.default_profile]?.name)} />
      </span>
      <Icon name="chevron-right" size={15} />
    </button>
  {/each}

  {#if googleOn}
    <button class="cnrow" onclick={() => (openKey = 'google')}>
      <span class="cnmark" style="--tint:{MARK_TINT.google}">G</span>
      <span class="cnmeta">
        <span class="cnname">Google</span>
        <IntegrationStatus status={googleStatus(ctx.google)} />
      </span>
      <Icon name="chevron-right" size={15} />
    </button>
  {/if}

  {#if githubOn}
    <button class="cnrow" onclick={() => (openKey = 'github')}>
      <span class="cnmark" style="--tint:{MARK_TINT.github}">G</span>
      <span class="cnmeta">
        <span class="cnname">GitHub</span>
        <IntegrationStatus status={githubStatus(ctx.s?.keys)} />
      </span>
      <Icon name="chevron-right" size={15} />
    </button>
  {/if}

  {#if !adding}
    <button class="addbtn" onclick={() => (adding = true)}>
      <Icon name="plus" size={14} /> Add integration
    </button>
  {:else}
    <div class="setsec">Pick an integration</div>
    <div class="mcpcat">
      {#each addable as e (e.id)}
        <button class="mcpcatcard" onclick={() => pick(e)}>
          <span class="mcpcathead">
            <span class="cnmark sm" style="--tint:{MARK_TINT[e.id]}">{e.label[0]}</span>
            {e.label}
            {#if count(e.id)}<span class="cntag">{count(e.id)} connected</span>{/if}
          </span>
          <span class="mcpcatblurb">{e.blurb}</span>
          {#if e.setup}<span class="cncatsetup">{e.setup}</span>{/if}
        </button>
      {/each}
    </div>
    <div class="keyrow" style="justify-content:flex-end">
      <button class="linkbtn" onclick={() => (adding = false)}>Cancel</button>
    </div>
  {/if}
{/if}
