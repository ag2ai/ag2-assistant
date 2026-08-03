<script>
  // The architecture map: which AG2 primitives power this assistant, and
  // (honestly) what's the app layer built on top.
  import { poweredByOpen, appVersion, ag2Version } from '../store.ts'
  import { PRIMITIVES, SUBSYSTEMS, AG2_DOCS } from '../lib/ag2map.ts'

  const close = () => ($poweredByOpen = false)
  const ag2 = PRIMITIVES.filter((p) => p.layer === 'ag2')
  const app = PRIMITIVES.filter((p) => p.layer === 'app')
  const dot = (sub) => (SUBSYSTEMS[sub] || {}).color || 'var(--line)'
</script>

<!-- Backdrop: click-to-dismiss duplicates the × button, so it stays out of the
     a11y tree rather than becoming a second focusable control. -->
<div class="modal-backdrop" role="presentation" onclick={close}></div>
<div class="modal poweredby">
  <button class="modal-x" aria-label="Close" onclick={close}>×</button>
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
