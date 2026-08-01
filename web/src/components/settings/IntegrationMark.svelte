<script>
  // One platform's mark, wherever Settings → Integrations names a platform: the row,
  // the Add grid, the Connect form header and a Connection's settings header.
  //
  // A platform this build does not recognise falls back to a neutral square carrying
  // the first letter of the Connection's own name — reachable by downgrading the app
  // while a Connection names a platform a newer version added. The name, not the
  // platform, so two unknown Connections stay distinguishable, and so the page still
  // renders instead of taking itself down over one unrecognised row.
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
  <span class="cnmark" class:sm style="--tint:var(--muted)">{(name.trim()[0] || '?').toUpperCase()}</span>
{/if}
