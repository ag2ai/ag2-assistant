<script>
  import { onMount } from 'svelte'
  import { inquiries, soundOnInput } from '../store.js'
  import { api } from '../transport/api.js'
  import { go } from '../router.js'
  import { chime } from '../lib/chime.js'

  let drafts = $state({})
  let seen = new Set()   // inquiry ids already surfaced — chime only on genuinely new ones
  let first = true

  async function refresh() {
    try {
      const next = await api.inquiries()
      const fresh = !first && next.some((q) => !seen.has(q.id))
      seen = new Set(next.map((q) => q.id))
      first = false
      $inquiries = next
      if (fresh && $soundOnInput) chime()
    } catch {}
  }
  onMount(() => { refresh(); const t = setInterval(refresh, 4000); return () => clearInterval(t) })

  async function answer(q, text) {
    if (!text || !text.trim()) return
    try { await api.answerInquiry(q.id, text.trim()) } catch {}
    drafts[q.id] = ''
    refresh()
  }
</script>

{#if $inquiries.length}
  <div class="hitl">
    <div class="hitlhead">Needs your input ({$inquiries.length})</div>
    {#each $inquiries as q (q.id)}
      <div class="qcard">
        <div class="qk">
          {q.kind === 'permission' ? 'Permission' : 'Question'}
          {#if q.task_title}· <a onclick={() => q.root_id && go('/t/' + q.root_id)}>{q.task_title}</a>{/if}
        </div>
        <div class="qt">{q.text}</div>
        {#if q.detail}<div class="qd">{q.detail}</div>{/if}
        {#if q.options && q.options.length}
          <div class="qopts">{#each q.options as o}<button onclick={() => answer(q, o)}>{o}</button>{/each}</div>
        {:else}
          <input placeholder="Your answer…" bind:value={drafts[q.id]}
                 onkeydown={(e) => e.key === 'Enter' && answer(q, drafts[q.id])} />
        {/if}
      </div>
    {/each}
  </div>
{/if}
