import { api as P } from '../../lib/profile.ts'
import { post } from '../http.ts'
import { CardSave } from '../../schemas/card.ts'
import type { CardSaveRequest } from '../../schemas/card.ts'

export const cardsApi = {
  saveCard: (chat: string, request: CardSaveRequest) =>
    post(P('/chats/' + encodeURIComponent(chat) + '/cards/save'), request, CardSave),
}
