import { test } from 'node:test'
import assert from 'node:assert/strict'
import { ProfileSkill, Skill, SkillInstalled, SkillSearchResults } from './skill.ts'

test('Skill carries the install-wide row', () => {
  const parsed = Skill.parse({ name: 'pdf', description: 'read pdfs', origin: 'global', enabled: true })
  assert.equal(parsed.origin, 'global')
})

test('Skill rejects an origin outside bundled/global/profile', () => {
  assert.throws(() => Skill.parse({ name: 'x', description: '', origin: 'remote', enabled: true }))
})

test('ProfileSkill adds the per-profile resolution to the same row', () => {
  const parsed = ProfileSkill.parse({
    name: 'pdf', description: '', origin: 'bundled', enabled: true,
    suppressed: true, available: false,
  })
  assert.equal(parsed.available, false)
})

test('SkillSearchResults carries the registry install id and install count', () => {
  const parsed = SkillSearchResults.parse({
    results: [{ name: 'pdf', install_id: 'acme/skills/pdf', description: '', installs: 12 }],
  })
  assert.equal(parsed.results[0].install_id, 'acme/skills/pdf')
})

// skills_install.py install_from_source / registry_install both return {name,
// description} rows — not bare names.
test('SkillInstalled reports installed rows, not plain names', () => {
  const parsed = SkillInstalled.parse({
    ok: true,
    installed: [{ name: 'pdf', description: 'read pdfs' }],
    skills: [{ name: 'pdf', description: 'read pdfs', origin: 'global', enabled: true }],
  })
  assert.equal(parsed.installed[0].name, 'pdf')
})

test('SkillInstalled rejects the bare-name shape', () => {
  assert.throws(() => SkillInstalled.parse({ ok: true, installed: ['pdf'], skills: [] }))
})
