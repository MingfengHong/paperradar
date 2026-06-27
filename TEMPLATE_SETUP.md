# PaperRadar Template Setup

这份指南面向点击 **Use this template** 创建自己 PaperRadar 仓库的用户。目标是不用改代码，只改配置和 GitHub Secrets，就能让系统每天自动筛选并推送论文。

## 1. 创建自己的仓库

1. 打开 PaperRadar 仓库页面。
2. 点击右上角 **Use this template**。
3. 选择 **Create a new repository**。
4. 仓库可以设为 Private。Private 仓库也可以运行 GitHub Actions；Pages 是否可用取决于你的 GitHub 账户/组织设置。

不要直接把密钥写进 `config/*.yaml`。推送 webhook、SMTP 密码、LLM Key、Zotero Key 都放在 GitHub Secrets。

## 2. 配置研究主题

编辑 `config/topics.yaml`：

```yaml
topics:
  - id: default
    name: "AI and Organization Research"
    research_question: "How does artificial intelligence reshape organizations and management?"
    keywords:
      - artificial intelligence
      - organization
      - management
      - digital transformation
    exclude_keywords: []
    venues: []
    reading_goal: "Find papers worth reading for management research."
    status: active
```

`keywords` 是最重要的召回和预筛信号。建议写 3-8 个稳定关键词，不要把整段研究计划塞进去。

## 3. 配置订阅

编辑 `config/subscriptions.yaml`。新用户建议先启用一个常规论文精选订阅：

```yaml
subscriptions:
  - id: daily-paper-digest
    topic_id: default
    type: paper
    enabled: true
    report_modules:
      - paper_digest
      - periodic_review
    schedule: manual
    max_papers: 8
    min_score: 0.55
    channels:
      - email
    source:
      query: ""
```

`source.query` 可以留空。配置 LLM 后，PaperRadar 会根据主题自动生成检索式；没有 LLM 时会用研究问题和关键词检索。

如果要跟踪 arXiv 新文，增加或启用：

```yaml
  - id: arxiv-daily
    topic_id: default
    type: arxiv
    enabled: true
    report_modules:
      - fresh_updates
    schedule: manual
    max_papers: 8
    min_score: 0.55
    channels:
      - email
    source:
      categories:
        - cs.AI
        - cs.CL
      query: ""
      mode: daily_window
```

## 4. 配置推送渠道

编辑 `config/notifications.yaml`，只把要用的渠道设为 `enabled: true`。密钥仍然放 Secrets。

邮件示例：

```yaml
channels:
  email:
    enabled: true
    from: ""
    password: ""
    to: ""
    smtp_server: ""
    smtp_port: 587
```

飞书示例：

```yaml
channels:
  feishu:
    enabled: true
    webhook_url: ""
```

## 5. 添加 GitHub Secrets

进入你的仓库：

```text
Settings -> Secrets and variables -> Actions -> Secrets -> New repository secret
```

邮件至少需要：

| Secret | 说明 |
| --- | --- |
| `EMAIL_FROM` | 发信邮箱或发信地址。 |
| `EMAIL_PASSWORD` | SMTP 授权码或 SMTP 密码。 |
| `EMAIL_TO` | 收件邮箱，多个邮箱用英文逗号分隔。 |
| `EMAIL_SMTP_SERVER` | SMTP 服务器。阿里云邮件推送一般为 `smtpdm.aliyun.com`。 |
| `EMAIL_SMTP_PORT` | SMTP 端口。阿里云 SSL 常用 `465`。 |

飞书需要：

| Secret | 说明 |
| --- | --- |
| `FEISHU_WEBHOOK_URL` | 飞书自定义机器人 webhook。多个 webhook 可用英文分号分隔。 |

LLM 推荐配置：

| Secret | 说明 |
| --- | --- |
| `LLM_API_KEY` | OpenAI-compatible API Key。 |
| `LLM_BASE_URL` | API Base URL，例如 `https://api.openai.com/v1`。 |
| `LLM_MODEL` | 模型名。 |

数据源和文献库：

| Secret | 说明 |
| --- | --- |
| `OPENALEX_API_KEY` | OpenAlex API Key。 |
| `EMBEDDING_API_KEY` | 可选，用于 LLM 前候选预筛。 |
| `EMBEDDING_BASE_URL` | 可选，embedding API Base URL。 |
| `EMBEDDING_MODEL` | 可选，embedding 模型名。 |
| `ZOTERO_USER_ID` | 可选，Zotero 用户 ID。 |
| `ZOTERO_GROUP_ID` | 可选，Zotero Group ID。 |
| `ZOTERO_API_KEY` | 可选，Zotero API Key。 |

## 6. 可选：开启 GitHub Pages 报告

进入：

```text
Settings -> Pages
```

把 `Build and deployment -> Source` 设为 `GitHub Actions`。

然后进入：

```text
Settings -> Secrets and variables -> Actions -> Variables
```

添加：

| Variable | 值 |
| --- | --- |
| `PAPERRADAR_DEPLOY_PAGES` | `true` |
| `PAPERRADAR_PUBLIC_BASE_URL` | `https://<GitHub用户名>.github.io/<仓库名>` |

如果不配置 Pages，报告仍会作为每次 Action 的 artifact 上传。

Pages 部署成功后，会同时提供静态配置生成器：

```text
https://<GitHub用户名>.github.io/<仓库名>/configurator.html
```

这个页面不连接后端，不会保存密钥。它用于可视化生成 `config/*.yaml` 和 Secrets 清单；复制生成结果到仓库后提交即可。需要直接写入配置文件、测试推送渠道或手动运行订阅时，在本地运行 `paperradar web`。

## 7. 第一次运行

进入：

```text
Actions -> PaperRadar -> Run workflow
```

第一次建议：

| 输入 | 值 |
| --- | --- |
| `no_push` | `true` |
| `deploy_pages` | `false` 或 `true` |
| `subscription` | 留空，或填 `daily-paper-digest` |

确认 Action 成功并且 artifact 中的报告内容正常后，再运行一次：

| 输入 | 值 |
| --- | --- |
| `no_push` | `false` |
| `deploy_pages` | 按需 |
| `subscription` | 留空，或填单个订阅 ID |

## 8. 修改每日推送时间

编辑 `.github/workflows/paperradar.yml`：

```yaml
schedule:
  - cron: "0 23 * * *"
```

GitHub cron 使用 UTC。`0 23 * * *` 对应北京时间每天 07:00。

## 9. 本地或服务器配置

本地第一次配置可以运行：

```bash
python -m pip install -e .
paperradar setup
paperradar doctor
paperradar run --no-push
```

Linux 服务器可以用 Docker：

```bash
docker compose -f docker/docker-compose.yml up --build -d
```

也可以用 systemd/cron：

```cron
0 7 * * * cd /opt/paperradar && /opt/paperradar/.venv/bin/paperradar run
```
