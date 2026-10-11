<script lang="ts">
  import ToolChip from './ToolChip.svelte'
  import type { ThreadItem } from '../../schemas/events.ts'

  let { item }: { item: Extract<ThreadItem, { kind: 'tools' }> } = $props()
</script>

<div class="tools">
  {#each item.names as t (t.name)}
    <ToolChip label={t.name} runs={item.runs.filter(run => run.name === t.name)} />
  {/each}
</div>
