import { api as P } from '../../lib/profile.ts'
import { get, post } from '../http.ts'
import { CardInstance, CardInstanceSave, type CardInstanceSaveRequest } from '../../schemas/card_instance.ts'

export const cardInstancesApi = {
  saveCardInstance: (chat: string, request: CardInstanceSaveRequest) =>
    post(P('/chats/' + encodeURIComponent(chat) + '/card-instances/save'), request, CardInstanceSave),
  cardInstance: (path: string) => get(P('/card-instances?path=' + encodeURIComponent(path)), CardInstance),
}
