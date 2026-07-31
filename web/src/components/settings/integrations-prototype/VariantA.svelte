<script>
  // PROTOTYPE variant A (v2) — one column. Connected integrations are rows; clicking
  // one REPLACES the list with its settings; "Add integration" swaps in a grid of
  // prebuilt cards, and picking a card opens the connect form (name + token).
  //
  // Three states, never two at once: list → detail, list → connect → detail.
  import Icon from '../../Icon.svelte'
  import { api } from '../../../transport/api.js'
  import IntegrationDetail from './IntegrationDetail.svelte'
  import ConnectForm from './ConnectForm.svelte'
  import { MARK_TINT, connectedRows, addableEntries } from './data.svelte.js'

  let { d, ctx, profiles, profById } = $props()

  let openKey = $state(null)      // connection being configured
  let connecting = $state(null)   // CATALOG entry being connected
  let adding = $state(false)      // card grid showing
  let renaming = $state(false)

  const rows = $derived(connectedRows(d, ctx, profById))
  const open = $derived(rows.find((r) => r.key === openKey) || null)
  const addable = $derived(addableEntries(d, ctx))

  function pick(entry) {
    adding = false
    // Google has no token to type — its own flow owns that. Straight through.
    if (entry.kind === 'google') { ctx.openGoogle(); return }
    connecting = entry
  }

  function connect(name, tokens) {
    // GitHub is a real single key, not a stubbed instance — save it for real.
    if (connecting.kind === 'github') {
      ctx.run(() => api.setKey('github', tokens.token))
      connecting = null
      openKey = 'github'
      return
    }
    const id = d.connect(connecting.id, name, profiles)
    connecting = null
    openKey = id
  }

  const count = (platform) => d.instances.filter((i) => i.platform === platform).length
</script>

{#if connecting}
  <button class="back" onclick={() => { connecting = null; adding = true }}>‹ All integrations</button>
  <ConnectForm
    entry={connecting} existing={count(connecting.id)}
    onConnect={connect} onCancel={() => { connecting = null; adding = true }}
  />
{:else if open}
  <button class="back" onclick={() => { openKey = null; renaming = false }}>‹ All integrations</button>
  <div class="dethead">
    <span class="mark" style="--tint:{MARK_TINT[open.mark]}">{open.label[0]}</span>
    <div class="detmeta">
      {#if renaming && open.kind === 'channel'}
        <input
          class="rename" value={open.label} autofocus
          onblur={(e) => { d.rename(open.inst.id, e.target.value.trim() || open.label); renaming = false }}
          onkeydown={(e) => { if (e.key === 'Enter') e.target.blur() }}
        />
      {:else}
        <div class="detname">
          {open.label}
          {#if open.kind === 'channel'}
            <button class="pencil" aria-label="Rename" onclick={() => (renaming = true)}>
              <Icon name="pencil" size={12} />
            </button>
            <span class="kindtag">{open.entry.label}</span>
          {/if}
        </div>
      {/if}
      <div class="stat {open.status.kind}">
        {#if open.status.kind === 'ok'}<Icon name="check" size={13} />{/if}
        {#if open.status.kind === 'err'}<Icon name="alert-triangle" size={13} />{/if}
        <span>{open.status.text}</span>
      </div>
    </div>
  </div>
  <IntegrationDetail row={open} {d} {ctx} {profiles} />
{:else}
  <div class="setgroup">Connected <span class="setwide" title="Shared across every profile in this install">install-wide</span></div>
  {#if !rows.length}
    <p class="setsub">Nothing connected yet — add one below.</p>
  {/if}
  {#each rows as r (r.key)}
    <button class="row" onclick={() => (openKey = r.key)}>
      <span class="mark" style="--tint:{MARK_TINT[r.mark]}">{r.label[0]}</span>
      <span class="rowmeta">
        <span class="rowname">
          {r.label}
          {#if r.kind === 'channel' && count(r.inst.platform) > 1}<span class="kindtag">{r.entry.label}</span>{/if}
        </span>
        <span class="stat {r.status.kind}">
          {#if r.status.kind === 'ok'}<Icon name="check" size={13} />{/if}
          {#if r.status.kind === 'err'}<Icon name="alert-triangle" size={13} />{/if}
          <span>{r.status.text}</span>
        </span>
      </span>
      <span class="chev">›</span>
    </button>
  {/each}

  {#if !adding}
    <button class="addbtn" onclick={() => (adding = true)}><Icon name="plus" size={14} /> Add integration</button>
  {:else}
    <div class="setsec">Pick an integration</div>
    <div class="mcpcat">
      {#each addable as e (e.id)}
        <button class="mcpcatcard" onclick={() => pick(e)}>
          <span class="mcpcathead">
            <span class="mark sm" style="--tint:{MARK_TINT[e.id]}">{e.label[0]}</span> {e.label}
            {#if count(e.id)}<span class="kindtag">{count(e.id)} connected</span>{/if}
          </span>
          <span class="mcpcatblurb">{e.blurb}</span>
        </button>
      {/each}
    </div>
    <div class="keyrow" style="justify-content:flex-end">
      <button class="linkbtn" onclick={() => (adding = false)}>Cancel</button>
    </div>
  {/if}
{/if}

<style>
  .row {
    display: flex; align-items: center; gap: 10px; width: 100%; text-align: left;
    border: 1px solid var(--line); border-radius: 10px; padding: 9px 10px; margin-bottom: 6px;
    background: var(--surface); color: var(--ink); font: inherit; cursor: pointer;
  }
  .row:hover { border-color: color-mix(in srgb, var(--accent) 45%, var(--line)); }
  .rowmeta { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
  .rowname { display: flex; align-items: center; gap: 7px; font-size: 13px; font-weight: 600; }
  .kindtag {
    font-size: 10px; font-weight: 500; text-transform: uppercase; letter-spacing: .5px;
    color: var(--muted); border: 1px solid var(--line); border-radius: 999px; padding: 0 6px;
  }
  .chev { color: var(--muted); font-size: 17px; line-height: 1; }
  .mark {
    flex: none; display: inline-grid; place-items: center; width: 26px; height: 26px;
    border-radius: 7px; background: color-mix(in srgb, var(--tint) 22%, transparent);
    color: var(--tint); font-size: 13px; font-weight: 700;
  }
  .mark.sm { width: 18px; height: 18px; border-radius: 5px; font-size: 10px; }
  .stat { display: flex; align-items: center; gap: 5px; font-size: 12px; color: var(--text-muted); }
  .stat.ok { color: var(--success, #2f8c44); }
  .stat.err { color: var(--warning, #e0b400); }
  .back {
    border: none; background: none; padding: 0 0 8px; cursor: pointer;
    font: inherit; font-size: 12px; color: var(--text-muted);
  }
  .back:hover { color: var(--ink); }
  .dethead { display: flex; align-items: center; gap: 10px; padding-bottom: 10px; border-bottom: 1px solid var(--line); }
  .detmeta { min-width: 0; }
  .detname { display: flex; align-items: center; gap: 8px; font-size: 15px; font-weight: 600; }
  .pencil { border: none; background: none; color: var(--muted); cursor: pointer; padding: 2px; display: inline-flex; }
  .pencil:hover { color: var(--ink); }
  .rename {
    border: 1px solid var(--accent); border-radius: 8px; padding: 4px 8px;
    background: var(--bg); color: var(--ink); font: inherit; font-size: 15px; font-weight: 600;
  }
  .rename:focus { outline: none; box-shadow: var(--focus-ring); }
</style>
