<script>
  // PROTOTYPE — throwaway. One connection's settings.
  //
  // No token field: a connected instance is defined by the token it was created
  // with, and a different token is a different bot. Replacing means Connect the new
  // one + Disconnect this one, which is also the only honest way to keep the paired
  // accounts and group pins straight.
  import Icon from '../../Icon.svelte'
  import { api } from '../../../transport/api.js'
  import { reachableAnywhere } from './data.svelte.js'

  // row: one entry from connectedRows(). d: createIntegrations(). ctx: settings ctx.
  let { row, d, ctx, profiles = [] } = $props()

  let confirmOff = $state(false)
  let pairing = $state(false)    // the "pair an account" form, folded by default
  let pairDraft = $state('')
  let revoking = $state(null)    // account key awaiting confirmation
  let replacing = $state(false)  // the "replace token" form, likewise
  let tokenDrafts = $state({})

  const tokensReady = $derived(
    row.kind === 'channel' && row.entry.fields.every((f) => (tokenDrafts[f.key] || '').trim()),
  )

  function saveToken(instId) {
    if (!tokensReady) return
    d.replaceToken(instId, tokenDrafts)
    tokenDrafts = {}
    replacing = false
  }

  function addPair(instId) {
    if (!pairDraft.trim()) return
    d.addAccount(instId, pairDraft)
    pairDraft = ''
    pairing = false
  }

  const saveGithub = () =>
    ctx.run(() => api.setKey('github', ctx.drafts.github || '').then(() => { ctx.drafts.github = '' }))
  const clearGithub = () => ctx.run(() => api.setKey('github', ''))
</script>

