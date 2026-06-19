<script>
  let { item } = $props()
</script>

<div class="note" class:alert={item.alert}>{item.text}</div>
