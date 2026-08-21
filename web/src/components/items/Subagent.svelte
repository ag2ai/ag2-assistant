<script lang="ts">
  import Item from '../Item.svelte'
  import { fmtStamp } from '../../lib/time.ts'
  import type { ThreadItem } from '../../schemas/events.ts'
  import { m } from '../../paraglide/messages.js'

  type Props = { item: Extract<ThreadItem, { kind: 'subagent' }> }
  let { item }: Props = $props()
  const label = $derived(`${(item.agent || 'subagent').replace(/[-_]/g, ' ')} ${m.thread_subagent_suffix()}`)
  const kids = $derived(item.items || [])
  const running = $derived((item.status || 'running') === 'running')
  // The status value drives the class name; only its label localizes.
  const STATUS_LABEL: Record<string, () => string> = {
    running: m.status_running, completed: m.status_completed,
    failed: m.status_failed, cancelled: m.status_cancelled,
  }
  const statusLabel = $derived.by(() => {
    const s = item.status || 'running'
    return (STATUS_LABEL[s] ?? (() => s))()
  })

  // Auto-open while the subagent works, auto-collapse to a summary when it's
  // done — unless the user has manually toggled, in which case respect that.
  let userToggled = $state(false)
  let open = $state(true)
  $effect(() => {
    if (!userToggled) open = running
  })
  const toggle = () => { userToggled = true; open = !open }
</script>

<div class={`subagent ${item.status || 'running'}`}>
  <div class="srow" onclick={toggle} role="button" tabindex="0"
       onkeydown={(e) => (e.key === 'Enter' || e.key === ' ') && toggle()}>
    <span class="sdot"></span>
    <span class="sname">{label}</span>
    {#if kids.length}<span class="scount">{kids.length}</span>{/if}
    {#if item.at}<span class="itemtime">{fmtStamp(item.at)}</span>{/if}
    <span class="sstatus">{statusLabel}</span>
    <span class="scaret">{open ? '▾' : '▸'}</span>
  </div>
  {#if item.objective}<div class="sobj">{item.objective}</div>{/if}
  {#if open}
    {#if kids.length}
      <div class="skids">
        {#each kids as k (k.id)}<Item item={k} />{/each}
      </div>
    {/if}
    <!-- The worker's result is the deliverable (shown in the thread's Deliverable
         produced item); keep it here only behind the expand, not reprinted inline. -->
    {#if item.result}<div class="sres">{item.result}</div>{/if}
  {/if}
  {#if item.error}<div class="serr">{item.error}</div>{/if}
</div>
