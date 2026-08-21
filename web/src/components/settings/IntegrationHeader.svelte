<script lang="ts">
  // The header of one integration's settings: its mark, its name — renamed in place
  // through the pencil when `onRename` is given — an optional platform tag, and the
  // same status line the list row shows. Shared by every detail pane (channel
  // connections, Google, GitHub) so the three read as one page.
  import Icon from '../Icon.svelte'
  import IntegrationStatus from './IntegrationStatus.svelte'
  import IntegrationMark from './IntegrationMark.svelte'
  import { m } from '../../paraglide/messages.js'
  import type { IntegrationStatus as Status } from '../../lib/integrations.ts'

  // onRename: async (name) => boolean — true when the rename was accepted. Absent
  // for the single-instance integrations, which have no name of their own.
  // platform: the CATALOG id whose mark is drawn — the platform's, not the
  // connection's, so two differently-named Telegram bots still read as Telegram.
  type Props = {
    label: string
    platform: string
    tag?: string
    status: Status
    onRename?: ((name: string) => Promise<boolean>) | null
    busy?: boolean
  }
  let { label, platform, tag = '', status, onRename = null, busy = false }: Props = $props()

  let renaming = $state(false)
  let draft = $state('')

  function focusSelect(node: HTMLInputElement) { node.focus(); node.select() }

  function start() {
    draft = label
    renaming = true
  }

  async function commit() {
    const name = draft.trim()
    if (!name || name === label) { renaming = false; return }
    if (await onRename?.(name)) renaming = false
  }
</script>

<div class="cnhead">
  <IntegrationMark {platform} name={label} />
  <div class="cnheadmeta">
    {#if renaming}
      <input
        class="cnrename" aria-label={m.integrations_connection_name_aria()} bind:value={draft} disabled={busy}
        use:focusSelect
        onkeydown={(e) => {
          if (e.key === 'Enter') commit()
          if (e.key === 'Escape') renaming = false
        }}
      />
      <div class="cnheadact">
        <button class="open primary" disabled={busy || !draft.trim()} onclick={commit}>{m.action_save()}</button>
        <button class="open" disabled={busy} onclick={() => (renaming = false)}>{m.action_cancel()}</button>
      </div>
    {:else}
      <div class="cnheadname">
        {label}
        {#if onRename}
          <button class="iconbtn sm" aria-label={m.action_rename()} disabled={busy} onclick={start}>
            <Icon name="pencil" size={12} />
          </button>
        {/if}
        {#if tag}<span class="cntag">{tag}</span>{/if}
      </div>
      <IntegrationStatus {status} />
    {/if}
  </div>
</div>
