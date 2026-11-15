# C1 领域规格：事实台账、标书包与规则表达式

> 本文件是 BidCraft 的契约层（C1）。代码、YAML 包、测试都以此为准。
> 目标：把“招标文件事实”和“行业检查知识”从程序里彻底拆出来，做到**换行业不改代码**。

## 1. 边界

- 只从上游项目借**行为规格**（字段、检查项、阈值、流程），不复制代码；上游均为 AGPL，本项目按 C 方案清权重写。
- 生成本地优先：唯一网络调用是用户显式配置的 OpenAI 兼容模型端点；无遥测、无云端上传。
- 不在 C1 范围：OCR、PDF 成品导出、Excel 评分对齐、Web UI、插件市场。

## 2. 事实台账（Fact Ledger）

机器可读 Schema：[`bidcraft/resources/schema/fact_ledger.schema.json`](../bidcraft/resources/schema/fact_ledger.schema.json)
（由 `bidcraft.schemas.FactLedger` 直接生成，改模型后运行 `python -m bidcraft.tools.dump_schema` 重新生成）。

### 2.1 顶层结构

| 字段 | 类型 | 说明 |
|---|---|---|
| `schema_version` | string | 当前 `1.0` |
| `package` | string | 产生台账的行业包名 |
| `project` | object | 项目标识、源文件、状态等元数据 |
| `facts` | FactItem[] | **标量事实**，`key` 唯一 |
| `materials` | MaterialItem[] | 设备/材料清单（数量单位必须可核） |
| `parameters` | ParameterItem[] | 技术参数逐条要求 |
| `scoring` | ScoringItem[] | 评分办法条目（分值与评分标准） |
| `requirements` | RequirementItem[] | 实质性/资格/格式要求 |
| `risks` | RiskItem[] | 废标、否决、★/▲ 等风险条款 |
| `template_sections` | string[] | 招标文件“投标文件组成/格式章”骨架 |
| `stats` | object | 解析统计（字段命中率、告警） |

### 2.2 FactItem

```json
{
  "key": "project.budget",
  "label": "预算金额",
  "value": "350",
  "unit": "万元",
  "status": "extracted",
  "confidence": 0.97,
  "required": true,
  "source": {"file": "tender.pdf", "page": 3, "quote": "预算金额：350万元"}
}
```

`status` 语义（生成门禁依赖它）：

- `extracted`：规则命中，带出处；可被人工覆盖。
- `confirmed`：人工核对过。
- `manual`：人工补录，无自动出处。
- `missing`：未命中。`required=true` 时阻止生成。
- `conflict`：多处取值冲突，必须人工裁决。

只有 `value` 非空且 `status ∈ {extracted, confirmed, manual}` 的事实会注入正文。
`{{待填:字段}}` 占位符用于无法从台账得到的信息，导出时标红。

### 2.3 生成门禁

`rules.engine.ledger_gate()` 在写作前检查 `missing_required()`：

- 有缺失必填项 → 拒绝生成，列出 key 与建议命令（`bidcraft fact ...`）。
- 全部就绪 → 允许生成；写手提示词只允许使用台账事实。

## 3. 标书包（Package YAML）

位置：`bidcraft/resources/packages/*.yaml`。管理员覆盖包放在
`BIDCRAFT_HOME/packages/`，同名覆盖内置；`hidden.json` 记录从新建项目列表隐藏的包，
但不影响已有项目加载。控制台入口：`/admin` →「规则包」。

```yaml
package: weakcurrent-cabling
version: 1
project_type: weakcurrent
project_type_label: 弱电智能化
title: 弱电综合布线
extends: [common-bid-facts]      # 递归继承；字段按 key 覆盖，检查按 id 合并
parse:
  keywords: [综合布线, 弱电]
  heading_patterns: ["第[一二三四五六七八九十]+章\\s*.+"]
  table_hints:
    scoring: [评审因素, 分值, 评分标准]
    materials: [名称, 规格, 单位, 数量]
  parameter_vocab: [信道, 永久链路, 衰减]
fields:                           # 事实定义
  - key: cabling.point_count
    label: 信息点数量
    group: 技术
    aliases: [信息点数, 点位数量]
    required: true
    type: number                  # text|number|money|datetime|duration
outline:
  mode: replace                   # replace|append
  items:
    - heading: 项目理解与需求分析
      intent: 复述建设目标与范围，不虚构
      target_words: 1200
      needs: [project.name, project.content]
checks:
  - id: CBL-001
    level: blocking               # blocking|critical|major|minor|info
    category: 实质性响应
    assert: "number(facts.cabling.point_count) > 0"
    message: 未识别信息点总数
    recommendation: 在台账中补录 cabling.point_count
```

### 3.1 项目类型

`project_type` 用于新建项目时分组，固定值：`common`、`weakcurrent`、`fire`、
`installation`、`civil`、`hvac`、`decoration`、`other`。`project_type_label` 是展示名；
缺失时抽象包归入「通用」，其余归入「其他」。

### 3.2 继承规则

- `extends` 可多层，检测环。
- `fields` 同 key 覆盖，新 key 追加。
- `outline` 默认整体替换；`mode: append` 追加到父级之后。
- `checks` 同 id 覆盖，新 id 追加。
- `parse` 深合并；列表字段以子包为准。

### 3.3 字段类型

