import { getBidPackages } from '../../lib/api'
import type { BidPackage } from '../../lib/types'

export interface BoqPackageState {
  supported: boolean
  packages: BidPackage[]
}

/**
 * 旧后端没有 `/projects/{ref}/packages`：要么返回 404，要么被 SPA 兜底成
 * 一份 HTML（JSON 解析失败）。两种情况都退回到单份清单的旧界面。
 */
export async function loadBoqPackageState(ref: string): Promise<BoqPackageState> {
  try {
    return { supported: true, packages: await getBidPackages(ref) }
  } catch {
    // 404 / HTML 兜底 / 暂时读取失败都先按旧界面呈现，避免整页被阻断；
    // 清单自身的请求会照常给出具体错误。
    return { supported: false, packages: [] }
  }
}
