<script>
  import { answer } from '../../controller.js'
  let { item } = $props()
  let text = $state('')
  function pick(opt) { answer(item.inquiryId, opt) }
  function submit() { if (text.trim()) { answer(item.inquiryId, text.trim()); text = '' } }
</script>

<div class="inquiry" class:resolved={item.resolved}>
  <div class="k">{item.qkind === 'permission' ? 'Permission' : 'Question'}</div>
  <div class="t">{item.question}</div>
  {#if item.resolved}
    <div class="d">Answered: {item.answer}</div>
  {:else if item.options && item.options.length}
    <div class="opts">{#each item.options as o}<button onclick={() => pick(o)}>{o}</button>{/each}</div>
  {:else}
    <input bind:value={text} placeholder="Your answer…" onkeydown={(e) => e.key === 'Enter' && submit()} />
  {/if}
</div>
