<script>
  // Settings → "Channels" section (§4.5). Channels are an INSTALL-LEVEL resource:
  // each platform (Telegram / Discord / Slack) binds to exactly one profile or is
  // disabled — never per-profile toggles, never conflicts. State comes from the
  // GLOBAL GET /api/channels; changing a row's profile picker POSTs to the GLOBAL
  // /api/channels and applies the returned entry. The bot token(s) for each platform
  // are edited inline here (POST /api/channels/token) — stored in the global secrets
  // store, mirroring the API-key inputs in Settings; values are never echoed back.
  import { onMount } from 'svelte'
  import { profiles } from '../store.js'
  import { api } from '../transport/api.js'
  import Icon from './Icon.svelte'

  // Token env var(s) per platform — mirrors the backend CHANNEL_TOKEN_ENVS. Slack
  // needs two (bot + app); the others a single token. `label` is null for a
  // single-field platform (no need to name it) and set when there's more than one.
  const PLATFORMS = [
    { id: 'telegram', label: 'Telegram', fields: [{ env: 'TELEGRAM_BOT_TOKEN', label: null }] },
    { id: 'discord', label: 'Discord', fields: [{ env: 'DISCORD_BOT_TOKEN', label: null }] },
    { id: 'slack', label: 'Slack', fields: [{ env: 'SLACK_BOT_TOKEN', label: 'Bot token' }, { env: 'SLACK_APP_TOKEN', label: 'App token' }] },
  ]

  // {telegram|discord|slack: {profile: pid|null, token_present, active, error}}
  let channels = $state(null)
  let busy = $state('') // platform id currently rebinding/saving (disables its row)
  let err = $state('')
  // Per-env token draft inputs (ENV_NAME -> string). Emptied after a successful save.
  let drafts = $state({})

  // Unarchived profiles, for the pickers + name/accent lookup.
  const list = $derived(($profiles.list || []).filter((p) => !p.archived))
  const profById = $derived(Object.fromEntries(list.map((p) => [p.id, p])))

  async function load() {
    try {
      channels = await api.channels()
    } catch (e) { err = String(e.message || e) }
  }
  onMount(load)

  async function bind(platform, profile) {
    if (busy) return
    busy = platform; err = ''
    // Empty <select> value means "Disabled" → null binding.
    const pid = profile || null
    try {
      const res = await api.channelBind(platform, pid)
      // Merge the single updated entry the POST returns.
      channels = { ...channels, [platform]: res[platform] }
    } catch (e) {
      err = String(e.message || e)
    }
    busy = ''
  }

  // Save the touched token field(s) for a platform. Only fields the user typed into
  // (present as a key in `drafts`) are sent; an empty string clears that token. After
  // save we merge the returned entry (which may flip to connected / error / waiting)
  // and drop the drafts so placeholders reflect the fresh token_present state.
  async function saveTokens(pf) {
    if (busy) return
    const tokens = {}
    for (const f of pf.fields) {
      if (f.env in drafts) tokens[f.env] = drafts[f.env]
    }
    if (!Object.keys(tokens).length) return
    busy = pf.id; err = ''
    try {
      const res = await api.channelTokens(pf.id, tokens)
      channels = { ...channels, [pf.id]: res[pf.id] }
      const next = { ...drafts }
      for (const f of pf.fields) delete next[f.env]
      drafts = next
    } catch (e) {
      err = String(e.message || e)
    }
    busy = ''
  }

  // Clear all token(s) for a platform (empty submit) — mirrors the "Clear" link on
  // the API-key rows, which clears without a confirm.
  async function clearTokens(pf) {
    if (busy) return
    busy = pf.id; err = ''
    const tokens = {}
    for (const f of pf.fields) tokens[f.env] = ''
    try {
      const res = await api.channelTokens(pf.id, tokens)
      channels = { ...channels, [pf.id]: res[pf.id] }
      const next = { ...drafts }
      for (const f of pf.fields) delete next[f.env]
      drafts = next
    } catch (e) {
      err = String(e.message || e)
    }
    busy = ''
  }

  // Status line for a platform, given its entry. Order matters: disabled → live
  // (active) → bound-without-token (a normal "waiting" state, even though the
  // backend records a no-token error for it, so it takes precedence over the raw
  // error string) → a genuine start error (bad token / network) → bound.
  function statusOf(id, c) {
    if (!c || c.profile == null) return { kind: 'off', text: 'disabled' }
    const name = profById[c.profile]?.name || c.profile
    if (c.active) return { kind: 'ok', text: `connected to ${name}` }
    if (!c.token_present) return { kind: 'wait', text: `assigned to ${name} — paste the bot token below`, name }
    if (c.error) return { kind: 'err', text: c.error }
    return { kind: 'wait', text: `assigned to ${name}`, name }
  }
</script>

