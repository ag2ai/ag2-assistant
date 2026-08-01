<script>
  // One platform's mark, wherever Settings → Integrations names a platform. An
  // unrecognised platform falls back to a neutral square carrying the name's first
  // letter, which keeps two unknown Connections distinguishable.
  import BrandMark from '../BrandMark.svelte'
  import { brandMark } from '../../lib/brandMarks.js'

  // platform: a CATALOG id. name: the Connection's own name, only read when the
  // platform is unrecognised. sm: the smaller size the Add grid draws marks at.
  let { platform, name = '', sm = false } = $props()

  const known = $derived(!!brandMark(platform))
</script>

{#if known}
  <BrandMark brand={platform} size={sm ? 16 : 20} />
{:else}
  <span class="cnmark" class:sm style="--tint:var(--muted)">{((name || '').trim()[0] || '?').toUpperCase()}</span>
{/if}
