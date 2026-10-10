import { api as P } from '../../lib/profile.ts'
import { post } from '../http.ts'
import { CardSourceResponse, type SourceTarget } from '../../schemas/card_source.ts'

export const cardSourceApi = {
  refreshCardSource: (target: SourceTarget, trigger: 'manual' | 'shown' | 'interval' = 'manual') =>
    post(P('/card-sources/refresh'), { ...target, trigger }, CardSourceResponse),
  approveCardSource: (target: SourceTarget, version: string, approved: boolean, secrets: Record<string, string>) =>
    post(P('/card-sources/approval'), { ...target, code_version: version, approved, secrets }, CardSourceResponse),
}
