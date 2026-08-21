<script lang="ts">
  // Settings → Advanced: shared identity ("Who you are"), turn budget, and the AG2
  // architecture map + live-view toggle. The identity doc is install-wide
  // (globalMemory/setGlobalMemory), so it lives here rather than the per-profile
  // Profile Memory tab (ADR 0015), edited inline.
  import { getSettings } from './context.svelte.ts'
  import { ag2View } from '../../store.ts'
  import { toggleAsideInspector } from '../../router.ts'
  import { api } from '../../transport/api/index.ts'
  import Icon from '../Icon.svelte'
  import MemoryDocEditor from './MemoryDocEditor.svelte'
  import { m } from '../../paraglide/messages.js'

  const ctx = getSettings()
  let replyTimeout = $state('')

  $effect(() => {
    if (ctx.s) replyTimeout = String(ctx.s.reply_timeout_s)
  })

  function saveReplyTimeout() {
    const seconds = Number(replyTimeout)
    if (!Number.isFinite(seconds) || seconds <= 0) {
      ctx.err = m.advanced_timeout_invalid()
      return
    }
    ctx.run(() => api.setReplyTimeout(seconds))
  }
</script>

<div class="setgroup">{m.advanced_turn_budget()}</div>
<div class="setrowwrap">
  <div class="setrow">
    <span class="sk"><Icon name="clock" size={15} /> {m.advanced_turn_timeout()}</span>
    <span class="sv">{m.advanced_turn_timeout_sub()}</span>
  </div>
  <label class="timeoutfield">
    <input type="number" min="1" max="3600" step="1" bind:value={replyTimeout} aria-label={m.advanced_turn_timeout_aria()} />
    <span>{m.advanced_sec()}</span>
  </label>
  <button class="open" disabled={ctx.busy} onclick={saveReplyTimeout}>{m.action_save()}</button>
</div>

<div class="setgroup">{m.advanced_who_you_are()}</div>
<MemoryDocEditor
  load={() => api.globalMemory().then((r) => r.text)}
  save={(t) => api.setGlobalMemory(t)}
  hint={m.advanced_identity_hint()}
  placeholder={m.advanced_identity_placeholder()}
/>

<div class="setgroup">AG2</div>
<div class="setrowwrap">
  <div class="setrow">
    <span class="sk"><Icon name="zap" size={15} /> {m.onboarding_feature_ag2_title()}</span>
    <span class="sv">{m.advanced_ag2_sub()}</span>
  </div>
  <button class="open" onclick={ctx.openPoweredBy}>{m.drawer_view()}</button>
</div>
<label class="setcheck">
  <input type="checkbox" checked={$ag2View} onchange={toggleAsideInspector} />
  {m.advanced_ag2_view()}
</label>

<style>
  .timeoutfield { display: flex; align-items: center; gap: 5px; color: var(--muted); font-size: 13px; }
  .timeoutfield input { width: 66px; border: 1px solid var(--line); border-radius: 8px; padding: 7px 8px; font: inherit; background: var(--bg); color: var(--ink); }
  .timeoutfield input:focus { outline: none; border-color: var(--accent); box-shadow: var(--focus-ring); }
</style>
