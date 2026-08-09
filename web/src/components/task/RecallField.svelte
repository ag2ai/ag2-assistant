<script lang="ts">
  // Maps the three UI options ⇄ the stored recall_depth number (0 none, -1 all,
  // else a count). The count input shows only for "Last N runs".

  type Choice = 'none' | 'last' | 'all'
  type Props = { depth: number }

  let { depth = $bindable() }: Props = $props()

  const CHOICES: { id: Choice; label: string }[] = [
    { id: 'none', label: "Don't look back" },
    { id: 'last', label: 'Last N runs' },
    { id: 'all', label: 'All previous runs' },
  ]

  const detect = (d: number): Choice => (d === 0 ? 'none' : d < 0 ? 'all' : 'last')

  let choice = $state(detect(depth))
  let n = $state(depth > 0 ? depth : 5) // the count "Last N runs" starts from

  function apply() {
    if (choice === 'none') depth = 0
    else if (choice === 'all') depth = -1
    else depth = Math.max(1, Math.floor(n || 1))
  }
</script>

<div class="recallfield">
  <select class="chpick" bind:value={choice} onchange={apply} aria-label="Earlier runs this task sees">
    {#each CHOICES as c}<option value={c.id}>{c.label}</option>{/each}
  </select>
  {#if choice === 'last'}
    <input type="number" min="1" step="1" bind:value={n} onchange={apply} aria-label="How many runs" />
  {/if}
</div>
<p class="recallhint">
  {#if choice === 'none'}
    Each run starts fresh, with no knowledge of earlier runs.
  {:else}
    Each run sees what earlier runs produced, so it can avoid repeating them.
  {/if}
</p>

<style>
  /* Same field-control recipe as ScheduleField (the sibling select+input row in this
     form), kept local for the same reason: no .tpfield ancestor to inherit from. */
  .recallfield { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .recallfield select, .recallfield input {
    font: inherit; font-size: 13px; color: var(--ink);
    min-width: 0; border: 1px solid var(--line); border-radius: 8px; padding: 7px 9px;
    background-color: var(--bg);
  }
  .recallfield select { flex: none; padding-right: 30px; } /* clears the shared chevron */
  .recallfield input { width: 90px; }
  .recallfield select:focus, .recallfield input:focus {
    outline: none; border-color: var(--accent); box-shadow: var(--focus-ring);
  }
  .recallhint { margin: 6px 0 0; font-size: 12px; color: var(--muted); }
</style>
