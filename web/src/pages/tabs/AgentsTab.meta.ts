/** orchestration/registry.py 的 APPROVAL_TYPES；未知类型保留原值，便于排查新闸门。 */
export const APPROVAL_TYPE_META: Record<string, string> = {
  technical: '技术审批',
  commercial: '商务审批',
  asset: '资质资产审批',
  qualification: '资格资质审批',
  price: '报价审批',
  compliance: '合规审批',
  delivery: '交付审批',
}

export function approvalTypeLabel(type: string): string {
  return APPROVAL_TYPE_META[type] || type || '未分类审批'
}
