<script>
  let { item } = $props()
</script>

<div class="msg user"><div class="bubble" class:voice={item.voice}>{item.text}</div></div>
