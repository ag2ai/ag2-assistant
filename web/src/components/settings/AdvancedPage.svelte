<script lang="ts">
  // Advanced settings: history and turn budgets, shared identity, and the AG2 view.
  import { getSettings } from './context.svelte.ts'
  import { ag2View } from '../../store.ts'
  import { toggleAsideInspector } from '../../router.ts'
  import { api } from '../../transport/api/index.ts'
  import Icon from '../Icon.svelte'
  import MemoryDocEditor from './MemoryDocEditor.svelte'

  const ctx = getSettings()
  let replyTimeout = $state('')
  let compactMaxTokens = $state('')

  $effect(() => {
    if (ctx.s) {
      replyTimeout = String(ctx.s.reply_timeout_s)
      compactMaxTokens = String(ctx.s.compact_max_tokens)
    }
  })

  function saveReplyTimeout() {
    const seconds = Number(replyTimeout)
    if (!Number.isFinite(seconds) || seconds <= 0) {
      ctx.err = 'Chat turn timeout must be greater than zero.'
      return
    }
    ctx.run(() => api.setReplyTimeout(seconds))
  }

  function saveCompactionThreshold() {
    const tokens = Number(compactMaxTokens)
    if (!Number.isSafeInteger(tokens) || tokens <= 0) {
      ctx.err = 'History compaction threshold must be a positive whole number.'
      return
    }
    ctx.run(() => api.setCompactionThreshold(tokens))
  }
</script>

<div class="setgroup">Chat history</div>
<div class="setrowwrap">
  <div class="setrow">
    <span class="sk">History compaction threshold</span>
    <span class="sv">Applies next message</span>
  </div>
  <label class="timeoutfield tokenfield">
    <input type="number" min="1" step="1" bind:value={compactMaxTokens} aria-label="History compaction threshold in tokens" />
    <span>tokens</span>
  </label>
  <button class="open" disabled={ctx.busy} onclick={saveCompactionThreshold}>Save</button>
</div>
<p class="muted">Budget the estimated full model input, including instructions, tools, memory, skills, and messages. Older messages are summarized before a model call when needed. The full chat history stays available.</p>

<div class="setgroup">Turn budget</div>
<div class="setrowwrap">
  <div class="setrow">
    <span class="sk"><Icon name="clock" size={15} /> Chat turn timeout</span>
    <span class="sv">Total time for model calls, tools, and questions</span>
  </div>
  <label class="timeoutfield">
    <input type="number" min="1" max="3600" step="1" bind:value={replyTimeout} aria-label="Chat turn timeout in seconds" />
    <span>sec</span>
  </label>
  <button class="open" disabled={ctx.busy} onclick={saveReplyTimeout}>Save</button>
</div>

<div class="setgroup">Who you are</div>
<MemoryDocEditor
  load={() => api.globalMemory().then((r) => r.text)}
  save={(t) => api.setGlobalMemory(t)}
  hint="Identity facts — name, location, timezone, family, writing voice — that are true no matter which profile you're in. Shared across every profile."
  placeholder="(nothing here yet)"
/>

<div class="setgroup">AG2</div>
<div class="setrowwrap">
  <div class="setrow">
    <span class="sk"><Icon name="zap" size={15} /> Powered by AG2</span>
    <span class="sv">the AG2 primitives behind this app</span>
  </div>
  <button class="open" onclick={ctx.openPoweredBy}>View</button>
</div>
<label class="setcheck">
  <input type="checkbox" checked={$ag2View} onchange={toggleAsideInspector} />
  AG2 view — reveal live AG2 events + per-item provenance
</label>

<style>
  .timeoutfield { display: flex; align-items: center; gap: 5px; color: var(--muted); font-size: 13px; }
  .timeoutfield input { width: 66px; border: 1px solid var(--line); border-radius: 8px; padding: 7px 8px; font: inherit; background: var(--bg); color: var(--ink); }
  .timeoutfield input:focus { outline: none; border-color: var(--accent); box-shadow: var(--focus-ring); }
  .tokenfield input { width: 100px; }
</style>
