<script lang="ts">
  // Inline HITL inquiries show choices while pending and their result once resolved.
  import { answer } from '../../controller.ts'
  import { runInfo } from '../../store.ts'
  import type { ThreadItem } from '../../schemas/events.ts'

  const TERMINAL = new Set(['completed', 'failed', 'cancelled'])

  type Props = { item: Extract<ThreadItem, { kind: 'inquiry' }> }
  let { item }: Props = $props()
  let text = $state('')
  let showDetail = $state(false)
  function pick(opt: string) { answer(item.inquiryId, opt) }
  function submit() { if (text.trim()) { answer(item.inquiryId, text.trim()); text = '' } }

  // Resolved inquiries and prompts belonging to a terminal task cannot be answered.
  let onTerminalTask = $derived(!!$runInfo && TERMINAL.has($runInfo.status))
  let retired = $derived(!!item.resolved || onTerminalTask)
  // Explicit resolution wins; a terminal task retires a stranded prompt as unanswered.
  let resolution = $derived(
    item.resolution || (item.resolved ? 'answered' : retired ? 'unanswered' : null)
  )
  const LABEL: Record<string, string | undefined> = {
    answered: 'You answered',
    expired: 'Expired · not answered in time',
    cancelled: 'Cancelled · task ended',
    unanswered: 'Not answered',
  }
  let header = $derived(
    resolution ? LABEL[resolution]
      : item.qkind === 'permission' ? 'Permission · needs your call'
      : 'Needs your answer'
  )
  let answered = $derived(resolution === 'answered' && !!item.answer)
</script>

<div class="choice" class:resolved={retired} class:unanswered={retired && !answered}>
  <div class="ck">
    <span class="cdot"></span>
    {#if answered}
      <span class="answer-label" role="status">{item.answer}</span>
    {:else}
      {header}
    {/if}
  </div>
  <div class="cq">{item.question}</div>
  {#if item.detail}
    {#if retired}
      <button class="detail-toggle" onclick={() => (showDetail = !showDetail)}>
        {showDetail ? 'Hide details' : 'Show details'}
      </button>
      {#if showDetail}<pre class="cdetail" title="Details">{item.detail}</pre>{/if}
    {:else}
      <pre class="cdetail" title="Details">{item.detail}</pre>
    {/if}
  {/if}
  {#if item.options && item.options.length}
    {#if !answered}
      <div class="copts">
        {#each item.options as o}
          <button
            class="chip"
            disabled={retired}
            onclick={() => pick(o)}
          >{o}</button>
        {/each}
      </div>
    {/if}
    {#if answered && !item.options.includes(item.answer ?? '')}
      <div class="cans">→ {item.answer}</div>
    {/if}
  {:else if answered}
    <div class="cans">→ {item.answer}</div>
  {:else if !retired}
    <input bind:value={text} placeholder="Your answer…" onkeydown={(e) => e.key === 'Enter' && submit()} />
  {/if}
</div>

<style>
  .choice {
    border: 1px solid var(--accent);
    border-left-width: 3px;
    border-radius: var(--radius-sm, 8px);
    padding: 13px 16px 14px;
    margin: 12px 0;
    background: color-mix(in srgb, var(--accent) 4%, transparent);
  }
  .choice.resolved { border-color: var(--line); background: transparent; }
  .choice.resolved .cdot { animation: none; opacity: .4; }
  /* Unanswered prompts render as muted records. */
  .choice.unanswered { opacity: .72; }
  .choice.unanswered .cdot { background: var(--muted); }

  .ck { display: flex; align-items: center; gap: 7px; font-family: var(--code, ui-monospace, monospace); font-size: 9.5px; font-weight: 700; letter-spacing: .18em; text-transform: uppercase; color: var(--accent); }
  .choice.resolved .ck { color: var(--muted); }

  .detail-toggle { margin-top: 9px; padding: 4px 9px; background: var(--bg); border: 1px solid var(--line); border-radius: 999px; color: var(--muted); font: inherit; font-size: 11px; cursor: pointer; }
  .detail-toggle:hover { color: var(--ink); border-color: var(--muted); }
  .detail-toggle:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  .answer-label { display: inline-flex; min-width: 0; max-width: 100%; padding: 3px 8px; border: 1px solid color-mix(in srgb, var(--accent) 25%, var(--line)); border-radius: 999px; background: color-mix(in srgb, var(--accent) 8%, transparent); color: var(--accent); font-family: var(--sans, inherit); font-size: 11px; font-weight: 600; line-height: 1.5; letter-spacing: normal; text-transform: none; overflow-wrap: anywhere; }
  .cdot { width: 7px; height: 7px; border-radius: 50%; background: var(--accent); animation: choice-pulse 1.6s infinite; }
  @keyframes choice-pulse {
    0% { box-shadow: 0 0 0 0 color-mix(in srgb, var(--accent) 55%, transparent); }
    70% { box-shadow: 0 0 0 7px transparent; }
    100% { box-shadow: 0 0 0 0 transparent; }
  }
  :global([data-animations='off']) .cdot { animation: none; }

  .cq { margin-top: 7px; font-family: var(--serif, inherit); font-weight: 600; font-size: 16.5px; line-height: 1.25; letter-spacing: -.01em; color: var(--ink); }

  .cdetail { margin: 9px 0 0; padding: 10px 12px; background: var(--code); border: 1px solid var(--line); border-radius: 8px; font-family: ui-monospace, monospace; font-size: 12px; line-height: 1.5; white-space: pre-wrap; word-break: break-word; overflow: auto; max-height: 320px; }

  .copts { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 11px; }
  .chip {
    border: 1px solid var(--line);
    background: var(--bg);
    color: var(--ink);
    border-radius: 999px;
    padding: 7px 15px;
    font-size: 13.5px;
    font-weight: 550;
    cursor: pointer;
    transition: border-color .12s, color .12s, background .12s;
  }
  .chip:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); background: color-mix(in srgb, var(--accent) 6%, transparent); }
  .chip:disabled { cursor: default; opacity: .45; }

  .cans { margin-top: 9px; font-family: var(--code, ui-monospace, monospace); font-size: 12.5px; color: var(--muted); }

  .choice input { width: 100%; margin-top: 10px; padding: 9px 11px; border: 1px solid var(--line); border-radius: 8px; background: var(--bg); color: var(--ink); font-size: 13.5px; }
  .choice input:focus { outline: none; border-color: var(--accent); }
</style>
