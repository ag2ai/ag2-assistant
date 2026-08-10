// Which zod schema describes which gateway response. The key is the real unit of
// the contract — "METHOD path" exactly as the OpenAPI document spells it — because
// the Pydantic names carry a `Response` suffix and cannot be matched by name.
//
// Every JSON route belongs to exactly ONE bucket; routes.test.ts fails otherwise,
// so a new route cannot be added without a decision about its schema.
import type { z } from 'zod'
import { ChatList, MessageReply, Transcript } from './chat.ts'
import {
  Connection,
  ConnectionExposure,
  ConnectionGroups,
  ConnectionList,
  ConnectionPairing,
  PairingCodeIssued,
} from './connection.ts'
import {
  FilesResponse,
  Mentions,
  MkdirResult,
  SearchResults,
  UploadResult,
  WriteResult,
} from './file.ts'
import { FolderList, FolderMutated, FolderRoots, FolderSaved } from './folder.ts'
import {
  LiveConfigList,
  LiveConfigSaved,
  LlmConfigList,
  LlmConfigSaved,
  PingResult,
  ProviderCatalog,
} from './llm.ts'
import { PermissionMutated, PermissionSnapshot, TaskRules } from './permission.ts'
import { Ok } from './primitives.ts'
import { ProfileEnvelope, ProfileList } from './profile.ts'
import { SecretList, SecretSaved } from './secret.ts'
import {
  FocusesSaved,
  LiveOverrideSaved,
  LlmOverrideSaved,
  McpHealth,
  McpServerSaved,
  McpServersSnapshot,
  MemoryDoc,
  ProfileHealth,
  ProfileSettings,
  ReplyTimeoutSaved,
  VoiceCatalog,
  VoiceSelected,
} from './settings.ts'
import {
  ProfileSkillInstalled,
  ProfileSkillList,
  ProfileSkillMutated,
  SkillDiscovered,
  SkillInstalled,
  SkillList,
  SkillMutated,
  SkillSearchResults,
} from './skill.ts'
import {
  HitlPending,
  InquiryList,
  NewTaskEnvelope,
  RunDetailEnvelope,
  RunList,
  TaskEnvelope,
  TaskList,
} from './task.ts'
import {
  CodexLoginUrl,
  CodexStatus,
  CodingAgents,
  CodingCatalog,
  FsListing,
  FsMkdirResult,
  GoogleLoginUrl,
  GoogleStatus,
  Health,
  IdentitySeeded,
  OkOrError,
  StatusList,
  Usage,
  UsageRollup,
} from './system.ts'

