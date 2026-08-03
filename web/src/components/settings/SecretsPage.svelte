<script>
  // Settings → Secrets: the install-wide list of named reusable API keys
  // (CONTEXT.md "Secrets"). Values are WRITE-ONLY: the edit field is a password
  // input whose placeholder shows the stored last-4 hint; saving it blank keeps
  // the value. The provider tag is soft (grouping only); at most one tagged
  // Secret per provider is the Default — the install-wide fallback, badge shown.
  import { onMount } from 'svelte'
  import { api } from '../../transport/api/index.ts'
  import { secretsStore, loadSecrets } from '../../lib/secrets.ts'
  import { loadLlmConfigs } from '../../lib/llm.ts'
  import { loadLiveConfigs } from '../../lib/live.ts'
  import Icon from '../Icon.svelte'

  const PROVIDERS = ['', 'openai', 'anthropic', 'gemini']
  const PROVIDER_LABEL = { '': 'no provider', openai: 'OpenAI', anthropic: 'Anthropic', gemini: 'Gemini' }

  const secrets = $derived($secretsStore.secrets)

  let editingId = $state(null)   // secret id being edited, 'new' for the add form
  let name = $state('')
  let value = $state('')
  let provider = $state('')
  let isDefault = $state(false)
  let busy = $state(false)
  let err = $state('')

  onMount(loadSecrets)

  function startAdd() { editingId = 'new'; name = ''; value = ''; provider = ''; isDefault = false; err = '' }
  function startEdit(s) { editingId = s.id; name = s.name; value = ''; provider = s.provider; isDefault = s.default; err = '' }
  function cancel() { editingId = null; err = '' }

  async function refresh() {
    // Model views embed the referenced Secret (name/hint/key_source) — refresh them too.
    await Promise.all([loadSecrets(), loadLlmConfigs(), loadLiveConfigs()])
  }

  async function save() {
    busy = true; err = ''
    try {
      if (editingId === 'new') {
        await api.createSecret({ name: name.trim(), value: value.trim(), provider, default: isDefault })
      } else {
        await api.updateSecret(editingId, {
          name: name.trim(),
          value: value !== '' ? value.trim() : null,  // blank keeps the stored value
          provider,
          default: isDefault,
        })
      }
      cancel()
      await refresh()
    } catch (e) { err = String(e.message || e) }
    busy = false
  }

  async function remove(s) {
    const used = s.used_by?.length
      ? `\n\nUsed by ${s.used_by.length} model${s.used_by.length > 1 ? 's' : ''}: ${s.used_by.join(', ')}.\nThey will fall back to the provider default or env key.`
      : ''
    if (!confirm(`Delete secret "${s.name}"?${used}`)) return
    busy = true; err = ''
    try {
      await api.deleteSecret(s.id)
      await refresh()
    } catch (e) { err = String(e.message || e) }
    busy = false
  }
</script>

<div class="setgroup">Secrets</div>
<p class="setsub">
  Named, reusable API keys. Attach one to any Text or Live model; mark a
  provider-tagged secret as its provider's default fallback. Values are never
  shown back — only the last 4 characters.
</p>

{#if err && editingId === null}<p class="muted" style="color:var(--danger);font-size:13px">{err}</p>{/if}

{#if !secrets.length && editingId === null}
  <p class="muted" style="font-size:13px">No secrets yet — add one below, or paste a key in a model form.</p>
{/if}

{#each secrets as s (s.id)}
  {#if editingId === s.id}
    <div class="llmform">
      <div class="llmfield">
        <label for="sp-name">Name</label>
        <input id="sp-name" bind:value={name} placeholder="e.g. Work OpenAI" />
      </div>
      <div class="llmfield">
        <label for="sp-value">Value <span class="llmhint">leave blank to keep the current key</span></label>
        <input id="sp-value" type="password" bind:value placeholder={'•••• ' + (s.hint || '')} />
      </div>
      <div class="llmfield">
        <label for="sp-provider">Provider <span class="llmhint">optional — groups the picker; required to be a default</span></label>
        <select id="sp-provider" bind:value={provider} onchange={() => { if (!provider) isDefault = false }}>
          {#each PROVIDERS as p}<option value={p}>{PROVIDER_LABEL[p]}</option>{/each}
        </select>
      </div>
      <div class="llmfield">
        <label><input type="checkbox" bind:checked={isDefault} disabled={!provider} /> Default for {PROVIDER_LABEL[provider] || 'its provider'}</label>
      </div>
      {#if err}<p class="muted" style="color:var(--danger);font-size:13px;margin:0">{err}</p>{/if}
      <div class="keyrow" style="justify-content:flex-end">
        <button class="linkbtn" disabled={busy} onclick={cancel}>Cancel</button>
        <button class="open" disabled={busy || !name.trim()} onclick={save}>{busy ? 'Saving…' : 'Save'}</button>
      </div>
    </div>
  {:else}
    <div class="llmrow">
      <div class="llmmeta">
        <div class="llmname">
          {s.name}
          {#if s.default}<span class="llmbadge">default</span>{/if}
        </div>
        <div class="llmsub">
          <span class="llmkey">{s.provider ? PROVIDER_LABEL[s.provider] + ' · ' : ''}{s.hint}{s.used_by?.length ? ` · used by ${s.used_by.length} model${s.used_by.length > 1 ? 's' : ''}` : ''}</span>
        </div>
      </div>
      <button class="linkbtn" disabled={busy || editingId !== null} onclick={() => startEdit(s)}>Edit</button>
      <button class="linkbtn danger" disabled={busy || editingId !== null} onclick={() => remove(s)}>Delete</button>
    </div>
  {/if}
{/each}

{#if editingId === 'new'}
  <div class="llmform">
    <div class="llmfield">
      <label for="sp-name">Name</label>
      <input id="sp-name" bind:value={name} placeholder="e.g. Work OpenAI" />
    </div>
    <div class="llmfield">
      <label for="sp-value">Value</label>
      <input id="sp-value" type="password" bind:value placeholder="paste key" />
    </div>
    <div class="llmfield">
      <label for="sp-provider">Provider <span class="llmhint">optional — groups the picker; required to be a default</span></label>
      <select id="sp-provider" bind:value={provider} onchange={() => { if (!provider) isDefault = false }}>
        {#each PROVIDERS as p}<option value={p}>{PROVIDER_LABEL[p]}</option>{/each}
      </select>
    </div>
    <div class="llmfield">
      <label><input type="checkbox" bind:checked={isDefault} disabled={!provider} /> Default for {PROVIDER_LABEL[provider] || 'its provider'}</label>
    </div>
    {#if err}<p class="muted" style="color:var(--danger);font-size:13px;margin:0">{err}</p>{/if}
    <div class="keyrow" style="justify-content:flex-end">
      <button class="linkbtn" disabled={busy} onclick={cancel}>Cancel</button>
      <button class="open" disabled={busy || !name.trim() || !value.trim()} onclick={save}>{busy ? 'Saving…' : 'Add'}</button>
    </div>
  </div>
{:else if editingId === null}
  <button class="addbtn" disabled={busy} onclick={startAdd}>
    <Icon name="plus" size={14} /> Add secret
  </button>
{/if}
