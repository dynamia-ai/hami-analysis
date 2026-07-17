# Project-HAMi 周活动证据采集与周报 Skill

本项目供 Dynamia 内部研发团队使用，由两个相互独立的部分组成：

- `hami-github-activity`：只读 GitHub Activity Collector CLI；
- `weekly-hami-org-highlights`：基于采集结果生成内部研发周报的 LLM Skill。

CLI 从 `Project-HAMi` organization 采集最近有活动的 Issue 和 Pull Request，并把正文、评论、review、状态与数据限制写入一个结构稳定的 Markdown evidence 文件。CLI 不判断优先级、不调用 LLM，也不修改 GitHub。Skill 只读取 evidence 文件，负责识别工程风险、归类相关事项并提出研发投入建议。

## 职责边界

```text
GitHub REST API
       │
       ▼
hami-github-activity
       │
       ▼
一个 Markdown evidence 文件
       │
       ▼
LLM Agent + weekly-hami-org-highlights
       │
       ▼
Weekly HAMi Org Highlights
```

CLI 固定使用 GitHub REST API，不依赖本地 `gh`。固定 transport 可以减少部署环境差异，保留明确的分页、rate limit、重试与部分失败语义，也避免把认证或输出结构交给外部命令版本决定。

CLI 固定采集：

- Issue 和 PR 正文；
- Issue comments；
- PR conversation comments；
- PR reviews；
- PR review comments；
- Issue 和 PR 当前基础状态；
- PR 当前 `merged`、`draft`、`mergeable` 和变更规模字段。

第一版不采集 timeline events、check runs、workflow runs、commits、文件 diff、全量仓库历史和 reaction 明细。

## 环境要求