`text`（默认）、`number`、`money`（元/万元归一）、`datetime`（ISO）、`duration`（天/日历天/月/年）。
提取值会做类型校验；不合法则视为未命中并记录告警，不写入脏值。

## 4. 规则表达式（安全子集）

`bidcraft/rules/expr.py` 用 Python AST 白名单求值：禁止属性访问、下标、导入、lambda、推导式；
限制 400 节点 / 2000 字符。只暴露纯函数，**不执行任意代码**。

### 4.1 上下文

| 变量 | 形态 |
|---|---|
| `facts` | `{key: value}`，缺失时为空串 |
| `materials` | `[{name,spec,unit,qty}]` |
| `parameters` | `[{name,value,unit,requirement,mandatory}]` |
| `scoring` | `[{name,score,criteria}]` |
| `requirements` / `risks` | `[{text,...}]` |
| `sections` | `{heading: body}` |
| `doc` | 全书纯文本 |
| `meta` | `{project_id,name,package}` |

### 4.2 函数

| 函数 | 说明 |
|---|---|
| `present(x)` / `missing(x)` | 非空判断 |
| `number(x)` | 提取数值（支持“350万元”“90日历天”） |
| `duration_days(x)` | 工期折算天数 |
| `contains(text, kw)` / `contains_any(text, [kw...])` | 文本包含 |
| `ratio(a, b)` | `a/b`，b=0 返回 0 |
| `materials_total(unit=None, include=None, exclude=None)` | 清单数量求和；默认排除米/公里等长度单位 |
| `storage_tb(cameras, days, mbps)` | 视频存储容量 TB：`n×天×86400×Mbps/8/1e6` |
| `param_response_ratio()` | 参数逐条响应覆盖率 |
| `param_response_missing()` | 未响应参数名列表 |
| `count_matches(pattern, text=None)` | 正则计数 |
| `has_section(kw)` / `section_text(kw)` | 章节存在/取正文 |
| `all/any/len/min/max/sum/round/abs/str/bool` | 通用 |

### 4.3 检查结果

`CheckResult`：`code, level, category, message, location, finding, recommendation, evidence, passed`。
`evidence` 可带 `show` 字段声明展示表达式，便于人工复核（例如 `materials_total` 的逐项和）。
汇总：任一 `blocking` 未通过 → `blocked`；否则有 `critical/major` → `warn`；否则 `pass`。

## 5. 输入与交付格式

- 招标文件：PDF、DOCX、TXT、XLSX/XLSM。Excel 的每个工作表按一个“页”处理，连续非空行自动构成表格，因此工程量清单与评分表可直接被材料/评分抽取复用。
- 交付：DOCX、Markdown、PDF（本机 LibreOffice）、XLSX（事实台账/材料/参数/评分/风险五张表）。
- HTTP：`bidcraft serve` 暴露项目创建、上传解析、大纲、写作、核对、导出接口；默认只监听 127.0.0.1。

## 6. 命令与产物

```bash
bidcraft init 某项目 --package weakcurrent-cabling
bidcraft ingest <project> 招标文件.pdf --role tender
bidcraft show <project>                 # 解析 + 台账摘要
bidcraft fact <project> project.budget=350 --unit 万元   # 人工修正
bidcraft outline <project> --mode scoring
bidcraft write <project> --stream
bidcraft check <project>
bidcraft export <project> -f docx
bidcraft export <project> -f pdf        # LibreOffice，自动刷新 TOC 页码
bidcraft schema --out ledger.schema.json
bidcraft run <project> 招标文件.pdf      # 全链路
```

产物落在 `BIDCRAFT_HOME/projects/<id>/`：源文件、`output/标书.docx`、`output/标书.md`；
台账可用 `bidcraft ledger-json` 导出为符合本 Schema 的 JSON。

## 7. 内置行业包

| 包 | 项目类型 | 关键事实 | 关键检查 |
|---|---|---|---|
| `weakcurrent-cabling` | 弱电 | 信息点数、线缆等级、光纤等级、测试标准、子系统 | 点位/清单一致、参数响应率、标准覆盖 |
| `weakcurrent-cctv` | 弱电 | 摄像机数、分辨率、编码、存储天数、码率、存储容量、平台协议 | 存储公式、GB28181、参数响应率 |
| `weakcurrent-ba` | 弱电 | DDC 数、总点位、DI/DO/AI/AO、协议、节能策略 | 点位加和、协议、参数响应率 |
| `fire-protection` | 消防 | 系统范围、建筑面积、喷头/探测器数量、消防水量、验收标准 | 喷头/探测器数量、验收标准、系统覆盖 |
| `installation` | 安装 | 安装范围、专业系统、管材、设备数量、试压标准 | 参数响应、试压标准、专业覆盖 |
| `civil` | 土建 | 建筑面积、结构形式、层数、基础形式、混凝土等级、质量目标 | 参数响应、验收标准、分部分项覆盖 |
| `hvac` | 暖通 | 系统范围、冷热负荷、设备数量、冷媒、风管材质、验收标准 | 参数响应、验收标准、系统覆盖 |
| `decoration` | 装修 | 装饰范围、面积、标准、材料、防火等级、环保标准 | 参数响应、防火/环保、分项覆盖 |

行业知识以 YAML 增量维护：新增行业 = 新建一个 `extends: common-bid-facts` 的包，
可由开发者加入安装目录，也可由超级管理员在 `/admin` →「规则包」中创建，不改 Python。
