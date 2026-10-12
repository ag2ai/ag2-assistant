<script lang="ts">
  import Select from '../src/components/Select.svelte'

  let { disabled = false }: { disabled?: boolean } = $props()
  let value = $state('')
  let changed = $state('')
</script>

<form>
  <label>Directory
    <Select bind:value {disabled} name="directory" required data-test="directory"
      onchange={(event) => { changed = event.currentTarget.value }}>
      <option value="">Files</option>
      <option value="drafts">Drafts with a long directory name</option>
    </Select>
  </label>
  <output data-selection>{value}</output>
  <output data-change>{changed}</output>
  <button type="button" onclick={() => { value = '' }}>Reset</button>
  <Select aria-label="Default selection" data-test="default">
    <option value="first">First</option>
    <option value="second">Second</option>
  </Select>
</form>
