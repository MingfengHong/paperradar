# PaperRadar

[![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-AGPL--3.0--only-blue.svg)](LICENSE)
[![Status](https://img.shields.io/badge/status-prototype-orange)](#项目状态)

**研究主题驱动的每日论文雷达。** PaperRadar 面向需要长期跟踪文献的研究者：你先配置研究主题、订阅类型、报告模块、推送渠道和运行时间，系统定期收集候选论文，再结合 Zotero/本地文献库、候选预筛和可选 LLM 分析，筛出更值得读的论文并自动生成推送报告。

## 概览

PaperRadar 把论文推送组织成一条可配置的工作流：

- 创建研究主题：研究问题、关键词、排除词、关注期刊/会议、阅读目标。
- 创建订阅：常规论文精选、arXiv 新文追踪、期刊 RSS 新文追踪。
- 收集候选论文：有 LLM 时先生成检索式；无 LLM 时使用订阅补充检索式或主题关键词；OpenAlex、Crossref、arXiv、RSS feed 拉取候选。
- 结合用户文献库：Zotero、本地 CSV/BibTeX/RIS 和手写 YAML 文献。
- 候选预筛：用主题相关性、文献库相似度、时间新鲜度和元数据完整度压缩候选池。
- LLM 分析：只把预筛后的候选交给 LLM 做 worth-read 判断和报告分析。
- 生成报告：论文精选、新文追踪、周期综述三个模块由用户勾选。
- 推送通知：飞书、邮件、钉钉、企业微信、Telegram、ntfy、Bark、Slack、通用 webhook。
- 自动运行：CLI、本地 Web UI、GitHub Actions、Docker、Linux systemd。

## 功能概览

| 功能 | 说明 |
| --- | --- |
| 研究主题入口 | 用户显式配置主题、关键词、排除词和阅读目标，不从一整段自然语言里猜推送频率。 |
| 参数化订阅 | 每个订阅独立设置类型、数据源、报告模块、最大论文数、最低推荐分和推送渠道。 |
| 常规论文精选 | 有 LLM 时每次运行先生成检索式；无 LLM 时优先使用补充检索式，留空则按研究主题从 OpenAlex/Crossref 搜索候选论文。 |
| arXiv 新文追踪 | 默认使用 `daily_window`，先抓 arXiv 公告窗口候选，再由 PaperRadar 筛选值得读的论文。 |
| 期刊 RSS 新文追踪 | 支持用户自填 RSS，也提供 `journal-rss-discover` 帮助从期刊主页发现 feed。 |
| Zotero 相关性 | 可接入 Zotero 文献库，用最近加入和指定 collection/tag 的文献帮助判断新论文相关性。 |
| 本地文献库 | 支持 YAML、CSV、BibTeX、RIS，适合没有 Zotero 或需要服务器部署的场景。 |
| 候选池预筛 | 在提交给 LLM 前先粗排候选，避免大量无关论文进入 LLM 分析。 |
| 时间衰减 rerank | 借鉴 `zotero-arxiv-daily` 的思路，最近关注的文献在候选预筛阶段权重更高。 |
| 可选 embedding 召回排序 | 配置 embedding API 后，在 LLM 前用向量相似度压缩候选池。 |
| LLM 分析 | 可选生成 TL;DR、关键词、分类、主要贡献、阅读前注意，并给出推荐理由。 |
| 三类报告模块 | `paper_digest` 论文精选、`fresh_updates` 新文追踪、`periodic_review` 周期综述，可复选。 |
| 多渠道推送 | 支持飞书、邮件、钉钉、企业微信等中国大陆常用通知渠道。 |
| CLI 与 Web UI | 可命令行部署到 Linux 服务器，也可打开本地浏览器配置和手动运行。 |
| GitHub Actions 部署 | Fork 后配置 Secrets 即可按 cron 自动运行，并上传报告 artifact。 |
| Docker/systemd 部署 | 适合 VPS、实验室服务器和 NAS 长期运行。 |

## 快速开始

进入项目目录：

```bash
cd paperradar
python -m pip install -e .
paperradar init
paperradar doctor
paperradar run --no-push
paperradar web
```

Windows PowerShell：

```powershell
cd paperradar
python -m pip install -e .
paperradar init
paperradar doctor
paperradar run --no-push
paperradar web
```

打开本地 Web UI：

```text
http://127.0.0.1:8766/
```

`paperradar run --no-push` 会生成报告但不发送通知，适合第一次测试。默认输出位置：

```text
output/reports/
output/static/index.html
```

## Web UI

Web UI 是轻量管理界面，适合本地配置和手动触发：

| 区域 | 用途 |
| --- | --- |
| 主题 | 左侧新增研究主题，右侧卡片编辑、暂停/恢复和删除；编辑在弹窗中完成。 |
| 订阅 | 左侧新增常规论文、arXiv、期刊 RSS 订阅，右侧卡片编辑、启停、运行和删除；报告模块、arXiv 分类和推送渠道使用复选框。 |
| 推送 | 纵向配置飞书、邮件、钉钉、企业微信、Telegram、ntfy、Bark、Slack、通用 webhook；支持保存、测试和重置单个渠道。 |
| 设置 | 配置 LLM、向量相似度预筛、Zotero 连接和数据源开关。 |
| 报告 | 手动运行订阅并查看最近报告 Markdown。 |

表单按“必填、选填、高级设置”分层；凡是能用下拉框或复选框表达的参数，不要求用户手写内部配置值。Web UI 会写入 `config/*.yaml`。本地部署时可以直接在界面保存 webhook/SMTP 等配置；多人共享服务器或 GitHub Actions 部署时，建议把真实密钥放在 `.env`、系统环境变量或 GitHub Secrets 中。

## CLI 用法

初始化配置：

```bash
paperradar init
```

诊断配置：

```bash
paperradar doctor
```

创建研究主题：

```bash
paperradar topic create \
  --id llm-literature \
  --name "LLM Literature Recommendation" \
  --question "How can LLMs help researchers avoid literature overload?" \
  --keywords "large language model,literature recommendation,scientific discovery"
```

查看主题：

```bash
paperradar topic list
```

创建常规论文精选订阅：

```bash
paperradar paper-subscription create \
  --id llm-digest \
  --topic-id llm-literature \
  --query "large language model scientific literature recommendation" \
  --modules paper_digest,periodic_review \
  --max-papers 8 \
  --min-score 0.55 \
  --channels feishu,email
```

创建 arXiv 新文追踪订阅：

```bash
paperradar arxiv-subscription create \
  --id llm-arxiv \
  --topic-id llm-literature \
  --categories cs.AI,cs.CL,cs.LG \
  --mode daily_window \
  --modules fresh_updates \
  --max-papers 10
```

创建期刊 RSS 订阅：

```bash
paperradar journal-subscription create \
  --id nature-rss \
  --topic-id llm-literature \
  --journal "Nature" \
  --feed-url https://www.nature.com/nature.rss \
  --modules fresh_updates
```

发现期刊 RSS：

```bash
paperradar journal-rss-discover \
  --journal "Nature" \
  --homepage-url "https://www.nature.com/nature/"
```

运行全部启用订阅：

```bash
paperradar run --no-push
paperradar run
```

运行单个订阅：

```bash
paperradar run --subscription llm-arxiv --no-push
```

查看报告历史：

```bash
paperradar report list
```

测试单个通知渠道：

```bash
paperradar test-notification feishu
paperradar test-notification email
```

## 配置文件

`paperradar init` 会生成：

```text
config/settings.yaml
config/topics.yaml
config/subscriptions.yaml
config/notifications.yaml
config/library.yaml
config/zotero.yaml
.env.example
```

| 文件 | 说明 |
| --- | --- |
| `settings.yaml` | 应用路径、LLM、数据源、ranking、embedding、分类器配置。 |
| `topics.yaml` | 研究主题。 |
| `subscriptions.yaml` | 常规论文、arXiv、期刊 RSS 订阅。 |
| `notifications.yaml` | 推送渠道配置。 |
| `library.yaml` | 本地文献库和导入路径。 |
| `zotero.yaml` | Zotero API、collection、tag、include/ignore path。 |
| `.env.example` | 本地密钥模板。 |

系统环境变量优先级高于 YAML。推荐把真实密钥放在 `.env`、系统环境变量或 GitHub Secrets，不要提交到仓库。

## LLM 配置

PaperRadar 可以无 LLM 运行。未配置 LLM 时，常规论文订阅使用配置里的补充检索式 `source.query`；如果留空，则使用研究问题和主题关键词召回候选，并用 lexical/embedding/Zotero 预筛和启发式规则生成报告。配置 LLM 后，系统会在每次运行时结合研究主题和补充检索式生成更合适的检索式，并对预筛后的候选论文做更细的 worth-read 分析。

OpenAI-compatible API：

```bash
export LLM_API_KEY=your-api-key
export LLM_BASE_URL=https://api.openai.com/v1
export LLM_MODEL=gpt-4o-mini
```

Windows PowerShell：

```powershell
$env:LLM_API_KEY = "your-api-key"
$env:LLM_BASE_URL = "https://api.openai.com/v1"
$env:LLM_MODEL = "gpt-4o-mini"
```

国内 OpenAI-compatible 服务也可以通过相同变量配置，例如 DeepSeek、ModelScope、硅基流动、中国科技云等，只要接口兼容 `/chat/completions`。

## 数据源配置

OpenAlex 需要 API Key。可以在 Web UI 的“设置 -> 数据源”里填写，或通过环境变量配置：

```bash
export OPENALEX_API_KEY=your-openalex-api-key
```

Crossref 仍可选填邮箱，用于 User-Agent 联系信息。

## Embedding 候选预筛

PaperRadar 会先用检索式和订阅源召回较大的候选池，再在 LLM 前做候选预筛。默认预筛使用 lexical token overlap；配置 embedding API 后，会改用向量相似度评估候选论文与用户文献库、研究主题之间的关系。

```bash
export EMBEDDING_API_KEY=your-api-key
export EMBEDDING_BASE_URL=https://api.openai.com/v1
export EMBEDDING_MODEL=text-embedding-3-small
```

启用后，PaperRadar 会把候选论文和用户文献库转成向量，计算余弦相似度，并结合时间衰减权重压缩进入 LLM 的候选池。embedding 不直接决定最终推荐，只用于减少 LLM 需要分析的论文数量和提高候选质量。

## Zotero 与本地文献库

Zotero 配置示例：

```yaml
zotero:
  enabled: true
  user_id: "123456"
  group_id: ""
  api_key: "your-zotero-api-key"
  collections: []
  tags:
    - reading-list
  include_path:
    - "2026/llm/**"
  ignore_path:
    - "archive/**"
```

`include_path` 和 `ignore_path` 用于控制哪些 collection 参与推荐。PaperRadar 会读取题名、摘要、标签、collection path 和 dateAdded。最近加入的文献权重更高。

本地文献库示例：

```yaml
items:
  - id: seed-1
    title: "A survey of literature recommendation"
    authors: ["Example Author"]
    year: 2025
    tags: ["literature recommendation"]
    note: "User considers this central to current work."
imports:
  - path: data/my-library.csv
    type: csv
  - path: data/my-library.bib
    type: bibtex
  - path: data/my-library.ris
    type: ris
```

CSV 推荐字段：

```text
title,authors,year,doi,arxiv_id,venue,tags,collection,collection_paths,abstract,added_at
```

## 订阅类型

### 常规论文精选

适合稳定研究主题，每天或每周从 OpenAlex/Crossref 找候选论文：

```yaml
type: paper
source:
  query: large language model scientific literature recommendation
report_modules:
  - paper_digest
  - periodic_review
```

### arXiv 新文追踪

适合跟踪每日新增预印本。`daily_window` 模式先抓 arXiv 官方 RSS 公告候选，再筛选推荐：

```yaml
type: arxiv
source:
  categories:
    - cs.AI
    - cs.CL
  query: ""
  mode: daily_window
report_modules:
  - fresh_updates
```

如果只想按查询取最近 arXiv 结果，可以使用：

```yaml
mode: latest
query: retrieval augmented generation
```

### 期刊 RSS 新文追踪

适合跟踪 Nature、Science、Cell、PNAS、领域顶刊或中文期刊 RSS：

```yaml
type: journal_rss
source:
  feeds:
    - name: Nature
      url: https://www.nature.com/nature.rss
      journal: Nature
report_modules:
  - fresh_updates
```

用户可以直接填写 RSS，也可以用 `journal-rss-discover` 让系统从期刊主页尝试发现 feed。实际部署时，推荐把发现结果人工确认后写入配置。

## 报告模块

用户只需要选择少数几类常用报告：

| 模块 | 说明 |
| --- | --- |
| `paper_digest` | 核心精选，按精读、略读、收藏、观察组织论文。 |
| `fresh_updates` | 只展示 arXiv 和期刊 RSS 的最新高相关新增。 |
| `periodic_review` | 对一个周期内推荐结果做主题分布、关键词和下一阶段阅读建议。 |

每篇推荐论文包含：

- 题名、作者、年份、期刊/来源、DOI/arXiv/链接。
- 推荐动作：精读、略读、收藏、观察、过滤。
- 综合分、置信度、文献库相关性。
- TL;DR、分类、关键词。
- 主要贡献和阅读前注意。
- 与 Zotero/本地文献库中哪些文献接近。

## 推送渠道

`config/notifications.yaml` 支持：

| 渠道 | 环境变量 |
| --- | --- |
| 飞书/Lark | `FEISHU_WEBHOOK_URL` |
| 邮件 SMTP | `EMAIL_FROM`, `EMAIL_PASSWORD`, `EMAIL_TO`, `EMAIL_SMTP_SERVER`, `EMAIL_SMTP_PORT` |
| 钉钉 | `DINGTALK_WEBHOOK_URL` |
| 企业微信 | `WEWORK_WEBHOOK_URL`, `WEWORK_MSG_TYPE` |
| 通用 webhook | `GENERIC_WEBHOOK_URL` |
| Telegram | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` |
| ntfy | `NTFY_SERVER_URL`, `NTFY_TOPIC`, `NTFY_TOKEN` |
| Bark | `BARK_URL` |
| Slack | `SLACK_WEBHOOK_URL` |

订阅中通过 `channels` 指定推送渠道：

```yaml
channels:
  - feishu
  - email
```

如果订阅未指定 channels，会使用 `notifications.yaml` 中所有 enabled 的渠道。

## 部署

### GitHub Actions

项目包含 `.github/workflows/paperradar.yml`。Fork 后在 GitHub Secrets 中配置需要的密钥：

```text
LLM_API_KEY
LLM_BASE_URL
LLM_MODEL
OPENALEX_API_KEY
EMBEDDING_API_KEY
EMBEDDING_BASE_URL
EMBEDDING_MODEL
FEISHU_WEBHOOK_URL
DINGTALK_WEBHOOK_URL
WEWORK_WEBHOOK_URL
EMAIL_FROM
EMAIL_PASSWORD
EMAIL_TO
ZOTERO_USER_ID
ZOTERO_GROUP_ID
ZOTERO_API_KEY
```

默认 cron：

```yaml
- cron: "0 23 * * *"
```

这相当于 UTC 23:00，每天北京时间 07:00 左右运行。可以在 workflow 里按自己的推送时间修改。

也可以打印模板：

```bash
paperradar deploy github-actions-template
```

### Docker

```bash
docker compose -f docker/docker-compose.yml up --build
```

或查看模板：

```bash
paperradar deploy docker-compose-template
```

### Linux systemd

```bash
paperradar deploy systemd-template
```

示例服务会执行：

```bash
paperradar schedule daemon --interval 1800
```

更简单的服务器部署也可以使用 cron：

```cron
0 7 * * * cd /opt/paperradar && /opt/paperradar/.venv/bin/paperradar run
```

## 推荐机制

PaperRadar 当前流程分为“召回、预筛、LLM 分析、报告推送”：

1. 检索规划：有 LLM 时根据主题、关键词、排除词和订阅目标生成 3-5 个检索式；无 LLM 时使用补充检索式，留空则回退到研究主题。
2. 数据源召回：OpenAlex/Crossref、arXiv 或期刊 RSS 返回较大的候选池。
3. LLM 前预筛：主题匹配、文献库相似度、时间衰减、embedding/lexical 和新鲜度共同压缩候选池。
4. 论文分析：有 LLM 时分析预筛后的候选并给出 worth-read 判断、贡献、局限、分类、关键词和推荐理由；无 LLM 时使用启发式摘要和理由。
5. 报告阈值：根据用户设置的 `min_score` 和 LLM 动作生成最终推送内容。

## 数据源

| 数据源 | 用途 |
| --- | --- |
| OpenAlex | 常规论文候选和开放元数据。 |
| Crossref | DOI/期刊论文元数据补充。 |
| arXiv Atom API/RSS | 预印本新文追踪。 |
| 期刊 RSS | 指定期刊最新一期/最新文章追踪。 |
| Zotero API | 用户已有文献库相关性判断。 |
| 本地 CSV/BibTeX/RIS | 无 Zotero 或服务器部署时的文献库输入。 |

## 目录结构

```text
paperradar/
  paperradar/
    cli.py
    config.py
    library.py
    llm.py
    ranking.py
    recommender.py
    reports.py
    runner.py
    sources/
    notifications/
    web/
  config/
  tests/
  docker/
  output/
  data/
```

`data/` 和 `output/` 是运行产物，默认被 `.gitignore` 忽略。

## 开发与验证

```bash
python -m pytest -q
python -m compileall -q paperradar
paperradar doctor
paperradar run --no-push
paperradar run --subscription arxiv-ai --no-push
```

当前测试覆盖：

- 初始化配置。
- 推荐器和增强字段。
- RSS 解析。
- 报告模块。
- Web 配置 API。
- 文献库时间衰减 rerank。
- arXiv 公告窗口。

## 致谢

PaperRadar 的产品和机制参考了以下项目：

- `sansan0/TrendRadar`：自托管、GitHub Actions 定时运行和多渠道推送。
- `TideDra/zotero-arxiv-daily`：Zotero corpus rerank、最近文献时间衰减、每日 arXiv 工作流。
- `MaoSong2022/arxiv_daily`：arXiv 公告窗口、TL;DR/关键词/分类报告字段。
- `MingfengHong/paperseek`：轻量 Web/CLI 双入口思路、文献检索。
- `binary-husky/chatgpt_academic`：学术分析提示词、证据边界和 Markdown 输出风格。

## 开源协议

PaperRadar 使用 [GNU Affero General Public License v3.0](LICENSE)协议
