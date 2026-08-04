<script>
  // Who this connection answers. An account not paired to it is served nothing at all
  // (ADR 0021), and the grant is to this connection alone — being paired to the work
  // bot is no access to the personal one. A live connection with an empty roster looks
  // healthy and answers nobody, so an empty roster says so.
  import Icon from '../Icon.svelte'
  import { api } from '../../transport/api.js'
  import { byId } from '../../lib/integrations.js'

  // connection: one entry from GET /api/connections. reload: re-fetch the list — the
  // header's status line is derived from the paired-account count, so anything that
  // changes the roster calls it.
  let { connection, reload } = $props()

  // The server's view: {accounts:[{key, account_id, handle, pending}],
  // code:{code, expires_at}|null}. Every route below returns it whole.
  let view = $state(null)
  let busy = $state(false)
  let err = $state('')
  let adding = $state(false) // the pairing form, folded until asked for
  let draft = $state('')
  let revoking = $state(null) // the account key awaiting confirmation on its own row

  const entry = $derived(byId[connection.platform])

  // Re-read only when the pane switches connections, not on every list reload.
  const cid = $derived(connection.id)
  $effect(() => {
    const id = cid
    adding = false
    draft = ''
    revoking = null
    api.connectionPairing(id)
      .then((v) => { if (id === cid) view = v })
      .catch((e) => { err = String(e.message || e) })
  })

  // Every mutation returns the whole view, so each is "run it, keep what came back".
  // `refresh` reloads the list for the ones that move the paired-account count.
  async function run(fn, refresh = true) {
    if (busy) return false
    err = ''; busy = true
    let ok = false
    try {
      view = await fn()
      if (refresh) await reload()
      ok = true
    } catch (e) { err = String(e.message || e) }
    busy = false
    return ok
  }

  async function pair() {
    const value = draft.trim()
    if (!value) return
    if (await run(() => api.connectionPair(cid, value))) {
      draft = ''
      adding = false
    }
  }

  async function revoke(key) {
    if (await run(() => api.connectionUnpair(cid, key))) revoking = null
  }

  const issueCode = () =>
    run(async () => { await api.connectionPairingCode(cid); return api.connectionPairing(cid) }, false)

  // A pinned account by its id and the handle it came in under; a pending invitation
  // by the handle it awaits.
  const label = (a) =>
    a.pending ? `@${a.handle}` : a.handle ? `${a.account_id} (@${a.handle})` : a.account_id

  function expiry(code) {
    const mins = Math.max(0, Math.round((code.expires_at * 1000 - Date.now()) / 60000))
    return mins ? `expires in ${mins} min` : 'expiring now'
  }
</script>

<div class="setgroup cnsechead">
  <span>Who it answers</span>
  <button class="open" disabled={busy} onclick={issueCode}>
    {view?.code ? 'New pairing code' : 'Pairing code'}
  </button>
</div>
<p class="setsub">
  Only these accounts reach {connection.name}. Pairing one here grants it nothing on any
  other connection, including another bot of the same kind.
</p>

{#if err}<p class="cnerr">{err}</p>{/if}

{#if view}
  {#if view.code}
    <p class="cnhint">
      Send <b class="cncode">{view.code.code}</b> to this bot from the account you want to
      pair — {expiry(view.code)}.
    </p>
  {/if}

  {#if !view.accounts.length}
    <p class="cnwarn">Nobody is paired — this connection answers nobody.</p>
  {:else}
    <ul class="cnlist">
      {#each view.accounts as a (a.key)}
        <li class="cnitem">
          <span class="cnid">{label(a)}</span>
          {#if a.pending}
            <span class="cntag" title="Pins to the first account that presents it">pending</span>
          {/if}
          <!-- Revoking is not undoable from here — the account has to be paired again,
               and a pending invitation cannot be re-offered without a new code. So it
               confirms on its own row, worded for the case it is. -->
          {#if revoking === a.key}
            <span class="cnwarn cnpush">
              {a.pending ? 'Cancel this invitation?' : 'Stop answering them?'}
            </span>
            <button class="open danger" disabled={busy} onclick={() => revoke(a.key)}>
              {a.pending ? 'Cancel invitation' : 'Revoke'}
            </button>
            <button class="open" disabled={busy} onclick={() => (revoking = null)}>Keep</button>
          {:else}
            <button
              class="open cnpush" disabled={busy}
              onclick={() => { revoking = a.key; err = '' }}
            >Revoke</button>
          {/if}
        </li>
      {/each}
    </ul>
  {/if}

  <!-- The pairing form is the rare action here — the list of who is already paired is
       what the user came to read — so it stays folded until asked for. -->
  {#if adding}
    <div class="keyrow">
      <input
        placeholder={entry.handles ? 'numeric account id, or @handle' : 'numeric account id'}
        aria-label="Pair an account" disabled={busy} bind:value={draft}
        onkeydown={(e) => {
          if (e.key === 'Enter') pair()
          if (e.key === 'Escape') { adding = false; draft = '' }
        }}
      />
      <button class="open primary" disabled={busy || !draft.trim()} onclick={pair}>
        {busy ? 'Pairing…' : 'Pair'}
      </button>
      <button class="open" disabled={busy} onclick={() => { adding = false; draft = '' }}>Cancel</button>
    </div>
  {:else}
    <button class="addbtn" disabled={busy} onclick={() => { adding = true; err = '' }}>
      <Icon name="plus" size={13} /> Pair an account
    </button>
  {/if}
{/if}
