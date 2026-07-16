<script>
  // The AG2 Inspector: the live AG2 event stream behind the current view. The UI
  // is a projection of one AG2 Stream, so these {type,data} events ARE the AG2
  // primitives at work — including memory aggregation / usage / observer events
  // the normal UI folds away.
  import { inspectorEvents, ag2View, poweredByOpen } from '../store.js'
  import { describe, SUBSYSTEMS } from '../lib/ag2map.js'
  import Icon from './Icon.svelte'

  let open = $state(new Set())
  const toggle = (id) => {
    const n = new Set(open)
    n.has(id) ? n.delete(id) : n.add(id)
    open = n
  }
  const fmt = (t) => new Date(t).toLocaleTimeString([], { hour12: false })
  const rows = $derived([...$inspectorEvents].reverse()) // newest first
</script>

<aside class="inspector ag2-slide-left">
  <div class="insp-head">
    <span class="insp-title">AG2 events</span>
    <button class="linklike" onclick={() => ($poweredByOpen = true)}>Powered by AG2</button>
    <button class="insp-x" title="Hide AG2 view" aria-label="Hide AG2 view" onclick={() => ($ag2View = false)}><Icon name="x" size={16} /></button>
  </div>
  <div class="insp-sub">
    Live events on this chat's AG2 <code>Stream</code> — the substrate this UI projects.
  </div>

  <div class="insp-list">
    {#if !rows.length}
      <div class="insp-empty">No events yet — say something to the assistant.</div>
    {/if}
    {#each rows as e (e._id)}
      {@const d = describe(e.type)}
      {@const c = (SUBSYSTEMS[d.sub] || {}).color || '#888'}
      <div class="insp-row" onclick={() => toggle(e._id)} role="button" tabindex="0">
        <span class="insp-dot" style="background:{c}"></span>
        <span class="insp-name">{d.label}</span>
        <span class="insp-sub2" style="color:{c}">{d.sub}</span>
        {#if d.layer === 'app'}<span class="insp-layer" title="App event riding the AG2 stream">app</span>{/if}
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
