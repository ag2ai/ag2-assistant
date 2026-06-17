<script>
  import { go } from '../../router.js'
  let { item } = $props()
</script>

<div class="card">
  <div class="k">{item.scheduled ? '⏰ Task scheduled' : '✓ Task started'}</div>
  <div class="t">{item.title || 'Task'}</div>
  <button class="open" onclick={() => go('/t/' + item.taskId)}>Open task →</button>
</div>
