<script>
  // The ChoiceCard — inline rendering of a durable HITL inquiry on the thread.
  // Not an A2UI surface: the question IS the inquiry (restart-proof, answerable
  // from any channel); this card is just its projection, styled to sit alongside
  // the editorial A2UI surfaces. Options render as tappable chips; free-text
  // stays available. Answered cards keep the chosen chip highlighted.
  import { answer } from '../../controller.js'
  let { item } = $props()
  let text = $state('')
  function pick(opt) { answer(item.inquiryId, opt) }
  function submit() { if (text.trim()) { answer(item.inquiryId, text.trim()); text = '' } }
</script>

<div class="choice" class:resolved={item.resolved}>
  <div class="ck">
    <span class="cdot"></span>
    {item.qkind === 'permission' ? 'Permission · needs your call' : item.resolved ? 'You answered' : 'Needs your answer'}
  </div>
  <div class="cq">{item.question}</div>
  {#if item.detail}<pre class="cdetail" title="Details">{item.detail}</pre>{/if}
  {#if item.options && item.options.length}
    <div class="copts">
      {#each item.options as o}
        <button
          class="chip"
          class:picked={item.resolved && item.answer === o}
          disabled={item.resolved}
          onclick={() => pick(o)}
        >{o}</button>
      {/each}
    </div>
    {#if item.resolved && !item.options.includes(item.answer)}
      <div class="cans">→ {item.answer}</div>
    {/if}
  {:else if item.resolved}
    <div class="cans">→ {item.answer}</div>
  {:else}
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

  .ck { display: flex; align-items: center; gap: 7px; font-family: var(--code, ui-monospace, monospace); font-size: 9.5px; font-weight: 700; letter-spacing: .18em; text-transform: uppercase; color: var(--accent); }
  .choice.resolved .ck { color: var(--muted); }
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
  .chip.picked { opacity: 1; border-color: var(--accent); background: var(--accent); color: var(--paper, #fff); }

  .cans { margin-top: 9px; font-family: var(--code, ui-monospace, monospace); font-size: 12.5px; color: var(--muted); }

  .choice input { width: 100%; margin-top: 10px; padding: 9px 11px; border: 1px solid var(--line); border-radius: 8px; background: var(--bg); color: var(--ink); font-size: 13.5px; }
  .choice input:focus { outline: none; border-color: var(--accent); }
</style>
