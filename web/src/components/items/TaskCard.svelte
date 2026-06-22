<script>
  import { go } from '../../router.js'
  import Icon from '../Icon.svelte'
  let { item } = $props()
</script>

<div class="card">
  <div class="k"><Icon name={item.scheduled ? 'clock' : 'check'} size={14} /> {item.scheduled ? 'Task scheduled' : 'Task started'}</div>
  <div class="t">{item.title || 'Task'}</div>
  <button class="open" onclick={() => go('/t/' + item.taskId)}>Open task →</button>
</div>
