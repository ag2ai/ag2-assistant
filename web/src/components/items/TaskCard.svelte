<script lang="ts">
  import { go } from '../../router.ts'
  import Icon from '../Icon.svelte'
  import { fmtStamp } from '../../lib/time.ts'
  import type { ThreadItem } from '../../schemas/events.ts'
  import { m } from '../../paraglide/messages.js'

  type Props = { item: Extract<ThreadItem, { kind: 'taskcard' }> }
  let { item }: Props = $props()
</script>

<div class="card">
  <div class="k"><Icon name={item.scheduled ? 'clock' : 'check'} size={14} /> {item.scheduled ? m.thread_task_scheduled() : m.thread_task_started()}{#if item.at}<span class="itemtime">{fmtStamp(item.at)}</span>{/if}</div>
  <div class="t">{item.title || m.thread_task()}</div>
  <button class="open" onclick={() => go('/t/' + item.taskId)}>{m.thread_open_task()}</button>
</div>
