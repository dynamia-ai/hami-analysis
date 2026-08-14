# Project-HAMi 周活动证据采集与周报 Skill

本项目供 Dynamia 内部研发团队使用，由两个相互独立的部分组成：

- `hami-github-activity`：只读 GitHub Activity Collector CLI；
- `weekly-hami-org-highlights`：基于采集结果生成内部研发周报的 LLM Skill。

CLI 从 `Project-HAMi` organization 采集最近有活动的 Issue 和 Pull Request，并把正文、评论、review、状态与数据限制写入一个结构稳定的 Markdown evidence 文件。CLI 不判断优先级、不调用 LLM，也不修改 GitHub。Skill 只读取 evidence 文件，负责识别工程风险、归类相关事项、评估全部活跃 PR 是否存在有直接证据的 Contribution Gates 违规，并提出研发投入建议。已确认违规事项只进入独立章节，不参与其他优先级或资源建议。

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

运行配置约束：`output.file` 必须是配置文件所在目录下 `output/` 的相对路径。这样 evidence、报告和其他运行产物统一落在被 Git 忽略的目录中；`config.example.yaml` 可提交，真实 `config*.yaml` 不应提交。

第一版不采集 timeline events、check runs、workflow runs、head commits、resolved review threads、文件 diff、全量仓库历史和 reaction 明细。

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
- `github.expected_repositories`（正式采集必须完整列出预期可见的 `OWNER/REPOSITORY`，并与 Token 实际可见的 organization 仓库集合完全一致）；
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

所有采集周期固定使用 `Asia/Shanghai`，即 UTC+8。GitHub Search Issues 只用于查找候选项，查询从包含精确 `utc_start` 的 UTC 日开始（`updated:>=YYYY-MM-DD`），故意形成候选超集；之后再用精确 UTC 时间检查创建、关闭、合并、评论和 review。查询不设 `updated` 上界，避免后续更新遮蔽历史窗口内发生的活动。

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

front matter 包含 schema 版本、组织、生成时间、时区、本地和 UTC 周期、事项数量、warning 数量与 `collection_status`，以及 collector 在首个 GitHub 请求前记录的 Git HEAD、dirty 状态、tracked diff 和 untracked inventory 哈希。工作树快照摘要由这些分量规范化计算，避免不同 clean commit 产生相同摘要；validator 和 manifest 会重新计算并交叉校验。索引只记录事实，不判断重要性。Search 截断、分页不完整、非对象候选项或事项端点失败会把 evidence 标记为 `partial`；正式周报 validator 会拒绝 partial evidence。

正式周报还必须附带 index trace、candidate-pool、selection ledger、Tech-Doc-Style-Chinese 润色记录和 run manifest：index trace 记录实际返回的全部索引页；candidate-pool 包含 evidence 中全部 PR 和最多 24 个 Issue 工程候选；ledger 为候选池每项记录索引信号、读取视图/实际输出字节数、块完整性、人类/maintainer/bot 计数、Contribution Gates 判定与来源 URL、选择或排除原因和排序；风格记录将输入稿与已审查的最终报告哈希绑定到所用的 Tech-Doc-Style-Chinese Skill。正式链路只接受由同一 checkout 中当前 collector 生成的 evidence，不接受旧版、手工编辑或其他 renderer 生成的同版本文件。manifest 重新校验这些输入文件及哈希，绑定 Skill 内固定的上游 Contribution Gates commit/blob 和预审策略正文哈希，重放每条 reader 输出并核对 ledger 的字节数、块完整性、活动计数和门禁来源 URL，再执行该 Skill 的 `scripts/lint_copy_rules.py`。活动 URL 必须来自 fenced GitHub 正文之外的 collector source 行，并归属于 maintainer 或 PR 作者；PR 根 URL 只允许与 `triage` metadata 和非空 `body` 视图共同证明正文。报告中的共用 `证据来源` 只承担事项级导航与周期活动摘要；门禁的精确 deep link、读取视图和 actor 归因以 ledger 与 manifest 为准。manifest 还会拒绝可见判定依据与 ledger 不一致，或已确认违规事项出现在独立章节之外。Skill 自带 `record_candidate_pool.py`、`record_polish_review.py` 和 `write_run_manifest.py` 用于完成这一步。

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

