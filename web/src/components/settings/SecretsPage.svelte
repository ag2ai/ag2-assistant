<script lang="ts">
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
  import { errText } from '../../lib/errors.ts'
  import { m } from '../../paraglide/messages.js'
  import type { Secret } from '../../schemas/index.ts'
  import Icon from '../Icon.svelte'

  const PROVIDERS = ['', 'openai', 'anthropic', 'gemini']
  // Vendor names are product names; only the empty "no provider" entry localizes.
  const PROVIDER_LABEL: Record<string, string | undefined> = { '': m.secrets_no_provider(), openai: 'OpenAI', anthropic: 'Anthropic', gemini: 'Gemini' }

  const secrets = $derived($secretsStore.secrets)

  // secret id being edited, 'new' for the add form, null when closed
  let editingId = $state<string | null>(null)
  let name = $state('')
  let value = $state('')
  let provider = $state('')
  let isDefault = $state(false)
  let busy = $state(false)
  let err = $state('')

  onMount(loadSecrets)

  function startAdd() { editingId = 'new'; name = ''; value = ''; provider = ''; isDefault = false; err = '' }
  function startEdit(s: Secret) { editingId = s.id; name = s.name; value = ''; provider = s.provider; isDefault = s.default; err = '' }
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
      } else if (editingId) {
        await api.updateSecret(editingId, {
          name: name.trim(),
          value: value !== '' ? value.trim() : null,  // blank keeps the stored value
          provider,
          default: isDefault,
        })
      }
      cancel()
      await refresh()
    } catch (e) { err = errText(e) }
    busy = false
  }

  async function remove(s: Secret) {
    const used = s.used_by?.length
      ? m.secrets_delete_used({ count: s.used_by.length, list: s.used_by.join(', ') })
      : ''
    if (!confirm(`${m.secrets_delete_confirm({ name: s.name })}${used}`)) return
    busy = true; err = ''
    try {
      await api.deleteSecret(s.id)
      await refresh()
    } catch (e) { err = errText(e) }
    busy = false
  }
</script>

<div class="setgroup">{m.secrets_title()}</div>
<p class="setsub">
  {m.secrets_lead()}
</p>

{#if err && editingId === null}<p class="muted" style="color:var(--danger);font-size:13px">{err}</p>{/if}

{#if !secrets.length && editingId === null}
  <p class="muted" style="font-size:13px">{m.secrets_empty()}</p>
{/if}

{#each secrets as s (s.id)}
  {#if editingId === s.id}
    <div class="llmform">
      <div class="llmfield">
        <label for="sp-name">{m.field_name()}</label>
        <input id="sp-name" bind:value={name} placeholder={m.secrets_name_placeholder()} />
      </div>
      <div class="llmfield">
        <label for="sp-value">{m.secrets_field_value()} <span class="llmhint">{m.secrets_value_hint()}</span></label>
        <input id="sp-value" type="password" bind:value placeholder={'•••• ' + (s.hint || '')} />
      </div>
      <div class="llmfield">
        <label for="sp-provider">{m.llm_field_provider()} <span class="llmhint">{m.secrets_provider_hint()}</span></label>
        <select id="sp-provider" bind:value={provider} onchange={() => { if (!provider) isDefault = false }}>
          {#each PROVIDERS as p}<option value={p}>{PROVIDER_LABEL[p]}</option>{/each}
        </select>
      </div>
      <div class="llmfield">
        <label><input type="checkbox" bind:checked={isDefault} disabled={!provider} /> {m.secrets_default_for({ name: PROVIDER_LABEL[provider] || m.secrets_its_provider() })}</label>
      </div>
      {#if err}<p class="muted" style="color:var(--danger);font-size:13px;margin:0">{err}</p>{/if}
      <div class="keyrow" style="justify-content:flex-end">
        <button class="linkbtn" disabled={busy} onclick={cancel}>{m.action_cancel()}</button>
        <button class="open" disabled={busy || !name.trim()} onclick={save}>{busy ? m.action_saving() : m.action_save()}</button>
      </div>
    </div>
  {:else}
    <div class="llmrow">
      <div class="llmmeta">
        <div class="llmname">
          {s.name}
          {#if s.default}<span class="llmbadge">{m.llm_badge_default()}</span>{/if}
        </div>
        <div class="llmsub">
          <span class="llmkey">{s.provider ? PROVIDER_LABEL[s.provider] + ' · ' : ''}{s.hint}{s.used_by?.length ? ` · ${m.secrets_used_by({ count: s.used_by.length })}` : ''}</span>
        </div>
      </div>
      <button class="linkbtn" disabled={busy || editingId !== null} onclick={() => startEdit(s)}>{m.action_edit_short()}</button>
      <button class="linkbtn danger" disabled={busy || editingId !== null} onclick={() => remove(s)}>{m.action_delete()}</button>
    </div>
  {/if}
{/each}

{#if editingId === 'new'}
  <div class="llmform">
    <div class="llmfield">
      <label for="sp-name">{m.field_name()}</label>
      <input id="sp-name" bind:value={name} placeholder={m.secrets_name_placeholder()} />
    </div>
    <div class="llmfield">
      <label for="sp-value">{m.secrets_field_value()}</label>
      <input id="sp-value" type="password" bind:value placeholder={m.secrets_paste_key()} />
    </div>
    <div class="llmfield">
      <label for="sp-provider">{m.llm_field_provider()} <span class="llmhint">{m.secrets_provider_hint()}</span></label>
      <select id="sp-provider" bind:value={provider} onchange={() => { if (!provider) isDefault = false }}>
        {#each PROVIDERS as p}<option value={p}>{PROVIDER_LABEL[p]}</option>{/each}
      </select>
    </div>
    <div class="llmfield">
      <label><input type="checkbox" bind:checked={isDefault} disabled={!provider} /> {m.secrets_default_for({ name: PROVIDER_LABEL[provider] || m.secrets_its_provider() })}</label>
    </div>
    {#if err}<p class="muted" style="color:var(--danger);font-size:13px;margin:0">{err}</p>{/if}
    <div class="keyrow" style="justify-content:flex-end">
      <button class="linkbtn" disabled={busy} onclick={cancel}>{m.action_cancel()}</button>
      <button class="open" disabled={busy || !name.trim() || !value.trim()} onclick={save}>{busy ? m.action_saving() : m.action_add()}</button>
    </div>
  </div>
{:else if editingId === null}
  <button class="addbtn" disabled={busy} onclick={startAdd}>
    <Icon name="plus" size={14} /> {m.secrets_add()}
  </button>
{/if}