- Python 3.14；
- [uv](https://docs.astral.sh/uv/)；
- 可读取目标 organization 的 GitHub Token。

项目通过 `.python-version` 固定 Python 3.14，并通过 `pyproject.toml` 的 `requires-python = ">=3.14,<3.15"` 阻止其他 Python 版本误用。

## 安装

同步锁定依赖和开发工具：

```bash
uv sync --locked
```

确认 CLI 可运行：

```bash
uv run hami-github-activity --help
uv run python -m hami_github_activity --help
```

## GitHub Token

CLI 从配置指定的环境变量读取 Token，示例配置使用 `GITHUB_TOKEN`：

```bash
export GITHUB_TOKEN=github_pat_xxx
```

只分析公开仓库时，Token 不需要写权限。使用 fine-grained personal access token 时，应只授予目标 organization/repository 的读取权限，至少允许读取 metadata、Issues 和 Pull requests；如果组织或仓库为私有，还需要对应私有仓库访问权限。组织的 SSO 策略可能要求额外授权。

不要把 Token 写入 YAML、命令行参数、evidence 文件或版本控制。

## 配置

复制示例配置：

```bash
cp config.example.yaml config.yaml
```

```yaml
github:
  org: Project-HAMi
  token_env: GITHUB_TOKEN

scan:
  # 最近 7 个自然日，包括运行当天。
  days: 7
  # 固定使用 UTC+8，不接受其他时区。
  timezone: Asia/Shanghai

output:
  file: ./output/github-activity-{org}-{start_date}-{end_date}.md
```

第一版配置只接受以下字段：

- `github.org`；
- `github.token_env`；
- `scan.days`；
- `scan.timezone`；
- `output.file`。

未知字段会导致配置校验失败。相对输出路径以配置文件所在目录为基准。输出文件名支持 `{org}`、`{start_date}` 和 `{end_date}` 三个占位符。

检查配置不会访问 GitHub，也不要求 Token：

```bash
uv run hami-github-activity validate-config --config ./config.yaml
```

## 采集

按配置的默认周期采集：

```bash
uv run hami-github-activity collect --config ./config.yaml
```

默认使用 8 个并发 worker，并把整个进程的 GitHub 请求启动速率限制为每秒 10 次。可在命令行调整，worker 范围为 1–16，请求速率范围为每秒 1–15 次：

```bash
uv run hami-github-activity collect \
  --config ./config.yaml \
  --workers 8 \
  --requests-per-second 10
```

请求速率限制由所有 worker 共享。提高并发主要用于覆盖网络等待时间，不会绕过 GitHub API 的主速率限制或次级速率限制。

模块入口等价：

```bash
uv run python -m hami_github_activity collect --config ./config.yaml
```

临时覆盖日期：

```bash
uv run hami-github-activity collect \
  --config ./config.yaml \
  --start-date 2026-07-10 \
  --end-date 2026-07-16
```

只展示执行计划，不读取 Token、不访问 GitHub、不写 evidence 文件：

```bash
uv run hami-github-activity collect --config ./config.yaml --dry-run
```

成功后，标准输出包含 organization、实际扫描起止时间、时区、Issue 数量、PR 数量、API 请求失败数量、evidence 路径和最后观测到的 GitHub API rate limit 剩余量。

## 时间范围

默认 `days: 7` 表示「包含运行当天在内的最近 7 个自然日」。例如程序在 `2026-07-16 14:30:00 Asia/Shanghai` 运行，周期为：

```text
2026-07-10 00:00:00 Asia/Shanghai
至
2026-07-16 14:30:00 Asia/Shanghai
```

同时指定 `--start-date 2026-07-10 --end-date 2026-07-16` 时，周期覆盖两个日期之间的完整自然日，结束时间为 `2026-07-16 23:59:59.999999`。

所有采集周期固定使用 `Asia/Shanghai`，即 UTC+8。GitHub Search Issues 只用于查找候选项，查询直接使用 UTC+8 的本地起止日期，不在前后扩展日期。API 时间戳会按 UTC+8 周期边界换算为精确 UTC 时间，再检查创建、关闭、合并、评论和 review。

为减少请求量和历史分页：

- Search 返回完整字段时，Issue 不再重复请求详情端点；
- Search 返回的 `updated_at` 早于精确周期起点时，直接排除候选项；
- GitHub 明确报告评论数量为 0 时，不请求对应评论端点；
- Issue comments、PR conversation comments 和 PR review comments 使用周期起点对应的 `since` 参数；
- PR reviews 端点不支持 `since`，仍按 API 分页读取。

Search 的 `updated_at` 只用于发现候选项，不作为最终收录理由。最终收录至少需要一个可以验证的周期内事件：创建、关闭、合并、comment、review 或 review comment。只有 `updated_at` 落入周期、但没有已采集事件可以解释的事项会被排除；这包括删除分支等未采集的元数据变化。Collection Summary 会记录这类排除项的数量。

如果活动端点请求失败，且事项没有其他可以验证的周期事件，该事项也会被排除。此类排除单独计数，因为失败端点可能隐藏了真实活动；受影响事项及请求 URL 会保留在 `Collection Warnings` 中。

## Evidence 文件

每次执行只生成一个主要 Markdown 文件，不附带 JSON 或 metadata 文件。固定章节顺序为：

```text
YAML front matter
Document Map
Collection Summary
Issues Index
Pull Requests Index
Issue Evidence
Pull Request Evidence
Collection Warnings
Data Limitations
```

front matter 包含 schema 版本、组织、生成时间、时区、本地和 UTC 周期、事项数量与 warning 数量。索引只记录事实，不判断重要性。

每个事项使用确定性的开始和结束标记：

```markdown
<!-- ITEM_START issue Project-HAMi/HAMi#1234 -->
...
<!-- ITEM_END issue Project-HAMi/HAMi#1234 -->
```

```markdown
<!-- ITEM_START pull_request Project-HAMi/HAMi#1235 -->
...
<!-- ITEM_END pull_request Project-HAMi/HAMi#1235 -->
```

Agent 应先读取 front matter、摘要和两个索引，再通过完整 `ITEM_START` 标记定位候选事项并读取到对应 `ITEM_END`。不需要一次性加载整个文件。

为控制文件大小，Issue/PR body 最多保留 30,000 字符，单条 comment/review body 最多保留 12,000 字符。超限内容会带显式截断标记。评论类端点只请求在周期起点之后更新的记录，因此较早的上下文可能不存在；API 返回周期前活动时，最多展示最近 3 条人类活动。周期内活动全部保留。bot 内容不会删除，而是标记并放在较低显著度位置。

## 安装或加载 Skill

Skill 位于：

```text
skills/weekly-hami-org-highlights/
```

可将该目录复制到 Codex skill 目录：

```bash
cp -R skills/weekly-hami-org-highlights "${CODEX_HOME:-$HOME/.codex}/skills/"
```

也可以让支持本地 Skill 的 Agent 直接加载仓库中的 `skills/weekly-hami-org-highlights/SKILL.md`。调用时提供 evidence 文件路径，例如：

```text
使用 $weekly-hami-org-highlights 分段读取
./output/github-activity-Project-HAMi-2026-07-10-2026-07-16.md，
生成 Dynamia 内部 Weekly HAMi Org Highlights。
```

Skill 明确禁止重新访问 GitHub。它会要求 Agent 先读索引、再按 ITEM 标记读取完整事项区块，检查 warning 和限制，最后生成研发投入建议。

## 每周执行

### cron

下面的示例每周一 09:00 执行。实际触发时区由运行机器的 cron 配置决定，采集周期固定按 YAML 中的 `Asia/Shanghai` 计算：

```cron
0 9 * * 1 cd /opt/project-hami-weekly-report && /usr/local/bin/uv run hami-github-activity collect --config ./config.yaml >> ./output/collector.log 2>&1
```

Token 应通过系统的 secret 管理或受限环境文件注入，不要写在 crontab 中。

### GitHub Actions

```yaml
name: Collect weekly HAMi activity

on:
  schedule:
    - cron: "0 1 * * 1" # 09:00 Asia/Shanghai
  workflow_dispatch:

jobs:
  collect:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v8
        with:
          python-version: "3.14"
      - run: uv sync --locked
      - run: uv run hami-github-activity collect --config ./config.yaml
        env:
          GITHUB_TOKEN: ${{ secrets.HAMI_ACTIVITY_GITHUB_TOKEN }}
      - uses: actions/upload-artifact@v4
        with:
          name: hami-github-activity
          path: output/*.md
```

定时触发使用 UTC。为避免 GitHub 自动提供的 token 受仓库边界限制，跨 organization 采集时应使用只读 secret。

## 错误处理

- Token 缺失或配置无效：CLI 在访问 GitHub 前退出；
- HTTP 429、HTTP 5xx 和网络错误：指数退避重试；
- 多个候选项：默认由 8 个 worker 并行采集，并对共享请求速率设置上限；
- rate limit 耗尽：记录 GitHub 返回的错误和剩余量；
- Search API 达到 1,000 条上限：在 evidence 中记录 warning；
- 单个事项详情或活动端点失败：继续采集其他事项，并把失败写入 `Collection Warnings` 和对应事项的 `Data Gaps`；
- 搜索为空：仍生成包含完整结构和数据限制的单一 evidence 文件；
- API 字段缺失：使用明确的缺失值或 `Data Gaps`，不推测内容。

## 已知限制

- 没有 timeline events，无法可靠确定 label、assignee、milestone、重新打开和 draft-to-ready 的变更时间；
- 只有 `updated_at` 命中、但没有可验证周期事件的事项不会进入 evidence，因此标签、assignee、milestone、删除分支等单独变化不会成为收录理由；
- 没有 CI/check runs，无法判断 CI 状态或完整 merge readiness；
- 没有 commits，无法判断周期内是否新增 commit；
- 没有文件 diff，无法分析具体改动内容；
- Search Issues 单个查询最多暴露 1,000 条结果；第一版只报告该限制，不做日期分片；
- 评论类端点使用 `since` 减少历史分页，因此不能保证包含周期前的完整评论上下文；
- `mergeable` 是 GitHub 当前返回的可空快照，不等价于「可合并」结论；
- 部分请求失败时 evidence 可能不完整，Agent 必须结合 warning 降低结论置信度。
- 活动端点失败且没有其他可验证周期事件时，事项会被排除；Collection Summary 和 `Collection Warnings` 会分别记录数量与失败详情。

## 测试

测试全部使用本地 fake 或 `httpx.MockTransport`，不访问真实 GitHub API：

```bash
uv run pytest
```

测试覆盖 UTC+8 时间范围、显式日期、并发采集、请求范围参数、分页、重试、Issue/PR 分类、评论/review 周期判断、bot 和 maintainer 标记、部分失败、空结果、截断、front matter、索引、ITEM 标记、固定章节顺序、warning、数据限制和 CLI dry-run。