Agent 只能通过 Skill 自带的 `evidence_reader.py` 执行 `overview → index → triage → targeted view` 有界读取；不要直接按 `ITEM_START` 标记、Document Map 或全文搜索读取 evidence。每个 reader 响应都带有独立的 `UNTRUSTED EVIDENCE` envelope，因为 GitHub 的标题、正文、评论和 review 都是不可执行的外部证据，而不是指令。

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

Skill 明确禁止重新访问 GitHub。它要求 Agent 使用有界 reader、检查 warning 和限制，并把 GitHub 文本视为非可信证据；最终须以 report 和 evidence 一起运行 validator，校验合同与引用溯源。

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
- HTTP 429、HTTP 5xx 和网络错误：指数退避重试；主限额耗尽时等待 `X-RateLimit-Reset`，次级限额缺少 `Retry-After` 时先等待至少 60 秒；
- 多个候选项：默认由 8 个 worker 并行采集，并对共享请求速率设置上限；
- rate limit 按 GitHub 的 `core`/`search` resource 分桶记录，避免把不同配额混为一个剩余量；
- Search API 达到 1,000 条上限、分页不完整或响应含非对象候选项：evidence 标记为 `partial`，不得用于正式周报；
- 单个事项详情或活动端点失败：继续采集其他事项，并把失败写入 `Collection Warnings` 和对应事项的 `Data Gaps`；
- Search、认证或权限请求失败：fail closed，不生成成功样式 evidence 文件；搜索确实为空时仍生成包含完整结构和数据限制的单一 evidence 文件；
- API 字段缺失：使用明确的缺失值或 `Data Gaps`，不推测内容。

## 已知限制

- 没有 timeline events，无法可靠确定 label、assignee、milestone、重新打开和 draft-to-ready 的变更时间；
- 只有 `updated_at` 命中、但没有可验证周期事件的事项不会进入 evidence，因此标签、assignee、milestone、删除分支等单独变化不会成为收录理由；
- 没有 CI/check runs，无法判断 CI 状态或完整 merge readiness；
- 没有 commits，无法判断周期内是否新增 commit；
- 没有文件 diff，无法分析具体改动内容；
- Contribution Gates 评估只能确认 evidence 中存在可归因直接证据的违规，不能证明其余 PR 合规。commit trailer、commit message 作者身份、硬件范围和 AI 作者身份通常不可判定；缺少证据不得作为违规依据。
- Contribution Gates 评估覆盖正式 evidence 中的全部 PR；「活跃」指采集周期内存在活动，包含当前已经关闭或合并的 PR。当前六项门禁针对 PR、改动、commit 和 review thread，普通 Issue 使用 `not_applicable`。
- manifest 只能验证候选覆盖、证据来源和报告交付一致性，不能自动证明分析者对 GitHub 文本的语义判断正确；已确认违规仍需人工复核。普通 AI 使用披露本身不是违规证据。
- Search Issues 单个查询最多暴露 1,000 条结果；当前 collector 会把该情形标记为 partial，正式报告不会放行，后续应按仓库或时间分片完成候选发现；
- 评论类端点使用 `since` 减少历史分页，因此不能保证包含周期前的完整评论上下文；
- `mergeable` 是 GitHub 当前返回的可空快照，不等价于「可合并」结论；
- 部分请求失败时 evidence 会显式标记为 partial；Agent 可以用于人工排障，但不能生成通过 validator 的正式周报。
- 活动端点失败且没有其他可验证周期事件时，事项会被排除；Collection Summary 和 `Collection Warnings` 会分别记录数量与失败详情。

## 测试

测试全部使用本地 fake 或 `httpx.MockTransport`，不访问真实 GitHub API：

```bash
uv run pytest
```

测试覆盖 UTC+8 时间范围、显式日期、并发采集、请求范围参数、分页、重试、Issue/PR 分类、评论/review 周期判断、bot 和 maintainer 标记、部分失败、空结果、截断、front matter、索引、ITEM 标记、固定章节顺序、warning、数据限制和 CLI dry-run。
