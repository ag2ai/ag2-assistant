import { api as P } from '../../lib/profile.ts'
import { get } from '../http.ts'
import { Screen, ScreenList } from '../../schemas/screen.ts'

export const screensApi = {
  screens: () => get(P('/screens'), ScreenList),
  screen: (path: string) => get(P('/screens/view?path=' + encodeURIComponent(path)), Screen),
}