export const ROUTES: Record<string, z.ZodTypeAny> = {
  'GET /api/health': Health,
  'GET /api/usage': UsageRollup,
  'GET /api/status': StatusList,
  'GET /api/p/{pid}/health': ProfileHealth,
  'GET /api/p/{pid}/usage': Usage,
  'GET /api/coding/agents': CodingAgents,
  'GET /api/coding/{agent}/models': CodingCatalog,
  'GET /api/fs/list': FsListing,
  'POST /api/fs/mkdir': FsMkdirResult,
  'GET /api/memory': MemoryDoc,
  'POST /api/memory': Ok,
  'GET /api/p/{pid}/memory': MemoryDoc,
  'POST /api/p/{pid}/memory': Ok,
  'POST /api/identity': IdentitySeeded,
  'POST /api/onboarded': Ok,
  'GET /api/p/{pid}/chats': ChatList,
  'GET /api/p/{pid}/chats/{chat_id}': Transcript,
  'PATCH /api/p/{pid}/chats/{chat_id}': Ok,
  'DELETE /api/p/{pid}/chats/{chat_id}': Ok,
  'POST /api/p/{pid}/message': MessageReply,
  'GET /api/p/{pid}/tasks': TaskList,
  'POST /api/p/{pid}/tasks': NewTaskEnvelope,
  'GET /api/p/{pid}/tasks/{task_id}': TaskEnvelope,
  'PATCH /api/p/{pid}/tasks/{task_id}': TaskEnvelope,
  'DELETE /api/p/{pid}/tasks/{task_id}': Ok,
  'POST /api/p/{pid}/tasks/{task_id}/run': RunDetailEnvelope,
  'GET /api/p/{pid}/tasks/{task_id}/runs': RunList,
  'GET /api/p/{pid}/tasks/{task_id}/permissions': TaskRules,
  'DELETE /api/p/{pid}/tasks/{task_id}/permissions': Ok,
  'GET /api/p/{pid}/runs/{run_id}': RunDetailEnvelope,
  'POST /api/p/{pid}/runs/{run_id}/stop': Ok,
  'POST /api/p/{pid}/runs/{run_id}/seen': Ok,
  'GET /api/p/{pid}/inquiries/pending': InquiryList,
  'POST /api/p/{pid}/inquiries/{inquiry_id}/answer': Ok,
  'GET /api/llm-configs': LlmConfigList,
  'GET /api/llm-configs/models': ProviderCatalog,
  'POST /api/llm-configs': LlmConfigSaved,
  'POST /api/llm-configs/test': PingResult,
  'POST /api/llm-configs/{cid}': LlmConfigSaved,
  'DELETE /api/llm-configs/{cid}': Ok,
  'POST /api/llm-configs/{cid}/use': Ok,
  'POST /api/llm-configs/{cid}/test': PingResult,
  'GET /api/live-configs': LiveConfigList,
  'POST /api/live-configs': LiveConfigSaved,
  'POST /api/live-configs/test': PingResult,
  'POST /api/live-configs/{cid}': LiveConfigSaved,
  'DELETE /api/live-configs/{cid}': Ok,
  'POST /api/live-configs/{cid}/use': Ok,
  'POST /api/live-configs/{cid}/test': PingResult,
  'GET /api/secrets': SecretList,
  'POST /api/secrets': SecretSaved,
  'POST /api/secrets/key': Ok,
  'POST /api/secrets/{sid}': SecretSaved,
  'DELETE /api/secrets/{sid}': Ok,
  'GET /api/google/status': GoogleStatus,
  'POST /api/google/credentials': OkOrError,
  'POST /api/google/login_url': GoogleLoginUrl,
  'POST /api/google/logout': Ok,
  'GET /api/codex/status': CodexStatus,
  'POST /api/codex/login_url': CodexLoginUrl,
  'POST /api/codex/submit': Ok,
  'POST /api/codex/logout': Ok,
  'GET /api/profiles': ProfileList,
  'POST /api/profiles': ProfileEnvelope,
  'POST /api/profiles/{pid}': ProfileEnvelope,
  'DELETE /api/profiles/{pid}': Ok,
  'POST /api/profiles/{pid}/restore': ProfileEnvelope,
  'GET /api/connections': ConnectionList,
  // The three writes below answer with the one Connection they changed, not the
  // list — Settings re-renders that row from the response.
  'POST /api/connections': Connection,
  'POST /api/connections/{cid}': Connection,
  'POST /api/connections/{cid}/token': Connection,
  'DELETE /api/connections/{cid}': Ok,
  'POST /api/connections/{cid}/default': Connection,
  'GET /api/connections/{cid}/exposure': ConnectionExposure,
  'POST /api/connections/{cid}/exposure': ConnectionExposure,
  'GET /api/connections/{cid}/pairing': ConnectionPairing,
  'POST /api/connections/{cid}/pairing': ConnectionPairing,
  'DELETE /api/connections/{cid}/pairing/{key}': ConnectionPairing,
  // Alone among the pairing routes: the minted code, not the roster around it.
  'POST /api/connections/{cid}/pairing/code': PairingCodeIssued,
  'GET /api/connections/{cid}/groups': ConnectionGroups,
  'POST /api/connections/{cid}/groups/{chat_id}/profile': ConnectionGroups,
  'GET /api/folders': FolderList,
  // Create and update echo the changed Folder next to the snapshot; delete and the
  // grant routes echo the snapshot alone, because the row they touched may be gone
  // (a Folder left with no grants is garbage-collected on revoke).
  'POST /api/folders': FolderSaved,
  'POST /api/folders/{fid}': FolderSaved,
  'DELETE /api/folders/{fid}': FolderMutated,
  'POST /api/folders/{fid}/grants': FolderMutated,
  'DELETE /api/folders/{fid}/grants': FolderMutated,
  'GET /api/p/{pid}/folders/roots': FolderRoots,
  // One route, two bodies — the branch is whether the requested path is absolute,
  // so the union IS the contract. Branch order is load-bearing: the gate pairs the
  // anyOf members by index.
  'GET /api/p/{pid}/files': FilesResponse,
  'GET /api/p/{pid}/files/search': SearchResults,
  'GET /api/p/{pid}/files/mentions': Mentions,
  'POST /api/p/{pid}/files/upload': UploadResult,
  'POST /api/p/{pid}/files/mkdir': MkdirResult,
  'POST /api/p/{pid}/files/move': Ok,
  'PUT /api/p/{pid}/files/raw': WriteResult,
  'DELETE /api/p/{pid}/files/raw': Ok,
  // Skills come in two scopes of one domain: an /api/skills* write lands in the
  // Global layer and fans a reload out to every profile, its /api/p/{pid} mirror
  // lands in that profile alone. That is why the rows differ — only a profile can
  // resolve `suppressed` and `available`, so the install-wide surface never
  // pretends to.
  'GET /api/skills': SkillList,
  'POST /api/skills/{name}/state': SkillMutated,
  'DELETE /api/skills/{name}': SkillMutated,
  // Search and discover touch no state, so both scopes answer the same shape;
  // search is target-agnostic and has no profile mirror at all.
  'POST /api/skills/search': SkillSearchResults,
  'POST /api/skills/discover': SkillDiscovered,
  'POST /api/skills/discover-upload': SkillDiscovered,
  'POST /api/skills/install': SkillInstalled,
  'POST /api/skills/install-upload': SkillInstalled,
  'GET /api/p/{pid}/skills': ProfileSkillList,
  'POST /api/p/{pid}/skills/{name}/state': ProfileSkillMutated,
  'DELETE /api/p/{pid}/skills/{name}': ProfileSkillMutated,
  'POST /api/p/{pid}/skills/{name}/suppress': ProfileSkillMutated,
  'DELETE /api/p/{pid}/skills/{name}/suppress': ProfileSkillMutated,
  'POST /api/p/{pid}/skills/install': ProfileSkillInstalled,
  'POST /api/p/{pid}/skills/install-upload': ProfileSkillInstalled,
  'POST /api/p/{pid}/skills/discover': SkillDiscovered,
  'POST /api/p/{pid}/skills/discover-upload': SkillDiscovered,
  'GET /api/permissions': PermissionSnapshot,
  // The install-wide writes echo the refreshed snapshot; the task-scoped revoke
  // answers a bare {ok} (see the TaskRules row above) because its caller already
  // holds the one list it touched.
  'POST /api/permissions/commands': PermissionMutated,
  'DELETE /api/permissions/commands': PermissionMutated,
  'GET /api/p/{pid}/settings': ProfileSettings,
  // Add and delete both answer the refreshed list; add also echoes the row it
  // wrote, because a manual add normalises what the form posted (args split, env
  // parsed) and the panel has to show what was actually stored.
  'POST /api/p/{pid}/settings/mcp': McpServerSaved,
  'DELETE /api/p/{pid}/settings/mcp/{name}': McpServersSnapshot,
  // The one probe that spawns a server. An unreachable one is a 200 ok:false —
  // the failure is a fact about the server, not about the request.
  'POST /api/p/{pid}/settings/mcp/{name}/health': McpHealth,
  // Each write echoes the one field it changed: the value the store normalised
  // (focuses), or null for an override that was cleared.
  'POST /api/p/{pid}/settings/focuses': FocusesSaved,
  'POST /api/p/{pid}/settings/llm-override': LlmOverrideSaved,
  'POST /api/p/{pid}/settings/live-override': LiveOverrideSaved,
  'POST /api/p/{pid}/settings/reply-timeout': ReplyTimeoutSaved,
  'POST /api/p/{pid}/settings/voice_provider': Ok,
  'GET /api/p/{pid}/voice/voices': VoiceCatalog,
  'POST /api/p/{pid}/voice/select': VoiceSelected,
  // The transient HITL registry, not the durable inquiries above — a different
  // set, and a thinner row.
  'GET /api/p/{pid}/hitl/pending': HitlPending,
}

