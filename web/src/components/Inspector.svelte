<script lang="ts">
  // The AG2 Inspector: the live AG2 event stream behind the current view. The UI
  // is a projection of one AG2 Stream, so these {type,data} events ARE the AG2
  // primitives at work — including memory aggregation / usage / observer events
  // the normal UI folds away.
  import { inspectorEvents } from '../store.ts'
  import { closeAside, openOverlay } from '../router.ts'
  import { describe, SUBSYSTEMS } from '../lib/ag2map.ts'
  import RailResizer from './RailResizer.svelte'
  import Icon from './Icon.svelte'
  import { m } from '../paraglide/messages.js'
  import { getLocale } from '../paraglide/runtime.js'

  let open = $state(new Set<number>())
  const toggle = (id: number) => {
    const n = new Set(open)
    n.has(id) ? n.delete(id) : n.add(id)
    open = n
  }
  const fmt = (t: number) => new Date(t).toLocaleTimeString(getLocale(), { hour12: false })
  const rows = $derived([...$inspectorEvents].reverse()) // newest first
</script>

<aside class="inspector ag2-slide-left">
  <RailResizer />
  <div class="insp-head">
    <span class="insp-title">{m.insp_title()}</span>
    <button class="linklike" onclick={() => openOverlay('poweredby')}>{m.pb_title()}</button>
    <button class="insp-x" title={m.insp_hide()} aria-label={m.insp_hide()} onclick={closeAside}><Icon name="x" size={16} /></button>
  </div>
  <div class="insp-sub">
    {m.insp_sub()} <code>Stream</code> {m.insp_sub_tail()}
  </div>

  <div class="insp-list">
    {#if !rows.length}
      <div class="insp-empty">{m.insp_empty()}</div>
    {/if}
    {#each rows as e (e._id)}
      {@const d = describe(e.type)}
      {@const c = (SUBSYSTEMS[d.sub] || {}).color || '#888'}
      <div class="insp-row" role="button" tabindex="0" onclick={() => toggle(e._id)}
        onkeydown={(ev) => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); toggle(e._id) } }}>
        <span class="insp-dot" style="background:{c}"></span>
        <span class="insp-name">{d.label}</span>
        <span class="insp-sub2" style="color:{c}">{d.sub}</span>
        {#if d.layer === 'app'}<span class="insp-layer" title={m.insp_app_layer()}>app</span>{/if}
        <span class="insp-time">{fmt(e._t)}</span>
      </div>
      {#if open.has(e._id)}
        <pre class="insp-raw">{JSON.stringify(e.data, null, 2)}</pre>
      {/if}
    {/each}
  </div>

  <div class="insp-legend">
    {#each Object.entries(SUBSYSTEMS) as [k, v]}
      <span class="insp-leg" title={v.blurb}><span class="insp-dot" style="background:{v.color}"></span>{k}</span>
    {/each}
  </div>
</aside>
