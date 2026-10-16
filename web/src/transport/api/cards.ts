import { api as P, globalApi as G } from '../../lib/profile.ts'
import { del, get, post } from '../http.ts'
import type { CardSaveRequest } from '../../schemas/card.ts'
import { CardSave, CardList, CardMutated, ProfileCardList, ProfileCardMutated } from '../../schemas/card.ts'

export const cardsApi = {
  saveCard: (chat: string, request: CardSaveRequest) =>
    post(P('/chats/' + encodeURIComponent(chat) + '/cards/save'), request, CardSave),
  cards: () => get(G('/cards'), CardList),
  setCardState: (name: string, enabled: boolean) =>
    post(G('/cards/state?name=' + encodeURIComponent(name)), { enabled }, CardMutated),
  deleteCard: (name: string) => del(G('/cards?name=' + encodeURIComponent(name)), CardMutated),
  profileCards: () => get(P('/cards'), ProfileCardList),
  setProfileCardState: (name: string, enabled: boolean) =>
    post(P('/cards/state?name=' + encodeURIComponent(name)), { enabled }, ProfileCardMutated),
  suppressCard: (name: string, suppressed: boolean) =>
    suppressed
      ? post(P('/cards/suppress?name=' + encodeURIComponent(name)), undefined, ProfileCardMutated)
      : del(P('/cards/suppress?name=' + encodeURIComponent(name)), ProfileCardMutated),
  deleteProfileCard: (name: string) =>
    del(P('/cards?name=' + encodeURIComponent(name)), ProfileCardMutated),
}
