<script>
  import { go } from '../../router.js'
  import Icon from '../Icon.svelte'
  import { fmtStamp } from '../../lib/time.js'
  let { item } = $props()
</script>

<div class="card">
  <div class="k"><Icon name={item.scheduled ? 'clock' : 'check'} size={14} /> {item.scheduled ? 'Task scheduled' : 'Task started'}{#if item.at}<span class="itemtime">{fmtStamp(item.at)}</span>{/if}</div>
  <div class="t">{item.title || 'Task'}</div>
  <button class="open" onclick={() => go('/t/' + item.taskId)}>Open task</button>
</div>
