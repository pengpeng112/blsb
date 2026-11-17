// 兼容层（LT-02）：全部实现迁至 src/api/，此文件保留所有旧导出名——
// DrugTrace 等 14 个旧引用方零修改可用。新增代码请从 './api'（目录）导入。
export {
  token, setToken, clearLogin, savedBackends, setBackends,
  coreBase, storageBase, ApiError,
} from './api/http'
import { core, storage as storageApi } from './api/http'
export { core, storageApi as storage }
export { login, loginWithToken, currentUser, logout, changePassword } from './api/auth'

/** 旧形状门面：api.coreGet/corePost/storageGet（签名与旧实现一致）。 */
export const api = {
  coreGet: (p: string) => core.get(p),
  corePost: (p: string, b?: any, idemKey?: string, extraHeaders?: Record<string, string>) =>
    core.post(p, b, idemKey, extraHeaders),
  storageGet: (p: string) => storageApi.get(p),
  storagePost: (p: string, b?: any, idemKey?: string) => storageApi.post(p, b, idemKey),
}
