<script>
  import { taskPanel } from '../../store.js'
  import { api } from '../../transport/api.js'

  const TERMINAL = ['completed', 'failed', 'cancelled']

  async function cancel() {
    if ($taskPanel) { await api.cancelTask($taskPanel.id); }
  }
  async function archive() {
    if ($taskPanel) { await api.archiveTask($taskPanel.id, !$taskPanel.archived) }
  }
</script>

{#if $taskPanel}
  <div class="panel">
    <div class="ptitle">Task</div>
    {#if $taskPanel.objective}<div>{$taskPanel.objective}</div>{/if}
    {#if $taskPanel.scheduled_for}
      <div class="note" style="text-align:left;margin:6px 0">⏰ {$taskPanel.scheduled_for}{$taskPanel.recurrence ? ' · repeats ' + $taskPanel.recurrence : ' (one-off)'}</div>
    {/if}
    {#if $taskPanel.run_of}<div class="note" style="text-align:left">↻ One run of a recurring task.</div>{/if}

    {#each ($taskPanel.deliverables || []) as d}
      <div class="deliv">
        <div class="d">{d.description} [{d.status}]</div>
        {#if d.asset}<div>{(d.asset || '').slice(0, 280)}{(d.asset || '').length > 280 ? '…' : ''}</div>{/if}
      </div>
    {/each}

    {#each ($taskPanel.children || []) as c}
      <div class="child"><span class="badge">{c.status}</span> {c.title}</div>
    {/each}

    <div style="margin-top:10px;display:flex;gap:8px">
      {#if !TERMINAL.includes($taskPanel.status)}
        <button class="open" onclick={cancel}>Cancel task</button>
      {/if}
      {#if $taskPanel.archived || TERMINAL.includes($taskPanel.status)}
        <button class="open" onclick={archive}>{$taskPanel.archived ? 'Unarchive' : 'Archive'}</button>
      {/if}
    </div>
  </div>
{/if}
