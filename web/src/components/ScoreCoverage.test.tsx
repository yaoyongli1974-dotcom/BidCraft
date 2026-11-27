import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ScoreCoverage } from './ScoreCoverage'
import type { ScoringCoverageReport } from '../lib/types'

const report: ScoringCoverageReport = {
  project_id: 'p1',
  total_items: 3,
  total_score: 100,
  covered_score: 40,
  weak_score: 10,
  covered_ratio: 0.4,
  uncovered_count: 1,
  issues: ['评分项「业绩」未找到对应章节'],
  rows: [
    {
      id: 's1',
      name: '技术方案完整性',
      score: 40,
      category: 'technical',
      section_hint: '技术方案',
      status: 'covered',
      status_label: '已覆盖',
      nodes: [{ id: 'n1', heading: '技术方案', words: 1500, has_body: true, review_status: 'draft', reasons: ['标题匹配'] }],
    },
    {
      id: 's2',
      name: '业绩证明',
      score: 10,
      category: 'performance',
      section_hint: '业绩',
      status: 'weak',
      status_label: '覆盖薄弱',
      nodes: [{ id: 'n2', heading: '业绩清单', words: 40, has_body: true, review_status: 'draft', reasons: ['标题匹配'] }],
    },
    {
      id: 's3',
      name: '售后服务',
      score: 50,
      category: 'service',
      section_hint: '售后',
      status: 'uncovered',
      status_label: '未覆盖',
      nodes: [],
    },
  ],
}

describe('ScoreCoverage', () => {
  it('空数据或只有无效评分项时给出重新提取提示', () => {
    render(<ScoreCoverage report={null} />)
    expect(screen.getByText(/没有可计算的有效评分项/)).toBeTruthy()
  })

  it('总分为 0 时不显示伪造的覆盖率', () => {
    render(<ScoreCoverage report={{ ...report, total_items: 2, total_score: 0, rows: [] }} />)
    expect(screen.getByText(/没有可计算的有效评分项/)).toBeTruthy()
  })

  it('渲染汇总指标与覆盖百分比', () => {
    render(<ScoreCoverage report={report} />)
    expect(screen.getByText(/40% 分值已覆盖/)).toBeTruthy()
    expect(screen.getByText((_t, el) => (el?.textContent || '').replace(/\s/g, '') === '未覆盖1项')).toBeTruthy()
    expect(screen.getByText(/评分项「业绩」未找到对应章节/)).toBeTruthy()
  })

  it('逐条显示评分点的覆盖状态与匹配章节', () => {
    render(<ScoreCoverage report={report} />)
    expect(screen.getByText('技术方案完整性')).toBeTruthy()
    expect(screen.getByText('技术方案')).toBeTruthy()
    expect(screen.getByText('业绩证明')).toBeTruthy()
    expect(screen.getByText('未找到对应章节')).toBeTruthy()
  })

  it('loading 时不渲染矩阵', () => {
    render(<ScoreCoverage report={report} loading />)
    expect(screen.getByText(/正在计算评分点/)).toBeTruthy()
  })
})
