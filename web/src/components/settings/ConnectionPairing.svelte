<script lang="ts">
  // Who this connection answers. An account not paired to it is served nothing at all
  // (ADR 0021), and the grant is to this connection alone — being paired to the work
  // bot is no access to the personal one. A live connection with an empty roster looks
  // healthy and answers nobody, so an empty roster says so.
  import Icon from '../Icon.svelte'
  import { api } from '../../transport/api/index.ts'
  import { byId } from '../../lib/integrations.ts'
  import { errText } from '../../lib/errors.ts'
  import { m } from '../../paraglide/messages.js'
  import type {
    Connection, ConnectionPairing, PairedAccount, PairingCode,
  } from '../../schemas/index.ts'

  // connection: one entry from GET /api/connections. reload: re-fetch the list — the
  // header's status line is derived from the paired-account count, so anything that
  // changes the roster calls it.
  type Props = { connection: Connection; reload: () => Promise<void> }
  let { connection, reload }: Props = $props()

  // The server's view: {accounts:[{key, account_id, handle, pending}],
  // code:{code, expires_at}|null}. Every route below returns it whole.
  let view = $state<ConnectionPairing | null>(null)
  let busy = $state(false)
  let err = $state('')
  let adding = $state(false) // the pairing form, folded until asked for
  let draft = $state('')
  // the account key awaiting confirmation on its own row
  let revoking = $state<string | null>(null)

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
      .catch((e) => { err = errText(e) })
  })

  // Every mutation returns the whole view, so each is "run it, keep what came back".
  // `refresh` reloads the list for the ones that move the paired-account count.
  async function run(fn: () => Promise<ConnectionPairing>, refresh = true) {
    if (busy) return false
    err = ''; busy = true
    let ok = false
    try {
      view = await fn()
      if (refresh) await reload()
      ok = true
    } catch (e) { err = errText(e) }
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

  async function revoke(key: string) {
    if (await run(() => api.connectionUnpair(cid, key))) revoking = null
  }

  const issueCode = () =>
    run(async () => { await api.connectionPairingCode(cid); return api.connectionPairing(cid) }, false)

  // A pinned account by its id and the handle it came in under; a pending invitation
  // by the handle it awaits.
  const label = (a: PairedAccount) =>
    a.pending ? `@${a.handle}` : a.handle ? `${a.account_id} (@${a.handle})` : a.account_id

  function expiry(code: PairingCode) {
    const mins = Math.max(0, Math.round((code.expires_at * 1000 - Date.now()) / 60000))
    return mins ? m.integrations_code_expires({ count: mins }) : m.integrations_code_expiring_now()
  }
</script>

<div class="setgroup cnsechead">
  <span>{m.integrations_who_it_answers()}</span>
  <button class="open" disabled={busy} onclick={issueCode}>
    {view?.code ? m.integrations_new_pairing_code() : m.integrations_pairing_code()}
  </button>
</div>
<p class="setsub">
  {m.integrations_pairing_lead({ name: connection.name })}
</p>

{#if err}<p class="cnerr">{err}</p>{/if}

{#if view}
  {#if view.code}
    <!-- One localizable sentence: the code rides as a parameter (word order differs
         per language), so the old inline <b> styling of the code is traded for text. -->
    <p class="cnhint">
      {m.integrations_code_send({ code: view.code.code, expiry: expiry(view.code) })}
    </p>
  {/if}

  {#if !view.accounts.length}
    <p class="cnwarn">{m.integrations_nobody_paired()}</p>
  {:else}
    <ul class="cnlist">
      {#each view.accounts as a (a.key)}
        <li class="cnitem">
          <span class="cnid">{label(a)}</span>
          {#if a.pending}
            <span class="cntag" title={m.integrations_pending_title()}>{m.integrations_pending()}</span>
          {/if}
          <!-- Revoking is not undoable from here — the account has to be paired again,
               and a pending invitation cannot be re-offered without a new code. So it
               confirms on its own row, worded for the case it is. -->
          {#if revoking === a.key}
            <span class="cnwarn cnpush">
              {a.pending ? m.integrations_cancel_invitation_q() : m.integrations_stop_answering_q()}
            </span>
            <button class="open danger" disabled={busy} onclick={() => revoke(a.key)}>
              {a.pending ? m.integrations_cancel_invitation() : m.integrations_revoke()}
            </button>
            <button class="open" disabled={busy} onclick={() => (revoking = null)}>{m.integrations_keep()}</button>
          {:else}
            <button
              class="open cnpush" disabled={busy}
              onclick={() => { revoking = a.key; err = '' }}
            >{m.integrations_revoke()}</button>
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
        placeholder={entry?.handles ? m.integrations_account_placeholder_handle() : m.integrations_account_placeholder()}
        aria-label={m.integrations_pair_account()} disabled={busy} bind:value={draft}
        onkeydown={(e) => {
          if (e.key === 'Enter') pair()
          if (e.key === 'Escape') { adding = false; draft = '' }
        }}
      />
      <button class="open primary" disabled={busy || !draft.trim()} onclick={pair}>
        {busy ? m.integrations_pairing_busy() : m.integrations_pair()}
      </button>
      <button class="open" disabled={busy} onclick={() => { adding = false; draft = '' }}>{m.action_cancel()}</button>
    </div>
  {:else}
    <button class="addbtn" disabled={busy} onclick={() => { adding = true; err = '' }}>
      <Icon name="plus" size={13} /> {m.integrations_pair_account()}
    </button>
  {/if}
{/if}
