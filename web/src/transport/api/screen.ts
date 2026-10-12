import { api as P } from '../../lib/profile.ts'
import { get, patch } from '../http.ts'
import { Screen, ScreenList, ScreenRow } from '../../schemas/screen.ts'

export const screensApi = {
  screens: () => get(P('/screens'), ScreenList),
  screen: (path: string) => get(P('/screens/view?path=' + encodeURIComponent(path)), Screen),
  updateScreenTitle: (path: string, title: string) => patch(P('/screens/title'), { path, title }, ScreenRow),
}
