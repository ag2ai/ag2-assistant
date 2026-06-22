<script>
  import { taskPanel, viewer } from '../../store.js'
  import { api } from '../../transport/api.js'
  import Markdown from '../Markdown.svelte'
  import Icon from '../Icon.svelte'

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
      <div class="note" style="text-align:left;margin:6px 0;display:inline-flex;align-items:center;gap:6px"><Icon name="clock" size={13} /> {$taskPanel.scheduled_for}{$taskPanel.recurrence ? ' · repeats ' + $taskPanel.recurrence : ' (one-off)'}</div>
    {/if}
    {#if $taskPanel.run_of}<div class="note" style="text-align:left;display:inline-flex;align-items:center;gap:6px"><Icon name="zap" size={13} /> One run of a recurring task.</div>{/if}

    {#each ($taskPanel.deliverables || []) as d}
      <div class="deliv">
        <div class="d">{d.description} [{d.status}]</div>
        {#if d.asset}
          <div class="asset"><Markdown text={d.asset} /></div>
          <button class="viewbtn" onclick={() => ($viewer = { title: d.description, text: d.asset })}>View full →</button>
        {/if}
      </div>
    {/each}

    {#each ($taskPanel.children || []) as c}
      <div class="child"><span class="badge">{c.status}</span> {c.title}</div>
    {/each}

    <div style="margin-top:10px;display:flex;gap:8px">
      {#if !TERMINAL.includes($taskPanel.status)}
        <button class="open" onclick={cancel}><Icon name="x" size={14} /> Cancel task</button>
      {/if}
      {#if $taskPanel.archived || TERMINAL.includes($taskPanel.status)}
        <button class="open" onclick={archive}><Icon name="folder" size={14} /> {$taskPanel.archived ? 'Unarchive' : 'Archive'}</button>
      {/if}
    </div>
  </div>
{/if}
