<script>
  // The architecture map: which AG2 primitives power this assistant, and
  // (honestly) what's the app layer built on top.
  import { appVersion, ag2Version } from '../store.js'
  import { route, closeOverlay, replaceOverlay } from '../router.js'
  import { PRIMITIVES, SUBSYSTEMS, AG2_DOCS } from '../lib/ag2map.js'
  import Icon from './Icon.svelte'

  // The map is URL-addressed (`#poweredby`), so × / Esc / browser Back all funnel
  // through the one close — Back off the map returns to whatever pushed it (the
  // Settings modal, or the bare page when opened from the Inspector).
  const close = () => closeOverlay()

  // Opened from Settings, the hash carries the Section we came from
  // (`#poweredby=advanced`) — render an explicit way back to it, so returning
  // doesn't depend on the user knowing about browser Back. Absent when the map was
  // opened from the Inspector: there'd be no Settings to go back TO.
  const backTo = $derived($route.overlayValue)
  const back = () => replaceOverlay('settings', backTo)

  const ag2 = PRIMITIVES.filter((p) => p.layer === 'ag2')
  const app = PRIMITIVES.filter((p) => p.layer === 'app')
  const dot = (sub) => (SUBSYSTEMS[sub] || {}).color || 'var(--line)'

  const onKey = (e) => { if (e.key === 'Escape') close() }
</script>

<svelte:window onkeydown={onKey} />
<div class="modal-backdrop" onclick={close}></div>
<div class="modal poweredby">
  <button class="modal-x" aria-label="Close" onclick={close}>×</button>
  <!-- Window chrome lives in the head: Back at the top-left, × at the top-right
       (absolutely positioned to the same corner band). The back button leads the
       title, the ProfileEditor .pback idiom. -->
  {#if backTo}
    <button class="pbback" onclick={back}><Icon name="chevron-left" size={15} /> Settings</button>
  {/if}
  <h2>Powered by AG2</h2>
  <p class="muted">
    The runtime, event stream, memory, tools, human-in-the-loop, voice and observers
    are all AG2 primitives. Turn on <strong>AG2 view</strong> (the <code>&lt;/&gt; AG2</code>
    button) to watch the live events behind the UI. The “app layer” rows are what this
    project adds on top.
  </p>
  <div class="pbscroll">
    <div class="setsec">AG2 primitives</div>
    {#each ag2 as p}
      <div class="pbrow">
        <span class="insp-dot" style="background:{dot(p.sub)}"></span>
        <div class="pbtext"><div class="pbname">{p.name}</div><div class="pbwhat">{p.what}</div></div>
      </div>
    {/each}
    <div class="setsec">App layer (built on AG2)</div>
    {#each app as p}
      <div class="pbrow">
        <span class="insp-dot" style="background:var(--line)"></span>
        <div class="pbtext"><div class="pbname">{p.name}</div><div class="pbwhat">{p.what}</div></div>
      </div>
    {/each}
  </div>
  <div class="mfoot">
    {#if $appVersion}
      <span class="pbver">AG2 Assistant v{$appVersion}{#if $ag2Version} · AG2 v{$ag2Version}{/if}</span>
    {/if}
    <a class="open" href={AG2_DOCS} target="_blank" rel="noopener noreferrer">AG2 docs ↗</a>
  </div>
</div>
