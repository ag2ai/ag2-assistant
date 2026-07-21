<script>
  // Compact header over a run's chat thread: which task, when, current status,
  // with a Stop for live runs and a link back to the task page.
  import { runInfo } from '../../store.js'
  import { api } from '../../transport/api.js'
  import { go } from '../../router.js'
  import Icon from '../Icon.svelte'
  import { fmtStamp } from '../../lib/time.js'

  const TERMINAL = ['completed', 'failed', 'cancelled']
  const WORD = { completed: '✓ completed', failed: '✗ failed', cancelled: 'cancelled',
                 running: 'running…', needs_input: 'needs your input' }
</script>

{#if $runInfo}
  <!-- .panel is the app's generic thread-column card (app.css) — the same shell
       Hitl's question cards and TaskCard use, centered to --thread-max and sitting
       right where those cards would. Its descendant selectors (`.panel .open`,
       `.panel .taskerror`, `.panel .runlink`) already style everything below for
       free; `.runbanner` only adds the row layout on top. -->
  <div class="runbanner panel">
    <button class="runlink" onclick={() => go('/t/' + $runInfo.task_id)}>
      <Icon name="list" size={13} /> {$runInfo.task_name || 'Task'}
    </button>
    <span class="runmeta">{fmtStamp($runInfo.started_at)} · {WORD[$runInfo.status] || $runInfo.status}</span>
    {#if !TERMINAL.includes($runInfo.status)}
      <button class="open danger" onclick={() => api.stopRun($runInfo.id)}><Icon name="x" size={13} /> Stop run</button>
    {/if}
    {#if $runInfo.status === 'failed' && $runInfo.error}
      <span class="taskerror"><Icon name="x" size={13} /> {$runInfo.error}</span>
    {/if}
  </div>
{/if}

<style>
  .runbanner { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; font-size: var(--text-sm); }
  .runmeta { color: var(--muted); }
  /* .panel .runlink (app.css) only resets the button chrome — add the icon+text
     row layout on top, same as every other icon-leading link/button in the app. */
  .runlink { display: inline-flex; align-items: center; gap: 5px; }
</style>