<div class="channels">
  <p class="chintro">Channels are shared across the install — each connects to one profile.</p>
  {#if err}<p class="cherr">{err}</p>{/if}
  {#if !channels}
    <p class="chmuted">Loading…</p>
  {:else}
    {#each PLATFORMS as pf}
      {@const c = channels[pf.id]}
      {@const st = statusOf(pf.id, c)}
      {@const boundAccent = c && c.profile != null ? profById[c.profile]?.accent : null}
      <div class="chrow">
        <div class="chtop">
          <div class="chmeta">
            <div class="chname">
              <span
                class="chdot"
                class:on={!!boundAccent}
                style={boundAccent ? `--dot:${boundAccent}` : ''}
              ></span>
              {pf.label}
            </div>
            <div class="chstatus {st.kind}">
              {#if st.kind === 'ok'}<Icon name="check" size={13} />{/if}
              {#if st.kind === 'err'}<Icon name="alert-triangle" size={13} />{/if}
              <span>{st.text}</span>
            </div>
          </div>
          <select
            class="chpick"
            value={c?.profile ?? ''}
            disabled={busy === pf.id}
            onchange={(e) => bind(pf.id, e.target.value)}
          >
            <option value="">Disabled</option>
            {#each list as p (p.id)}
              <option value={p.id}>{p.name}</option>
            {/each}
          </select>
        </div>

        <div class="chtokens">
          {#each pf.fields as f}
            <div class="chtokrow">
              {#if f.label}<span class="chtoklab">{f.label}</span>{/if}
              <input
                type="password"
                class="chtokinput"
                placeholder={c?.token_present ? 'token set — paste to replace' : 'paste token…'}
                bind:value={drafts[f.env]}
                disabled={busy === pf.id}
                onkeydown={(e) => { if (e.key === 'Enter') saveTokens(pf) }}
              />
            </div>
          {/each}
          <div class="chtokact">
            <button class="chsave" disabled={busy === pf.id} onclick={() => saveTokens(pf)}>Save</button>
            {#if c?.token_present}
              <button class="chclear" disabled={busy === pf.id} onclick={() => clearTokens(pf)}>Clear</button>
            {/if}
          </div>
        </div>
      </div>
    {/each}
  {/if}
</div>

<style>
  .channels { display: flex; flex-direction: column; gap: 2px; }
  .chintro { font-size: var(--text-xs); color: var(--text-muted); margin: 0 0 8px; line-height: var(--leading-snug); }
  .chrow {
    display: flex; flex-direction: column; gap: 8px; padding: 9px 4px;
    border-bottom: 1px solid var(--line);
  }
  .chrow:last-of-type { border-bottom: none; }
  .chtop { display: flex; align-items: center; gap: 12px; }
  .chmeta { flex: 1; min-width: 0; }
  .chname { display: flex; align-items: center; gap: 8px; font-size: var(--text-sm); font-weight: var(--fw-semibold); }
  .chdot {
    width: 10px; height: 10px; flex: none; border-radius: var(--radius-pill);
    background: var(--dot, var(--line)); box-shadow: inset 0 0 0 1px var(--line);
  }
  .chdot.on { box-shadow: none; }
  .chstatus {
    display: flex; align-items: center; gap: 5px;
    font-size: var(--text-xs); color: var(--text-muted); margin-top: 2px;
    line-height: var(--leading-snug);
  }
  .chstatus.ok { color: var(--success, #2f8c44); }
  .chstatus.err { color: var(--warning, #e0b400); }
  .chstatus :global(svg) { flex: none; }

  /* Profile picker — matches the form controls in Profiles/Settings. */
  .chpick {
    flex: none; border: 1px solid var(--line); border-radius: var(--radius-sm);
    padding: 7px 30px 7px 10px; background-color: var(--bg); color: var(--text);
    font: inherit; font-size: var(--text-sm); cursor: pointer;
  }
  .chpick:focus { outline: none; border-color: var(--accent); box-shadow: var(--focus-ring); }
  .chpick:disabled { opacity: .6; cursor: default; }

  /* Token inputs — sit under the picker/status line, mirroring the API-key rows. */
  .chtokens { display: flex; flex-direction: column; gap: 6px; }
  .chtokrow { display: flex; align-items: center; gap: 8px; }
  .chtoklab { flex: none; width: 64px; font-size: var(--text-xs); color: var(--text-muted); }
  .chtokinput {
    flex: 1; min-width: 0; border: 1px solid var(--line); border-radius: var(--radius-sm);
    padding: 7px 10px; background: var(--bg); color: var(--text);
    font: inherit; font-size: var(--text-sm);
  }
  .chtokinput:focus { outline: none; border-color: var(--accent); box-shadow: var(--focus-ring); }
  .chtokinput:disabled { opacity: .6; }
  .chtokact { display: flex; align-items: center; gap: 8px; }
  .chsave {
    border: 1px solid var(--line); border-radius: var(--radius-sm);
    padding: 6px 14px; background: var(--bg); color: var(--text);
    font: inherit; font-size: var(--text-sm); cursor: pointer;
  }
  .chsave:hover:not(:disabled) { border-color: var(--accent); }
  .chsave:disabled { opacity: .6; cursor: default; }
  .chclear {
    border: none; background: none; padding: 6px 4px; cursor: pointer;
    font: inherit; font-size: var(--text-sm); color: var(--text-muted);
  }
  .chclear:hover:not(:disabled) { color: var(--danger, var(--danger)); }
  .chclear:disabled { opacity: .6; cursor: default; }

  .chmuted { font-size: var(--text-sm); color: var(--text-muted); margin: 0; }
  .cherr { font-size: var(--text-sm); color: var(--danger, var(--danger)); margin: 0 0 6px; }
</style>
