<script>
  import Icon from '../Icon.svelte'
  let { item } = $props()
</script>

<div class="note" class:alert={item.alert} class:withicon={item.icon}>
  {#if item.icon}<Icon name={item.icon} size={14} />{/if}{item.text}
</div>
