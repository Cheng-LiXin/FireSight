# 大模型火情分析与防火建议报告 —— 设计

> 日期：2026-09-29 · 状态：已评审设计，待实现
> 需求原话：「大模型自动生成火情分析与防火建议报告」

## 1. 目标与非目标

**目标**：在值班控制台里，对选定时间段的告警数据生成一份可归档、可下载的
Markdown 报告——火情态势分析 + 分场所防火建议 + 处置优先级。

**非目标**：定时自动生成、邮件/消息推送、对话式追问、RAG、多语言、
数据质检与训练质量诊断（后者路线图里有，但本期只做火情分析与建议）。

## 2. 关键决策

| 决策 | 选择 | 理由 |
|---|---|---|
| 模型通道 | 可配置（默认 DeepSeek，可切 SiliconFlow / Claude / 自定义） | 现场换模型只改环境变量；本机有 DeepSeek、SiliconFlow 的 key，无 Claude key |
| 报告内容 | 火情分析 + 防火建议 | 对齐需求原话；消防人员真正会看的部分 |
| 生成方式 | 手动触发 + Markdown 落盘归档 | 每份可追溯、可对比；不做定时器 |
| 任务模型 | 后台任务 + 轮询 | 与 `eval_jobs.py` 同模式：生成要几十秒，同步 POST 会超时；进度粒度是「一次生成」，不必上 SSE |

## 3. 架构

```
ReportView.vue ──POST /api/reports──▶ ReportJobManager（后台线程）
                     │                     │
                     │                     ├─▶ ReportService.aggregate()   统计摘要
                     │                     ├─▶ LlmClient.chat()            生成正文
                     │                     └─▶ data/reports/<id>.md|.json  归档
                     │
              GET /api/reports/jobs/{id}   轮询状态
              GET /api/reports[/{id}]      历史与全文
```

模块边界：

| 模块 | 职责 | 依赖 |
|---|---|---|
| `fire_sight/services/llm.py` | 模型通道适配（`chat(system, user) -> str`） | 仅 HTTP / anthropic SDK |
| `fire_sight/services/report_service.py` | 事件统计摘要、Prompt 组装、Markdown 归档读写 | `EventStore`、`StatsService` |
| `fire_sight/api/report_jobs.py` | 后台任务生命周期（起/查/历史） | 上面两个 |
| `fire_sight/api/routers/reports.py` | REST 契约 | `report_jobs` |
| `apps/web/src/views/ReportView.vue` | 时间段选择、生成、渲染、下载、历史 | `api/client.ts` |

## 4. LLM 通道

### 配置（环境变量，全部可选）

| 变量 | 默认 | 说明 |
|---|---|---|
| `FIRESIGHT_LLM_PROVIDER` | `deepseek` | `deepseek` / `siliconflow` / `claude` / `custom` |
| `FIRESIGHT_LLM_API_KEY` | 无 | 也可分别用 `DEEPSEEK_API_KEY` / `SILICONFLOW_API_KEY` / `ANTHROPIC_API_KEY` |
| `FIRESIGHT_LLM_BASE_URL` | 按 provider 预设 | DeepSeek `https://api.deepseek.com`；SiliconFlow `https://api.siliconflow.cn/v1` |
| `FIRESIGHT_LLM_MODEL` | 按 provider 预设 | DeepSeek `deepseek-chat`；SiliconFlow `Qwen/Qwen3-32B`；Claude `claude-opus-5-5` |

### 适配器

统一接口：

```python
class LlmClient(Protocol):
    def chat(self, system: str, user: str, *, max_tokens: int = 4000) -> str: ...
```

- **`OpenAICompatChat`**：覆盖 `deepseek` / `siliconflow` / `custom`。`POST {base_url}/chat/completions`，
  JSON 体 `{model, messages:[{role:system},{role:user}], max_tokens}`，取 `choices[0].message.content`。
- **`AnthropicChat`**：Claude 通道，走官方 `anthropic` SDK（`claude-opus-5-5`），
  adaptive thinking 开启、流式取最终文本（长输出避免 HTTP 超时）。不走 OpenAI 兼容层。

`resolve_client()` 按配置返回适配器；**缺 key 时抛 `LlmNotConfigured`**，
路由转成可读的「未配置模型通道」空态，而不是 500。

### 错误语义

| 情况 | 对外表现 |
|---|---|
| 未配置 key | **POST /api/reports 直接 400**，附配置说明——不让一个注定失败的任务白跑；任务内再遇到同样问题仍记 `failed` |
| 超时 / 网络 | 任务 `failed`，原因含「网络或超时」+ 原始信息 |
| 限流 429 | 任务 `failed`，原因含「被限流，请稍后重试」 |
| 其他 4xx/5xx | 任务 `failed`，原因透传状态码与消息 |

