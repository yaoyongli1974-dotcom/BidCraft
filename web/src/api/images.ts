import { asArray, jsonBody, request, uploadForm } from '../lib/http'
import type { ImageAsset, ImageCreateInput, ImagePlan, ImagePlanApplyResult, ImagePolicy } from '../lib/types'
import { asLooseBool, asNumber, asText, normalizeImagePlanDocument } from './shared'

// ---------------------------------------------------------------- images domain
export async function listImages(ref: string, nodeId?: string): Promise<ImageAsset[]> {
  const query = nodeId ? `?node_id=${encodeURIComponent(nodeId)}` : ''
  const data = await request<ImageAsset[] | { images?: ImageAsset[] }>(
    `/projects/${encodeURIComponent(ref)}/images${query}`,
  )
  return asArray<ImageAsset>(data, 'images')
}

/** 与后端 DEFAULT_POLICY 对齐；字段缺省时用它兜底，旧后端也不会崩。 */

export const DEFAULT_IMAGE_POLICY: ImagePolicy = {
  max_total: 12,
  style: '',
  size: 'auto',
  captions: true,
  caption_prefix: '图',
  width_cm: 15,
}

export function normalizeImagePolicy(value: unknown): ImagePolicy {
  const raw = value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
  const maxTotal = Math.round(
    Math.min(60, Math.max(0, asNumber(raw.max_total, DEFAULT_IMAGE_POLICY.max_total))),
  )
  const widthRaw = asNumber(raw.width_cm, DEFAULT_IMAGE_POLICY.width_cm)
  const width = Math.min(19, Math.max(8, Math.round(widthRaw * 100) / 100))
  const size = asText(raw.size, DEFAULT_IMAGE_POLICY.size)
  return {
    max_total: maxTotal,
    style: asText(raw.style, DEFAULT_IMAGE_POLICY.style).slice(0, 80),
    size: size === 'auto' || /^\d{2,4}x\d{2,4}$/i.test(size) ? size : 'auto',
    captions: asLooseBool(raw.captions, DEFAULT_IMAGE_POLICY.captions),
    caption_prefix: asText(raw.caption_prefix, '').slice(0, 10) || DEFAULT_IMAGE_POLICY.caption_prefix,
    width_cm: width,
  }
}

export async function getImagePolicy(ref: string): Promise<ImagePolicy> {
  const data = await request<Partial<ImagePolicy>>(
    `/projects/${encodeURIComponent(ref)}/image-policy`,
  )
  return normalizeImagePolicy(data)
}

export async function updateImagePolicy(
  ref: string,
  patch: Partial<ImagePolicy>,
): Promise<ImagePolicy> {
  const data = await request<Partial<ImagePolicy>>(
    `/projects/${encodeURIComponent(ref)}/image-policy`,
    { method: 'PUT', body: JSON.stringify(patch) },
  )
  return normalizeImagePolicy(data)
}

export async function getImagePlan(ref: string): Promise<ImagePlan> {
  const data = await request<Partial<ImagePlan>>(
    `/projects/${encodeURIComponent(ref)}/images/plan`,
  )
  const summary = {
    placeholders: data.summary?.placeholders ?? 0,
    ready: data.summary?.ready ?? 0,
    failed: data.summary?.failed ?? 0,
    missing: data.summary?.missing ?? 0,
  }
  const plan: ImagePlan = {
    chapters: Array.isArray(data.chapters) ? data.chapters : [],
    summary,
  }
  if (data.policy !== undefined && data.policy !== null) {
    plan.policy = normalizeImagePolicy(data.policy)
  }
  const document = normalizeImagePlanDocument(data.document, plan.policy, summary)
  if (document) plan.document = document
  return plan
}

export async function applyImagePlan(ref: string): Promise<ImagePlanApplyResult> {
  const data = await request<Partial<ImagePlanApplyResult>>(
    `/projects/${encodeURIComponent(ref)}/images/apply-plan`,
    { method: 'POST' },
  )
  return {
    applied: data.applied ?? 0,
    skipped_missing: data.skipped_missing ?? 0,
    skipped_failed: data.skipped_failed ?? 0,
    skipped_over_budget: data.skipped_over_budget ?? 0,
    skipped_over_total: data.skipped_over_total ?? 0,
    applied_labels: Array.isArray(data.applied_labels) ? data.applied_labels : [],
    chapters: Array.isArray(data.chapters) ? data.chapters : [],
  }
}

export const createImage = (ref: string, input: ImageCreateInput) =>
  request<ImageAsset>(`/projects/${encodeURIComponent(ref)}/images`, jsonBody(input))

export const regenerateImage = (ref: string, imageId: string) =>
  request<ImageAsset>(
    `/projects/${encodeURIComponent(ref)}/images/${encodeURIComponent(imageId)}/regenerate`,
    { method: 'POST' },
  )

/**
 * 上传本地图片作为配图。multipart 传输使用 XHR，以便复用全局 401/ApiError 处理。
 */

export function uploadImage(
  ref: string,
  file: File,
  meta: { label: string; node_id?: string },
): Promise<ImageAsset> {
  const form = new FormData()
  form.append('file', file)
  form.append('label', meta.label)
  if (meta.node_id) form.append('node_id', meta.node_id)
  return uploadForm<ImageAsset>(
    `/projects/${encodeURIComponent(ref)}/images/upload`,
    form,
  )
}

export const deleteImage = (ref: string, imageId: string) =>
  request<{ deleted: boolean }>(
    `/projects/${encodeURIComponent(ref)}/images/${encodeURIComponent(imageId)}`,
    { method: 'DELETE' },
  )
