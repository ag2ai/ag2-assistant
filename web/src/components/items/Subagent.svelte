<script>
  import Item from '../Item.svelte'

  let { item } = $props()
  const label = $derived(`${(item.agent || 'subagent').replace(/[-_]/g, ' ')} (subagent)`)
  const kids = $derived(item.items || [])
  const running = $derived((item.status || 'running') === 'running')

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
    <span class="sstatus">{item.status || 'running'}</span>
    <span class="scaret">{open ? '▾' : '▸'}</span>
  </div>
  {#if item.objective}<div class="sobj">{item.objective}</div>{/if}
  {#if open && kids.length}
    <div class="skids">
      {#each kids as k (k.id)}<Item item={k} />{/each}
    </div>
  {/if}
  {#if item.result}<div class="sres">{item.result}</div>{/if}
  {#if item.error}<div class="serr">{item.error}</div>{/if}
</div>
