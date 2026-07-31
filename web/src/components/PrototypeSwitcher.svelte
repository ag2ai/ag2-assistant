<script>
  // PROTOTYPE PLUMBING — throwaway. A floating pill at the bottom of the screen
  // that cycles the `?variant=` search param. Deliberately loud and un-themed so
  // it never reads as part of the design being judged. Dev builds only.
  import { variant, setVariant, PROTOTYPE_ENABLED } from '../lib/prototypeVariant.js'

  // variants: [{ key, name }] — key '' is the current/production rendering.
  let { variants } = $props()

  const idx = $derived(Math.max(0, variants.findIndex((v) => v.key === $variant)))
  const cur = $derived(variants[idx])

  function go(step) {
    const n = variants.length
    setVariant(variants[(idx + step + n) % n].key)
  }

  function onKey(e) {
    if (!PROTOTYPE_ENABLED) return
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
    const t = e.target
    if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable || t.tagName === 'SELECT')) return
    e.preventDefault()
    go(e.key === 'ArrowRight' ? 1 : -1)
  }
</script>

<svelte:window onkeydown={onKey} />

{#if PROTOTYPE_ENABLED}
  <div class="protobar">
    <button aria-label="Previous variant" onclick={() => go(-1)}>‹</button>
    <span class="protolab">
      <b>{cur.key || '—'}</b> {cur.name}
      <em>prototype · ← →</em>
    </span>
    <button aria-label="Next variant" onclick={() => go(1)}>›</button>
  </div>
{/if}

<style>
  .protobar {
    position: fixed; z-index: 999; left: 50%; bottom: 18px; transform: translateX(-50%);
    display: flex; align-items: center; gap: 4px;
    background: #f5c518; color: #171717; border-radius: 999px;
    padding: 4px 6px; box-shadow: 0 8px 26px rgba(0,0,0,.42);
    font: 600 12px/1 var(--font, system-ui, sans-serif);
  }
  .protobar button {
    border: none; background: rgba(0,0,0,.09); color: inherit; cursor: pointer;
    width: 26px; height: 26px; border-radius: 999px; font-size: 17px; line-height: 1;
  }
  .protobar button:hover { background: rgba(0,0,0,.2); }
  .protolab { display: flex; align-items: baseline; gap: 6px; padding: 0 6px; white-space: nowrap; }
  .protolab b { font-size: 13px; }
  .protolab em { font-style: normal; opacity: .6; font-size: 10.5px; letter-spacing: .3px; }
</style>
