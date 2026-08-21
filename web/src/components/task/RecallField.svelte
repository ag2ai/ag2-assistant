<script lang="ts">
  // Maps the three UI options ⇄ the stored recall_depth number (0 none, -1 all,
  // else a count). The count input shows only for "Last N runs".

  import { recallCount } from '../../lib/taskEdit.ts'
  import { m } from '../../paraglide/messages.js'

  type Choice = 'none' | 'last' | 'all'
  type Props = { depth: number }

  let { depth = $bindable() }: Props = $props()

  // The choice id maps to the stored depth; only its label localizes.
  const CHOICES: { id: Choice; label: () => string }[] = [
    { id: 'none', label: m.task_recall_none },
    { id: 'last', label: m.task_recall_last },
    { id: 'all', label: m.task_recall_all },
  ]

  // Anything that isn't a positive count is no recall, so a missing depth opens the
  // form on "No recall" rather than silently saving a count the user never chose.
  const detect = (d: number): Choice => (!d ? 'none' : d < 0 ? 'all' : 'last')

  let choice = $state(detect(depth))
  let n = $state(depth > 0 ? depth : 5) // the count "Last N runs" starts from

  function apply() {
    if (choice === 'none') depth = 0
    else if (choice === 'all') depth = -1
    else depth = n = recallCount(n) // write back, so the box shows what Save sends
  }
</script>

<div class="recallfield">
  <select class="chpick" bind:value={choice} onchange={apply} aria-label={m.task_recall_aria()}>
    {#each CHOICES as c}<option value={c.id}>{c.label()}</option>{/each}
  </select>
  {#if choice === 'last'}
    <input type="number" min="1" step="1" bind:value={n} onchange={apply} aria-label={m.task_recall_count_aria()} />
  {/if}
</div>
<p class="recallhint">
  {#if choice === 'none'}
    {m.task_recall_hint_none()}
  {:else}
    {m.task_recall_hint_some()}
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
