<script lang="ts">
  // Settings → Integrations (install-wide, ADR 0022): the list of Connections this
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
  import { profiles } from '../../store.ts'
  import { getSettings } from './context.svelte.ts'
  import { api } from '../../transport/api/index.ts'
  import Icon from '../Icon.svelte'
  import ConnectForm from './ConnectForm.svelte'
  import ConnectionDetail from './ConnectionDetail.svelte'
  import IntegrationHeader from './IntegrationHeader.svelte'
  import IntegrationStatus from './IntegrationStatus.svelte'
  import IntegrationMark from './IntegrationMark.svelte'
  import {
    CATALOG, byId, platformLabel, connectionStatus, googleStatus, githubStatus,
  } from '../../lib/integrations.ts'
  import type { Integration } from '../../lib/integrations.ts'
  import { errText } from '../../lib/errors.ts'
  import { m } from '../../paraglide/messages.js'
  import type { Connection, Profile } from '../../schemas/index.ts'

  const ctx = getSettings()

  let list = $state<Connection[]>([])   // GET /api/connections, creation order
  let err = $state('')
  let loaded = $state(false)
  // connection id, 'google' or 'github' — null = the list
  let openKey = $state<string | null>(null)
  // CATALOG entry being connected — null = not connecting
  let connecting = $state<Integration | null>(null)
  let adding = $state(false)     // the card grid
  let clearingGithub = $state(false)

  onMount(load)

  async function load() {
    try {
      list = await api.connections()
      err = ''
    } catch (e) { err = errText(e) }
    loaded = true
  }

  const profById: Record<string, Profile | undefined> =
    $derived(Object.fromEntries($profiles.list.map((p) => [p.id, p])))
  const count = (platform: string) => list.filter((c) => c.platform === platform).length
  const open = $derived(list.find((c) => c.id === openKey) || null)
  const googleOn = $derived(!!ctx.google?.signed_in)
  const githubOn = $derived(!!ctx.s?.keys?.github?.set)

  // What the grid offers: a platform that can be connected more than once is always
  // there (that is how a second bot is added), the singletons only while unconnected.
  const addable = $derived(CATALOG.filter((e) => e.multiple
    || (e.kind === 'google' ? !googleOn : !githubOn)))

  function pick(entry: Integration) {
    adding = false
    // Google has no token to type — its own flow owns sign-in.
    if (entry.kind === 'google') { ctx.openGoogle(); return }
    connecting = entry
  }

  // Throws on failure; ConnectForm shows the message and stays open. A connection
  // that is created but will not start comes back 200 with its reason — the new
  // settings pane opens on it and the status line says so.
  async function connect(name: string, tokens: Record<string, string>) {
    if (!connecting) return
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
    <Icon name="chevron-left" size={13} /> {m.integrations_all()}
  </button>
  <ConnectForm
    entry={connecting} connections={list}
    onConnect={connect} onCancel={() => { connecting = null; adding = true }}
  />
{:else if open}
  <button class="cnback" onclick={back}><Icon name="chevron-left" size={13} /> {m.integrations_all()}</button>
  <ConnectionDetail
    connection={open}
    tag={count(open.platform) > 1 ? platformLabel(open.platform) : ''}
    reload={load} onDisconnected={back}
  />
{:else if openKey === 'google'}
  <button class="cnback" onclick={back}><Icon name="chevron-left" size={13} /> {m.integrations_all()}</button>
  <IntegrationHeader platform="google" label="Google" status={googleStatus(ctx.google)} />
  <div class="setgroup">{m.integrations_account()}</div>
  <p class="setsub">
    {byId.google?.setup()} {m.integrations_google_note()}
  </p>
  <div class="keyrow">
    <button class="open" onclick={ctx.openGoogle}>{m.integrations_manage()}</button>
  </div>
{:else if openKey === 'github'}
  <button class="cnback" onclick={back}><Icon name="chevron-left" size={13} /> {m.integrations_all()}</button>
  <IntegrationHeader platform="github" label="GitHub" status={githubStatus(ctx.s?.keys)} />
  <div class="setgroup">{m.integrations_token()}</div>
  <p class="setsub">{byId.github?.blurb()} {byId.github?.setup()}</p>
  {#if ctx.err}<p class="cnerr">{ctx.err}</p>{/if}
  <div class="keyrow">
    <span class="kp">{m.integrations_token()}</span>
    <input
      type="password" aria-label={m.integrations_github_token_aria()}
      placeholder={githubOn ? '•••• ' + (ctx.s?.keys.github?.hint || '') : m.integrations_paste_token()}
      bind:value={ctx.drafts.github}
    />
    <button class="open primary" disabled={ctx.busy} onclick={saveGithub}>{m.action_save()}</button>
  </div>
  {#if githubOn}
    {#if clearingGithub}
      <div class="keyrow">
        <span class="cnwarn">{m.integrations_github_disconnect_confirm()}</span>
      </div>
      <div class="keyrow">
        <button class="open danger" disabled={ctx.busy} onclick={clearGithub}>{m.integrations_disconnect()}</button>
        <button class="open" disabled={ctx.busy} onclick={() => (clearingGithub = false)}>{m.action_cancel()}</button>
      </div>
    {:else}
      <div class="keyrow">
        <button class="open danger" disabled={ctx.busy} onclick={() => (clearingGithub = true)}>{m.integrations_disconnect()}</button>
      </div>
    {/if}
  {/if}
{:else}
  <div class="setgroup">
    {m.integrations_connected()} <span class="setwide" title={m.settings_install_wide_title()}>{m.settings_install_wide()}</span>
  </div>

  {#if err}<p class="cnerr">{err}</p>{/if}

  {#if loaded && !list.length && !googleOn && !githubOn}
    <p class="setsub">{m.integrations_empty()}</p>
  {/if}

  {#each list as c (c.id)}
    <button class="cnrow" onclick={() => (openKey = c.id)}>
      <IntegrationMark platform={c.platform} name={c.name} />
      <span class="cnmeta">
        <span class="cnname">
          {c.name}
          <!-- The platform is only worth saying once it stops being the row's identity. -->
          {#if count(c.platform) > 1}<span class="cntag">{platformLabel(c.platform)}</span>{/if}
        </span>
        <IntegrationStatus status={connectionStatus(c, c.default_profile ? profById[c.default_profile]?.name : '')} />
      </span>
      <Icon name="chevron-right" size={15} />
    </button>
  {/each}

  {#if googleOn}
    <button class="cnrow" onclick={() => (openKey = 'google')}>
      <IntegrationMark platform="google" name="Google" />
      <span class="cnmeta">
        <span class="cnname">Google</span>
        <IntegrationStatus status={googleStatus(ctx.google)} />
      </span>
      <Icon name="chevron-right" size={15} />
    </button>
  {/if}

  {#if githubOn}
    <button class="cnrow" onclick={() => (openKey = 'github')}>
      <IntegrationMark platform="github" name="GitHub" />
      <span class="cnmeta">
        <span class="cnname">GitHub</span>
        <IntegrationStatus status={githubStatus(ctx.s?.keys)} />
      </span>
      <Icon name="chevron-right" size={15} />
    </button>
  {/if}

  {#if !adding}
    <button class="addbtn" onclick={() => (adding = true)}>
      <Icon name="plus" size={14} /> {m.integrations_add()}
    </button>
  {:else}
    <div class="setsec">{m.integrations_pick()}</div>
    <div class="mcpcat">
      {#each addable as e (e.id)}
        <button class="mcpcatcard" onclick={() => pick(e)}>
          <span class="mcpcathead">
            <IntegrationMark platform={e.id} name={e.label} sm />
            {e.label}
            {#if count(e.id)}<span class="cntag">{m.integrations_connected_count({ count: count(e.id) })}</span>{/if}
          </span>
          <span class="mcpcatblurb">{e.blurb()}</span>
          <span class="cncatsetup">{e.setup()}</span>
        </button>
      {/each}
    </div>
    <div class="keyrow" style="justify-content:flex-end">
      <button class="linkbtn" onclick={() => (adding = false)}>{m.action_cancel()}</button>
    </div>
  {/if}
{/if}
