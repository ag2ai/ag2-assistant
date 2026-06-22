<script>
  import Icon from '../Icon.svelte'
  import { fmtStamp } from '../../lib/time.js'
  let { item } = $props()
</script>

<div class="note" class:alert={item.alert} class:withicon={item.icon}>
  {#if item.icon}<Icon name={item.icon} size={14} />{/if}{item.text}
  {#if item.at}<span class="itemtime">{fmtStamp(item.at)}</span>{/if}
</div>
