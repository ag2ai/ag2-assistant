<script lang="ts">
  // The one place a token is ever typed: picking a card from the Add grid opens this,
  // and connecting lands in the new connection's settings. A name is asked for only
  // where a platform can be connected more than once ("Telegram" tells you nothing
  // when there are three of them); Connect stays refused until every token is filled.
  import { nextConnectionName } from '../../lib/integrations.ts'
  import type { Integration } from '../../lib/integrations.ts'
  import IntegrationMark from './IntegrationMark.svelte'
  import { errText } from '../../lib/errors.ts'
  import type { Connection } from '../../schemas/index.ts'

  // entry: a CATALOG entry. connections: the current list, for the default name and
  // the "already connected" count. onConnect(name, tokens) does the write and may
  // throw — its message is shown here rather than swallowed.
  type Props = {
    entry: Integration
    connections?: Connection[]
    onConnect: (name: string, tokens: Record<string, string>) => Promise<void>
    onCancel: () => void
  }
  let { entry, connections = [], onConnect, onCancel }: Props = $props()

  const existing = $derived(connections.filter((c) => c.platform === entry.id).length)

  // The name the server would pick for a blank one, shown before it is created and
  // replaced the moment the user types over it.
  let typed = $state<string | null>(null)
  const name = $derived(typed ?? nextConnectionName(connections, entry))
  let tokens = $state<Record<string, string>>({})
  let busy = $state(false)
  let err = $state('')

  const ready = $derived(entry.fields.every((f) => (tokens[f.env] || '').trim()))

  async function connect() {
    if (!ready || busy) return
    err = ''; busy = true
    try {
      await onConnect(name.trim(), Object.fromEntries(
        entry.fields.map((f) => [f.env, (tokens[f.env] || '').trim()]),
      ))
    } catch (e) {
      err = errText(e)
      busy = false
    }
  }
</script>

<div class="cnhead">
  <IntegrationMark platform={entry.id} name={entry.label} />
  <div class="cnheadmeta">
    <div class="cnheadname">Connect {entry.label}</div>
    <span class="cnhint">{entry.setup}</span>
  </div>
</div>

<div class="cnform">
  {#if entry.multiple}
    <div class="keyrow">
      <span class="kp">Name</span>
      <input
        value={name} placeholder={entry.label} disabled={busy} aria-label="Connection name"
        oninput={(e) => (typed = e.currentTarget.value)}
        onkeydown={(e) => { if (e.key === 'Enter') connect() }}
      />
    </div>
    <p class="cnhint">
      Yours, not the platform's — it is how this connection is listed. Rename it any time.
      {#if existing}You already have {existing} {entry.label} {existing === 1 ? 'connection' : 'connections'}.{/if}
    </p>
  {/if}

  {#each entry.fields as f (f.env)}
    <div class="keyrow">
      <span class="kp">{f.label}</span>
      <input
        type="password" placeholder={f.placeholder} disabled={busy}
        aria-label={f.label} bind:value={tokens[f.env]}
        onkeydown={(e) => { if (e.key === 'Enter') connect() }}
      />
    </div>
  {/each}
  <p class="cnhint">Written to the secrets store and never shown to the browser again.</p>

  {#if err}<p class="cnerr">{err}</p>{/if}

  <div class="keyrow">
    <button class="open primary" disabled={!ready || busy} onclick={connect}>
      {busy ? 'Connecting…' : 'Connect'}
    </button>
    <button class="open" disabled={busy} onclick={onCancel}>Cancel</button>
  </div>
</div>
