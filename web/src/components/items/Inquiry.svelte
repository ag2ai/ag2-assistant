<script>
  // The ChoiceCard — inline rendering of a durable HITL inquiry on the thread.
  // Not an A2UI surface: the question IS the inquiry (restart-proof, answerable
  // from any channel); this card is just its projection, styled to sit alongside
  // the editorial A2UI surfaces. Options render as tappable chips; free-text
  // stays available. A retired card keeps the chosen chip highlighted (if answered)
  // and says HOW it resolved — answered / expired / cancelled — with its buttons
  // disabled, so a timed-out or task-ended prompt never leaves dead live controls.
  import { answer } from '../../controller.js'
  import { taskPanel } from '../../store.js'
  const TERMINAL = new Set(['completed', 'failed', 'cancelled'])
  let { item } = $props()
  let text = $state('')
  let showDetail = $state(false)
  function pick(opt) { answer(item.inquiryId, opt) }
  function submit() { if (text.trim()) { answer(item.inquiryId, text.trim()); text = '' } }

  // A card is retired once its inquiry resolves OR its owning task reaches a
  // terminal state (belt-and-suspenders: catches prompts stranded before the
  // resolution event existed — answering them is a server-side no-op anyway).
  let onTerminalTask = $derived(!!$taskPanel && TERMINAL.has($taskPanel.status))
  let retired = $derived(!!item.resolved || onTerminalTask)
  // How it resolved: explicit resolution wins; a real answer implies "answered";
  // otherwise a retired-by-terminal-task prompt was simply never answered.
  let state = $derived(
    item.resolution || (item.resolved ? 'answered' : retired ? 'unanswered' : null)
  )
  const LABEL = {
    answered: 'You answered',
    expired: 'Expired · not answered in time',
    cancelled: 'Cancelled · task ended',
    unanswered: 'Not answered',
  }
  let header = $derived(
    state ? LABEL[state]
      : item.qkind === 'permission' ? 'Permission · needs your call'
      : 'Needs your answer'
  )
  let answered = $derived(state === 'answered' && !!item.answer)
</script>

<div class="choice" class:resolved={retired} class:unanswered={retired && !answered}>
  <div class="ck">
    <span class="cdot"></span>
    {header}
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
    <div class="copts">
      {#each item.options as o}
        <button
          class="chip"
          class:picked={answered && item.answer === o}
          disabled={retired}
          onclick={() => pick(o)}
        >{o}</button>
      {/each}
    </div>
    {#if answered && !item.options.includes(item.answer)}
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
  /* An expired/cancelled/unanswered prompt reads as a muted, dimmed record — it
     asked for something it never got, so it shouldn't look like a done deal. */
  .choice.unanswered { opacity: .72; }
  .choice.unanswered .cdot { background: var(--muted); }

  .ck { display: flex; align-items: center; gap: 7px; font-family: var(--code, ui-monospace, monospace); font-size: 9.5px; font-weight: 700; letter-spacing: .18em; text-transform: uppercase; color: var(--accent); }
  .choice.resolved .ck { color: var(--muted); }

  .detail-toggle { margin-top: 9px; padding: 0; background: none; border: none; color: var(--muted); font-family: var(--code, ui-monospace, monospace); font-size: 11px; letter-spacing: .04em; cursor: pointer; text-decoration: underline; text-underline-offset: 3px; }
  .detail-toggle:hover { color: var(--ink); }
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
  .chip.picked { opacity: 1; border-color: var(--accent); background: var(--accent); color: var(--text-on-accent, #fff); }

  .cans { margin-top: 9px; font-family: var(--code, ui-monospace, monospace); font-size: 12.5px; color: var(--muted); }

  .choice input { width: 100%; margin-top: 10px; padding: 9px 11px; border: 1px solid var(--line); border-radius: 8px; background: var(--bg); color: var(--ink); font-size: 13.5px; }
  .choice input:focus { outline: none; border-color: var(--accent); }
</style>
