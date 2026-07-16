<script>
  import { onMount } from 'svelte'
  import { inquiries, soundOnInput } from '../store.js'
  import { api } from '../transport/api.js'
  import { go, route } from '../router.js'
  import { chime } from '../lib/chime.js'

  let drafts = $state({})
  let seen = new Set()   // inquiry ids already surfaced — chime only on genuinely new ones
  let first = true

  // The stream the open page renders inline: a task page → "task:<id>", a chat
  // page → the chat id (mirrors controller.js's chat mapping).
  const pageChat = $derived(
    $route.name === 'task' ? 'task:' + $route.id : $route.name === 'chat' ? $route.id : null,
  )

  // An inquiry the open page already renders inline (its InquiryRaised rides that
  // chat's stream) is dropped from the strip — answer it in context, not twice.
  // Everything else stays: other pages' inquiries, subtask inquiries (different
  // stream), and transient prompts without a chat.
  const visible = $derived(
    $inquiries.filter((q) => {
      if (q._src !== 'inquiry') return true
      if (q.chat && q.chat === pageChat) return false
      if ($route.name === 'task' && q.task_id === $route.id) return false // pre-chat fallback
      return true
    }),
  )

  async function refresh() {
    try {
      // Two sources merged into one strip: durable task inquiries, and chat-turn
      // permission prompts (run_code/shell/file) which live in the HitlServer.
      const [inq, hitl] = await Promise.all([
        api.inquiries().catch(() => []),
        api.hitlPending().catch(() => []),
      ])
      const next = [
        ...inq.map((q) => ({ ...q, _src: 'inquiry', _key: 'inq:' + q.id })),
        ...hitl.map((q) => ({ ...q, _src: 'hitl', _key: 'hitl:' + q.id })),
      ]
      const fresh = !first && next.some((q) => !seen.has(q._key))
      seen = new Set(next.map((q) => q._key))
      first = false
      $inquiries = next
      if (fresh && $soundOnInput) chime()
    } catch {}
  }
  onMount(() => { refresh(); const t = setInterval(refresh, 4000); return () => clearInterval(t) })

  async function answer(q, text) {
    if (!text || !text.trim()) return
    try {
      if (q._src === 'hitl') await api.answerHitl(q.id, text.trim())
      else await api.answerInquiry(q.id, text.trim())
    } catch {}
    drafts[q._key] = ''
    refresh()
  }
</script>

{#if visible.length}
  <div class="hitl">
    <div class="hitlhead">Needs your input ({visible.length})</div>
    {#each visible as q (q._key)}
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
          <input placeholder="Your answer…" bind:value={drafts[q._key]}
                 onkeydown={(e) => e.key === 'Enter' && answer(q, drafts[q._key])} />
        {/if}
      </div>
    {/each}
  </div>
{/if}