{#if row.kind === 'channel'}
  {@const inst = row.inst}
  {@const e = row.entry}

  <!-- One table, two questions that were separate controls: which profiles this
       connection can reach (migrated from Profile editor → Channels, read
       integration-major) and which of them it lands in by default. They're the same
       row of the same list, so they're the same table — and the pairing is now
       visible: you can't make an unreachable profile the default. -->
  <section class="sec">
    <h4>Profiles</h4>
    <p class="hint">
      Every profile is reachable through this connection until you turn it off; a conversation
      already sitting in a withdrawn one is told, not moved. <b>Default</b> is where a new
      conversation lands when nothing else has been chosen.
    </p>
    <table class="exp">
      <thead>
        <tr>
          <th></th>
          <th class="expdef">Default</th>
          {#each e.surfaces as s}<th class="expcol">{s.label}</th>{/each}
        </tr>
      </thead>
      <tbody>
        {#each profiles as p (p.id)}
          {@const reach = reachableAnywhere(inst.exposure[p.id], e)}
          <tr>
            <td class="expname">
              {#if p.accent}<span class="pdot" style="--dot:{p.accent}"></span>{/if}
              <span class:dim={!reach}>{p.name}</span>
            </td>
            <td class="expdef">
              <!-- A button, not an <input type=radio>: a native radio group can't be
                   emptied by clicking the checked one, and "no default" has to stay
                   reachable now that its own row is gone. -->
              <button
                class="radio" class:on={inst.default_profile === p.id} role="radio"
                aria-checked={inst.default_profile === p.id} disabled={!reach}
                aria-label="{p.name} is the default profile for {inst.name}"
                title={!reach
                  ? 'Withdrawn from every surface — it can’t be the default'
                  : inst.default_profile === p.id
                    ? 'Click to leave this connection with no default'
                    : 'Make this the default profile'}
                onclick={() => d.setDefault(inst.id, inst.default_profile === p.id ? '' : p.id)}
              ></button>
            </td>
            {#each e.surfaces as s}
              <td class="expcol">
                <button
                  class="sw" class:on={inst.exposure[p.id]?.[s.id] !== false} role="switch"
                  aria-checked={inst.exposure[p.id]?.[s.id] !== false}
                  aria-label="{p.name} reachable from {inst.name} {s.label}"
                  onclick={() => d.toggleExposure(inst.id, p.id, s.id)}
                ></button>
              </td>
            {/each}
          </tr>
        {/each}
      </tbody>
    </table>
    {#if inst.default_profile == null}
      <p class="warn">No default — a conversation that hasn't been pointed anywhere is refused.</p>
    {/if}
  </section>

  <section class="sec">
    <div class="sechead">
      <h4>Who it answers</h4>
      <button class="btn ghost" onclick={() => d.issueCode(inst.id)}>
        {inst.code ? 'New pairing code' : 'Pairing code'}
      </button>
    </div>
    {#if inst.code}
      <p class="hint">Send <b class="mono">{inst.code.code}</b> to this bot from the account you want to pair.</p>
    {/if}
    {#if !inst.accounts.length}
      <p class="warn">Nobody yet. Until someone is paired, this connection answers nobody.</p>
    {:else}
      <ul class="rows">
        {#each inst.accounts as a (a.key)}
          <li>
            <span class="mono">{a.pending ? '@' + a.handle : (a.handle ? `${a.account_id} (@${a.handle})` : a.account_id)}</span>
            <!-- Revoking is not undoable from here — the account has to be paired
                 again, and a pending invitation can't be re-issued to the same
                 handle without a new code. So it asks first, in place. -->
            {#if revoking === a.key}
              <span class="warn push">
                {a.pending ? 'Cancel this invitation?' : 'Stop answering them?'}
              </span>
              <button class="btn danger" onclick={() => { d.revoke(inst.id, a.key); revoking = null }}>
                {a.pending ? 'Cancel invite' : 'Revoke'}
              </button>
              <button class="btn ghost" onclick={() => (revoking = null)}>Keep</button>
            {:else}
              <button class="btn ghost push" onclick={() => (revoking = a.key)}>Revoke</button>
            {/if}
          </li>
        {/each}
      </ul>
    {/if}
    <!-- The pairing form is the rare action here — the list of who's already paired is
         what you came to read. It stays folded until asked for. -->
    {#if pairing}
      <div class="line">
        <input
          class="ctl grow" placeholder={e.handles ? 'numeric account id, or @handle' : 'numeric account id'}
          aria-label="Pair an account" bind:value={pairDraft}
          onkeydown={(ev) => { if (ev.key === 'Enter') addPair(inst.id); if (ev.key === 'Escape') pairing = false }}
        />
        <button class="btn" disabled={!pairDraft.trim()} onclick={() => addPair(inst.id)}>Pair</button>
        <button class="btn ghost" onclick={() => { pairing = false; pairDraft = '' }}>Cancel</button>
      </div>
    {:else}
      <button class="add" onclick={() => (pairing = true)}>
        <Icon name="plus" size={13} /> Pair an account
      </button>
    {/if}
  </section>

  {#if inst.groups.length}
    <section class="sec">
      <h4>Groups</h4>
      <p class="hint">A group's profile is set here, not from the group — anyone in it can read the answers.</p>
      <ul class="rows">
        {#each inst.groups as g (g.chat_id)}
          {@const reachable = profiles.some((p) => p.id === g.profile)}
          <li>
            <span class="mono">{g.chat_id}</span>
            {#if !reachable}<span class="warn">not reachable here — pick another</span>{/if}
            <select
              class="ctl push" aria-label="Profile for group {g.chat_id}" value={reachable ? g.profile : ''}
              onchange={(ev) => d.setGroupProfile(inst.id, g.chat_id, ev.target.value)}
            >
              {#if !reachable}<option value="">Pick a profile</option>{/if}
              {#each profiles as p (p.id)}<option value={p.id}>{p.name}</option>{/each}
            </select>
          </li>
        {/each}
      </ul>
    </section>
  {/if}

  <section class="sec">
    <h4>Connection</h4>
    <p class="hint">
      {e.label} · {inst.tokenNote || 'token set when this connection was made'}. Replacing it keeps
      this connection's paired accounts, groups and profile settings — right for a rotated token,
      wrong for a different bot, which should be connected as its own integration.
    </p>

    {#if replacing}
      {#each e.fields as f (f.key)}
        <div class="line">
          <span class="lab">{f.label}</span>
          <input
            type="password" class="ctl grow" placeholder="paste to replace"
            bind:value={tokenDrafts[f.key]}
            onkeydown={(ev) => { if (ev.key === 'Enter' && tokensReady) saveToken(inst.id); if (ev.key === 'Escape') replacing = false }}
          />
        </div>
      {/each}
      <div class="line">
        <button class="btn" disabled={!tokensReady} onclick={() => saveToken(inst.id)}>Replace token</button>
        <button class="btn ghost" onclick={() => { replacing = false; tokenDrafts = {} }}>Cancel</button>
      </div>
    {:else if !confirmOff}
      <div class="line">
        <button class="btn ghost" onclick={() => (replacing = true)}>Replace token</button>
        <button class="btn ghost danger push" onclick={() => (confirmOff = true)}>Disconnect</button>
      </div>
    {/if}

    {#if confirmOff}
      <div class="line">
        <span class="warn">Disconnect {inst.name}? Its paired accounts and group pins go with it.</span>
        <button class="btn danger push" onclick={() => d.disconnect(inst.id)}>Disconnect</button>
        <button class="btn ghost" onclick={() => (confirmOff = false)}>Cancel</button>
      </div>
    {/if}
  </section>

{:else if row.kind === 'google'}
  <section class="sec">
    <h4>Account</h4>
    <p class="hint">{row.entry.setup} Gmail, Calendar and Drive tools appear once you're signed in.</p>
    <div class="line">
      <span class="stat {row.status.kind}">
        {#if row.status.kind === 'ok'}<Icon name="check" size={13} />{/if}{row.status.text}
      </span>
      <button class="btn push" onclick={ctx.openGoogle}>Manage…</button>
    </div>
    <p class="hint">Opens the Google connect flow — Settings closes behind it.</p>
  </section>

{:else}
  <section class="sec">
    <h4>Token</h4>
    <p class="hint">{row.entry.blurb} {row.entry.setup}</p>
    <div class="line">
      <input
        type="password" class="ctl grow"
        placeholder={ctx.s.keys.github?.set ? '•••• ' + ctx.s.keys.github.hint : 'paste token'}
        bind:value={ctx.drafts.github}
      />
      <button class="btn" disabled={ctx.busy} onclick={saveGithub}>Save</button>
      {#if ctx.s.keys.github?.set}
        <button class="btn ghost danger" disabled={ctx.busy} onclick={clearGithub}>Disconnect</button>
      {/if}
    </div>
  </section>
{/if}

<style>
  .sec { display: flex; flex-direction: column; gap: 7px; padding: 12px 0; border-bottom: 1px solid var(--line); }
  .sec:last-child { border-bottom: none; }
  .sechead { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
  h4 { margin: 0; font-size: 11px; text-transform: uppercase; letter-spacing: 1px; color: var(--muted); font-weight: 600; }
  .hint { margin: 0; font-size: 12px; color: var(--text-muted); line-height: 1.45; }
  .warn { margin: 0; font-size: 12px; color: var(--warning, #e0b400); }
  .line { display: flex; align-items: center; gap: 8px; }
  .grow { flex: 1; min-width: 0; }
  .push { margin-left: auto; }
  .mono { font-family: var(--mono, monospace); font-size: 12px; }
  .tag { font-size: 10px; text-transform: uppercase; letter-spacing: .5px; color: var(--muted); border: 1px solid var(--line); border-radius: 999px; padding: 0 6px; margin-left: 6px; }
  .ctl {
    border: 1px solid var(--line); border-radius: 8px; padding: 7px 10px;
    background: var(--bg); color: var(--ink); font: inherit; font-size: 13px;
  }
  select.ctl { padding-right: 28px; cursor: pointer; }
  .ctl:focus { outline: none; border-color: var(--accent); box-shadow: var(--focus-ring); }
  .btn {
    border: 1px solid var(--line); border-radius: 8px; padding: 6px 13px;
    background: var(--bg); color: var(--ink); font: inherit; font-size: 13px; cursor: pointer;
  }
  .btn:hover:not(:disabled) { border-color: var(--accent); }
  .btn:disabled { opacity: .55; cursor: default; }
  .btn.ghost { border-color: transparent; background: none; color: var(--text-muted); padding: 6px 8px; }
  .btn.ghost:hover:not(:disabled) { color: var(--ink); }
  .btn.danger { color: var(--danger); }
  /* Ghost "add" affordance, same idiom as Settings' .addbtn (Add model / Add profile). */
  .add {
    display: inline-flex; align-items: center; gap: 6px; align-self: flex-start;
    border: none; background: none; padding: 4px 0; cursor: pointer;
    font: inherit; font-size: 13px; font-weight: 600; color: var(--text-muted);
  }
  .add:hover { color: var(--ink); }
  .btn.danger:hover:not(:disabled) { border-color: var(--danger); color: var(--danger); }
  .rows { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
  .rows li { display: flex; align-items: center; gap: 8px; }
  .stat { display: inline-flex; align-items: center; gap: 5px; font-size: 13px; color: var(--text-muted); }
  .stat.ok { color: var(--success, #2f8c44); }
  .stat.err { color: var(--warning, #e0b400); }

  /* Exposure matrix — one row per profile, one switch per surface. */
  .exp { width: 100%; border-collapse: collapse; }
  .exp th { font-weight: 500; font-size: 10.5px; text-transform: uppercase; letter-spacing: .5px; color: var(--muted); padding: 0 0 4px; text-align: left; }
  .exp td { padding: 4px 0; font-size: 13px; }
  .expcol { width: 108px; text-align: left; }
  .expdef { width: 62px; text-align: left; }
  .expname { display: flex; align-items: center; gap: 7px; }
  .expname .dim { color: var(--muted); }
  .radio {
    width: 15px; height: 15px; border-radius: 999px; padding: 0; cursor: pointer;
    border: 1.5px solid var(--line); background: var(--bg); position: relative;
  }
  .radio:hover:not(:disabled) { border-color: var(--accent); }
  .radio.on { border-color: var(--accent); }
  .radio.on::after {
    content: ''; position: absolute; inset: 2.5px; border-radius: 999px; background: var(--accent);
  }
  .radio:disabled { cursor: default; opacity: .35; }
  .radio:focus-visible { outline: none; box-shadow: var(--focus-ring); }
  .pdot { width: 8px; height: 8px; border-radius: 999px; background: var(--dot); flex: none; }
  .sw {
    width: 32px; height: 18px; border-radius: 999px; border: 1px solid var(--line);
    background: var(--surface-sunk, rgba(0,0,0,.06)); cursor: pointer; padding: 0; position: relative;
  }
  .sw::after {
    content: ''; position: absolute; top: 2px; left: 2px; width: 12px; height: 12px;
    border-radius: 999px; background: var(--muted); transition: left .12s ease, background .12s ease;
  }
  .sw.on { border-color: var(--accent); background: var(--accent-soft); }
  .sw.on::after { left: 16px; background: var(--accent); }
</style>