// No schema by design — the reason is the point of the entry.
export const UNMAPPED: Record<string, string> = {
  'GET /api/p/{pid}/files/raw': 'FileResponse — raw file bytes, not JSON',
  'POST /api/p/{pid}/voice/preview': 'audio/wav',
  'GET /api/google/callback': 'HTMLResponse — the OAuth redirect landing page',
  'GET /': 'RedirectResponse to /app/',
  'GET /app': 'serves the SPA',
  'GET /app/{path}': 'serves the SPA',
  'GET /{full_path}': 'catch-all: SPA redirect, or 404 for an unknown /api path',
  'GET /{name}.svg': 'FileResponse — image/svg+xml',
  'GET /favicon.ico': 'FileResponse',
  'GET /voices/{name}.wav': 'FileResponse — audio/wav',
  // The unmatched-/api/* write catch-all. It only ever answers a bare 404, so
  // there is no 200 body to describe.
  'DELETE /api/{full_path}': 'api_not_found — bare 404, no body',
  'PATCH /api/{full_path}': 'api_not_found — bare 404, no body',
  'POST /api/{full_path}': 'api_not_found — bare 404, no body',
  'PUT /api/{full_path}': 'api_not_found — bare 404, no body',
  // Mounted by assistant/hitl/desktop.py, not by the gateway's own route table:
  // the desktop HITL page posts to them itself, the SPA never does, so neither
  // has a zod twin to check against.
  'GET /hitl/{req_id}': 'HTMLResponse — the server-rendered HITL question page',
  'POST /hitl/{req_id}/answer': 'the HITL page posts to itself; not part of the SPA contract',
}

// Empty, and it stays that way: the rollout is done, so a route with no schema
// is now a route someone forgot rather than one whose phase has not arrived. A
// new route belongs in ROUTES, or in UNMAPPED with the reason it has no body to
// describe.
export const PENDING: Record<string, string> = {}