## 5. 数据聚合

`ReportService.aggregate(start, end) -> dict`，只喂统计摘要而非原始事件：

```json
{
  "range": {"start": "2026-09-01", "end": "2026-09-30"},
  "total": 80,
  "by_class": {"fire": 78, "smoke": 50},
  "by_hour": [0, ...24 个整数...],
  "by_location": [{"location": "东门", "count": 22}, ...],
  "false_positives": 4,
  "false_positive_rate": 0.05,
  "avg_conf": 0.81,
  "mom_pct": 12.5,
  "samples": [{"label": "fire", "conf": 0.93, "location": "仓库", "message": "..."}]
}
```

- `by_location` 取前 5；`samples` 每类取置信度最高的 3 条。
- 区间内**没有事件**时 `aggregate` 返回 `{"total": 0, ...}`，路由直接拒绝生成
  并说明「所选时间段没有告警记录」——不拿空数据去花模型的钱。

## 6. Prompt 契约

**system**（稳定，可缓存）：消防数据分析专家；只依据给定数据，不得编造；
数据不足的项写「数据不足」；输出 Markdown，固定五节：

1. `## 火情态势` —— 总量、类别构成、与上月对比
2. `## 时段与场所规律` —— 高发时段、高发机位
3. `## 风险点` —— 从数据能推出的隐患
4. `## 防火建议` —— 按场所分条，可执行
5. `## 处置优先级` —— 高/中/低，各附一句理由

**user**（每次变化）：时间范围 + 统计 JSON +「据此撰写报告」。

## 7. 任务与 REST 契约

### 任务状态机

`queued → running → done | failed`（无 `stopping`：生成是短任务，取消不作本期需求）

### 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/reports` | `{start, end}` → 201 `{id, status}`；区间无事件 → 400；未配置模型通道 → 400 |
| GET | `/api/reports/jobs/{id}` | `{id, status, stage, error, report_id}` |
| GET | `/api/reports` | 历史归档列表：`[{id, title, start, end, created_at, model}]` |
| GET | `/api/reports/{id}` | `{meta, markdown}` 全文；下载走 `?download=1`（`text/markdown` 附件） |

`stage` 是离散阶段标签而非百分比——生成进度本来就没有可信的百分比，
假进度条只会误导。取值：`聚合数据` / `模型生成中` / `归档中`，终态为空。

### 归档

`data/reports/<YYYYMMDD-HHMMSS>-<id>.md` + 同名 `.json`
（时间段、模型、统计快照、生成时间、用时秒数）。

报告 `title` 由后端生成：「火情分析与防火建议 · <start> ~ <end>」。

## 8. 前端

新路由 `/reports`（顶栏加「分析报告」入口），`ReportView.vue`：

- 时间段选择（默认本月）→「生成报告」按钮（生成中禁用并显示当前阶段）
- Markdown 渲染用 `marked`（前端渲染，下载仍是 `.md`；参考设计同样使用 marked）
- 「下载 .md」按钮；下方历史列表，点开回看
- 空态两种：未配置模型通道（附配置说明）、所选区间无事件
- 错误显示后端中文原因（沿用 `fs-note-error`）

## 9. 测试

**pytest**（`tests/test_26_report.py`）：
- 聚合：空区间、有数据、`by_location` 截断、`samples` 每类 3 条
- Prompt：固定五节出现在 system，user 含统计 JSON 与时间范围
- 任务：生命周期、无事件拒绝、LLM 失败转 failed、归档文件存在且 JSON 与 MD 同名
- 适配器：OpenAI 兼容（mock HTTP）与 Claude（mock SDK 客户端）各一条；缺 key 抛 `LlmNotConfigured`

**e2e**（`apps/web/e2e/report.spec.ts`）：
- 报告页结构（时间选择、生成按钮、历史区）
- 空态文案可见（未配置或无事件二者其一）
- 生成流程冒烟：**只在 `FIRESIGHT_LLM_API_KEY` 存在时跑**（`test.skip` 否则跳过），
  真调一次模型并断言五节标题出现——这是真实花销，默认跑在 CI 里不合适；
  无 key 时改为断言失败原因可读

## 10. 实现顺序

1. `llm.py` 适配器 + 配置解析（TDD，mock HTTP/SDK）
2. `report_service.py` 聚合 + Prompt + 归档
3. `report_jobs.py` + `routers/reports.py`
4. `ReportView.vue` + 路由 + 客户端方法
5. e2e 与文档（`docs/api/REST-API.md` 补接口条目）
