import { api as P, globalApi as G } from '../../lib/profile.ts'
import { del, get, post } from '../http.ts'
import type { CardSaveRequest } from '../../schemas/card.ts'
import { CardSave, CardList, CardMutated, ProfileCardList, ProfileCardMutated } from '../../schemas/card.ts'

export const cardsApi = {
  saveCard: (chat: string, request: CardSaveRequest) =>
    post(P('/chats/' + encodeURIComponent(chat) + '/cards/save'), request, CardSave),
  cards: () => get(G('/cards'), CardList),
  setCardState: (name: string, enabled: boolean) =>
    post(G('/cards/' + encodeURIComponent(name) + '/state'), { enabled }, CardMutated),
  deleteCard: (name: string) => del(G('/cards/' + encodeURIComponent(name)), CardMutated),
  profileCards: () => get(P('/cards'), ProfileCardList),
  setProfileCardState: (name: string, enabled: boolean) =>
    post(P('/cards/' + encodeURIComponent(name) + '/state'), { enabled }, ProfileCardMutated),
  suppressCard: (name: string, suppressed: boolean) =>
    suppressed
      ? post(P('/cards/' + encodeURIComponent(name) + '/suppress'), undefined, ProfileCardMutated)
      : del(P('/cards/' + encodeURIComponent(name) + '/suppress'), ProfileCardMutated),
  deleteProfileCard: (name: string) =>
    del(P('/cards/' + encodeURIComponent(name)), ProfileCardMutated),
}
