<script>
  // Settings → "Channels" section (§4.5). Channels are an INSTALL-LEVEL resource and
  // are never owned by a profile (ADR 0019): each platform (Telegram / Discord /
  // Slack) connects once for the whole install as soon as its token is set. The row's
  // picker chooses that channel's DEFAULT PROFILE — where its conversations land when
  // nothing else has been chosen — and POSTs to the GLOBAL /api/channels/default,
  // applying the returned entry. It never starts or stops the connection. State comes
  // from the GLOBAL GET /api/channels. The bot token(s) are edited inline here (POST
  // /api/channels/token) — stored in the global secrets store, mirroring the API-key
  // inputs in Settings; values are never echoed back. Each platform's GROUP Peers are
  // listed too: a group's profile is pinned and re-pointed only here (GET/POST
  // /api/channels/{platform}/groups*), from the profiles exposed to the group surface.
  import { onMount } from 'svelte'
  import { profiles } from '../store.js'
  import { api } from '../transport/api.js'
  import Icon from './Icon.svelte'

  // Token env var(s) per platform — mirrors the backend CHANNEL_TOKEN_ENVS. Slack
  // needs two (bot + app); the others a single token. `label` is null for a
  // single-field platform (no need to name it) and set when there's more than one.
  // `handles` mirrors the backend HANDLE_PLATFORMS: Slack messages carry no handle,
  // so an invitation by handle could never be presented there.
  const PLATFORMS = [
    { id: 'telegram', label: 'Telegram', handles: true, fields: [{ env: 'TELEGRAM_BOT_TOKEN', label: null }] },
    { id: 'discord', label: 'Discord', handles: true, fields: [{ env: 'DISCORD_BOT_TOKEN', label: null }] },
    { id: 'slack', label: 'Slack', handles: false, fields: [{ env: 'SLACK_BOT_TOKEN', label: 'Bot token' }, { env: 'SLACK_APP_TOKEN', label: 'App token' }] },
  ]

  // {telegram|discord|slack: {default_profile: pid|null, token_present, active, error,
  // paired_accounts}}
  let channels = $state(null)
  let busy = $state('') // platform id currently saving (disables its row)
  let err = $state('')
  // Per-env token draft inputs (ENV_NAME -> string). Emptied after a successful save.
  let drafts = $state({})
  // Paired accounts per platform: {platform: {accounts:[…], code:{…}|null}}. A channel
  // serves nobody who is not on this list (ADR 0021), so it sits with the token.
  let pairing = $state({})
  // Draft "numeric id or @handle" input per platform.
  let pairDrafts = $state({})
  // Group Peers per platform: {platform: {groups:[{chat_id, profile}], profiles:[…]}}. A
  // group's profile is pinned (/profile is refused there), so this row is where it moves.
  let groups = $state({})

  // Unarchived profiles, for the pickers + name/accent lookup.
  const list = $derived(($profiles.list || []).filter((p) => !p.archived))
  const profById = $derived(Object.fromEntries(list.map((p) => [p.id, p])))

  async function load() {
    try {
      channels = await api.channels()
      const entries = await Promise.all(
        PLATFORMS.map(async (pf) => [pf.id, await api.channelPairing(pf.id)]),
      )
      pairing = Object.fromEntries(entries)
      const grouped = await Promise.all(
        PLATFORMS.map(async (pf) => [pf.id, await api.channelGroups(pf.id)]),
      )
      groups = Object.fromEntries(grouped)
    } catch (e) { err = String(e.message || e) }
  }
  onMount(load)

  // Every pairing route returns the platform's whole {accounts, code} view, so each
  // mutation is "run it, keep what came back" — and the channel row is refreshed too,
  // because its "nobody paired" status is derived from the same count.
  async function pair(platform, run) {
    if (busy) return
    busy = platform; err = ''
    try {
      pairing = { ...pairing, [platform]: await run() }
      channels = { ...channels, [platform]: (await api.channels())[platform] }
    } catch (e) {
      err = String(e.message || e)
    }
    busy = ''
  }

  function addAccount(platform) {
    const value = (pairDrafts[platform] || '').trim()
    if (!value) return
    pair(platform, async () => {
      const view = await api.channelPair(platform, value)
      pairDrafts = { ...pairDrafts, [platform]: '' }
      return view
    })
  }

  const revoke = (platform, key) => pair(platform, () => api.channelUnpair(platform, key))

  function issueCode(platform) {
    pair(platform, async () => {
      await api.channelPairingCode(platform)
      return api.channelPairing(platform)
    })
  }

  // How an entry reads in the list: a pinned account by its id (with the handle it
  // came in under, when it had one), a pending invitation by the handle it awaits.
  function accountLabel(a) {
    if (a.pending) return `@${a.handle}`
    return a.handle ? `${a.account_id} (@${a.handle})` : a.account_id
  }

  function codeExpiry(code) {
    const mins = Math.max(0, Math.round((code.expires_at * 1000 - Date.now()) / 60000))
    return mins ? `expires in ${mins} min` : 'expiring now'
  }

  // Re-point one group at another profile. The route returns the platform's whole group
  // view, so this is "run it, keep what came back" like the pairing mutations.
  async function setGroupProfile(platform, chatId, profile) {
    if (busy || !profile) return
    busy = platform; err = ''
    try {
      groups = { ...groups, [platform]: await api.channelGroupProfile(platform, chatId, profile) }
    } catch (e) {
      err = String(e.message || e)
    }
    busy = ''
  }

  async function setDefault(platform, profile) {
    if (busy) return
    busy = platform; err = ''
    // Empty <select> value means "No default" → cleared.
    const pid = profile || null
    try {
      const res = await api.channelDefault(platform, pid)
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

  // Status line for a platform, given its entry. Order matters: no token (the resting
  // state of a fresh install, not an error) → a genuine start error (bad token /
  // network) → live, with or without somewhere to send its messages.
  function statusOf(id, c) {
    if (!c || !c.token_present) return { kind: 'off', text: 'not connected — paste the bot token below' }
    if (c.error) return { kind: 'err', text: c.error }
    if (!c.active) return { kind: 'wait', text: 'not connected' }
    // A live channel with nobody paired looks healthy and answers nobody — say
    // which of the two it is before anything else about where messages land.
    if (!c.paired_accounts) return { kind: 'err', text: 'connected — but nobody is paired, so it answers nobody' }
    if (c.default_profile == null) return { kind: 'wait', text: 'connected — pick a default profile' }
    const name = profById[c.default_profile]?.name || c.default_profile
    return { kind: 'ok', text: `connected — messages go to ${name}` }
  }
</script>

<div class="channels">
  <p class="chintro">Channels are shared across the install. Each connects once; its default profile is where conversations land unless something else is chosen.</p>
  {#if err}<p class="cherr">{err}</p>{/if}
  {#if !channels}
    <p class="chmuted">Loading…</p>
  {:else}
    {#each PLATFORMS as pf}
      {@const c = channels[pf.id]}
      {@const st = statusOf(pf.id, c)}
      {@const defaultAccent = c && c.default_profile != null ? profById[c.default_profile]?.accent : null}
      {@const pr = pairing[pf.id]}
      {@const gp = groups[pf.id]}
      <div class="chrow">
        <div class="chtop">
          <div class="chmeta">
            <div class="chname">
              <span
                class="chdot"
                class:on={!!defaultAccent}
                style={defaultAccent ? `--dot:${defaultAccent}` : ''}
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
            aria-label="{pf.label} default profile"
            value={c?.default_profile ?? ''}
            disabled={busy === pf.id}
            onchange={(e) => setDefault(pf.id, e.target.value)}
          >
            <option value="">No default</option>
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

        <div class="chpair">
          <div class="chpairhead">
            <span class="chpairlab">Paired accounts</span>
            <button class="chclear" disabled={busy === pf.id} onclick={() => issueCode(pf.id)}>
              {pr?.code ? 'New code' : 'Pairing code'}
            </button>
          </div>

          {#if pr?.code}
            <p class="chcode">
              Send <b>{pr.code.code}</b> to the bot from the account you want to pair — {codeExpiry(pr.code)}.
            </p>
          {/if}

          {#if pr && !pr.accounts.length}
            <p class="chnone">Nobody yet. Until someone is paired, this channel answers nobody.</p>
          {:else if pr}
            <ul class="chaccs">
              {#each pr.accounts as a (a.key)}
                <li class="chacc">
                  <span class="chaccid">{accountLabel(a)}</span>
                  {#if a.pending}<span class="chpending">pending — pins to whoever answers to it first</span>{/if}
                  <button class="chclear" disabled={busy === pf.id} onclick={() => revoke(pf.id, a.key)}>Revoke</button>
                </li>
              {/each}
            </ul>
          {/if}

          <div class="chtokrow">
            <input
              class="chtokinput"
              placeholder={pf.handles ? 'numeric account id, or @handle' : 'numeric account id'}
              aria-label="Pair a {pf.label} account"
              bind:value={pairDrafts[pf.id]}
              disabled={busy === pf.id}
              onkeydown={(e) => { if (e.key === 'Enter') addAccount(pf.id) }}
            />
            <button class="chsave" disabled={busy === pf.id} onclick={() => addAccount(pf.id)}>Pair</button>
          </div>
        </div>

        {#if gp?.groups.length}
          <div class="chpair">
            <span class="chpairlab">Groups</span>
            <p class="chnone chgroupnote">A group's profile is set here, not from the group — anyone in it can read the answers.</p>
            <ul class="chaccs">
              {#each gp.groups as g (g.chat_id)}
                {@const reachable = gp.profiles.some((p) => p.id === g.profile)}
                <li class="chacc">
                  <span class="chaccid">{g.chat_id}</span>
                  {#if !reachable}<span class="chpending">not reachable from groups — pick another</span>{/if}
                  <select
                    class="chpick chgrouppick"
                    aria-label="Profile for group {g.chat_id}"
                    value={reachable ? g.profile : ''}
                    disabled={busy === pf.id}
                    onchange={(e) => setGroupProfile(pf.id, g.chat_id, e.target.value)}
                  >
                    {#if !reachable}<option value="">Pick a profile</option>{/if}
                    {#each gp.profiles as p (p.id)}
                      <option value={p.id}>{p.name}</option>
                    {/each}
                  </select>
                </li>
              {/each}
            </ul>
          </div>
        {/if}
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

  /* Paired accounts — who the channel answers at all. */
  .chpair { display: flex; flex-direction: column; gap: 6px; margin-top: 2px; }
  .chpairhead { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
  .chpairlab { font-size: var(--text-xs); color: var(--text-muted); }
  .chcode { margin: 0; font-size: var(--text-xs); color: var(--text-muted); line-height: var(--leading-snug); }
  .chcode b { font-family: var(--font-mono, monospace); font-size: var(--text-sm); color: var(--text); letter-spacing: .04em; }
  .chnone { margin: 0; font-size: var(--text-xs); color: var(--warning, #e0b400); line-height: var(--leading-snug); }
  .chaccs { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 2px; }
  .chacc { display: flex; align-items: center; gap: 8px; font-size: var(--text-sm); }
  .chaccid { font-family: var(--font-mono, monospace); font-size: var(--text-xs); }
  .chpending { flex: 1; min-width: 0; font-size: var(--text-xs); color: var(--text-muted); }
  .chacc .chclear { margin-left: auto; }

  /* Group Peers — a pinned profile, re-pointed only from here. */
  .chgroupnote { color: var(--text-muted); }
  .chgrouppick { margin-left: auto; padding: 5px 26px 5px 8px; font-size: var(--text-xs); }

  .chmuted { font-size: var(--text-sm); color: var(--text-muted); margin: 0; }
  .cherr { font-size: var(--text-sm); color: var(--danger, var(--danger)); margin: 0 0 6px; }
</style>
