<script lang="ts">
  // Compact header over a run's chat thread: which task, when, current status,
  // with a Stop for live runs and a link back to the task page.
  import { runInfo } from '../../store.ts'
  import { api } from '../../transport/api/index.ts'
  import { go } from '../../router.ts'
  import type { RunStatus } from '../../schemas/index.ts'
  import Icon from '../Icon.svelte'
  import { fmtStamp } from '../../lib/time.ts'
  import { m } from '../../paraglide/messages.js'

  const TERMINAL: RunStatus[] = ['completed', 'failed', 'cancelled']
  const WORD: Record<RunStatus, () => string> = {
    completed: m.task_run_completed, failed: m.task_run_failed_word, cancelled: m.status_cancelled,
    running: m.task_run_running, needs_input: m.task_run_needs_input,
  }
</script>

{#if $runInfo}
  <!-- .panel is the app's generic thread-column card (app.css) — the same shell
       Hitl's question cards and TaskCard use, centered to --thread-max and sitting
       right where those cards would. Its descendant selectors (`.panel .open`,
       `.panel .taskerror`, `.panel .runlink`) already style everything below for
       free; `.runbanner` only adds the row layout on top. -->
  <div class="runbanner panel">
    <button class="runlink" onclick={() => go('/t/' + $runInfo.task_id)}>
      <Icon name="list" size={13} /> {$runInfo.task_name || m.thread_task()}
    </button>
    <span class="runmeta">{fmtStamp($runInfo.started_at)} · {(WORD[$runInfo.status] ?? (() => $runInfo.status))()}</span>
    {#if !TERMINAL.includes($runInfo.status)}
      <button class="open danger" onclick={() => api.stopRun($runInfo.id)}><Icon name="x" size={13} /> {m.task_stop_run()}</button>
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
