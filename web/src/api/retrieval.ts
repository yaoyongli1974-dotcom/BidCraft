/**
 * 项目级检索预览的 API 门面。实现与 KB 预览共用归一化器，
 * 端点固定为 GET /projects/{ref}/retrieval/preview?q=&limit=。
 */
export {
  getRetrievalPreview,
  normalizeRetrievalPassage,
  previewProjectRetrieval,
  previewRetrieval,
  previewRetrievalPassages,
  type RetrievalPreview,
} from './knowledge'
