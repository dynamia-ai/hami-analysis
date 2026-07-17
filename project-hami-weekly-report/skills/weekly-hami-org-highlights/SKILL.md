---
name: weekly-hami-org-highlights
description: Use when an agent must turn one hami-github-activity Markdown evidence file into an evidence-backed Weekly HAMi Org Highlights report, especially when the file is too large to load into one model context.
---

# Weekly HAMi Org Highlights

基于 `hami-github-activity` 生成的单一 Markdown evidence 文件，形成供 Dynamia 内部研发决策使用的周报。聚焦工程影响和资源投入，不写社区宣传稿或活动流水账。

## 证据边界

- 只使用指定 evidence 文件。不要访问 GitHub、网页、API 或其他数据源补充信息。
- 将事实、推断和建议分开表达。每个判断都关联具体 Issue 或 PR。
- 先检查 `Collection Warnings` 和 `Data Limitations`，再定稿。
- 证据不足时明确说明，不要猜测缺失的状态、动机、根因或时间。
- 不要声称 CI 已通过或失败、PR 已满足完整合并条件、作者已提交修复 commit、PR 只差 CI 即可合并。
- 允许建议「需要人工检查 CI」，但不要编造 CI 结论。
- 评论类证据按采集周期起点请求，周期前上下文可能不完整。不要把未出现的旧评论解释为不存在。

## 大文件读取铁律

Evidence 文件只能通过本 Skill 自带的 `scripts/evidence_reader.py` 读取。不要对 evidence 文件执行不带范围限制的文件读取，也不要使用 `cat`、完整文件 `read` 或无边界的 `sed` 输出。

分多次输出原文仍会累计占用同一个模型上下文。只满足「每次少读一些」不够，还必须限制单次输出、累计原文和主 agent 接收的内容。

- 索引单次最多读取 50 项和 20,000 字节。
- 事项原文单次最多读取 40,000 字节。
- 主 agent 只接收 overview、分页索引和结构化 evidence card；不要接收 `Issue Evidence` 或 `Pull Request Evidence` 的完整原文。
- 文件超过 1 MB 或事项超过 100 个时，如运行环境支持 subagent 或独立上下文，必须把索引分页和事项读取交给独立 worker。
- 每个事项 worker 最多处理 5 个事项或累计读取 120,000 字节原文，以先达到的限制为准。
- 没有独立上下文时，单个 agent 的累计事项原文不得超过 120,000 字节。达到限制后停止扩读，根据已有证据降低结论置信度；不要通过读取整个文件绕过限制。

`evidence_reader.py` 只使用 Python 标准库。所有命令通过 `uv run python` 执行，以使用项目指定的 Python 3.14。以下命令中的 `<SKILL_DIR>` 替换为本 Skill 所在目录，`<EVIDENCE>` 替换为 evidence 文件路径。

## 有界读取命令

### 概览

```bash
uv run python <SKILL_DIR>/scripts/evidence_reader.py overview <EVIDENCE>
```

该命令只返回 YAML front matter、`Document Map`、`Collection Summary`、`Collection Warnings` 和 `Data Limitations`。不要再直接读取这些章节。

### 分页索引

```bash
uv run python <SKILL_DIR>/scripts/evidence_reader.py index issue --offset 0 --limit 50 --max-bytes 20000 <EVIDENCE>
uv run python <SKILL_DIR>/scripts/evidence_reader.py index pull_request --offset 0 --limit 50 --max-bytes 20000 <EVIDENCE>
```

进度和下一页 offset 写入标准错误。按提示更新 `--offset`，直到出现 `end of index`。不要一次请求超过 50 项，也不要把多个分页命令合并成一次大输出。

### 事项初筛视图

```bash
uv run python <SKILL_DIR>/scripts/evidence_reader.py item issue OWNER/REPO#NUMBER --view triage --max-bytes 20000 <EVIDENCE>
uv run python <SKILL_DIR>/scripts/evidence_reader.py item pull_request OWNER/REPO#NUMBER --view triage --max-bytes 20000 <EVIDENCE>
```

`triage` 只返回元数据、周期活动、标签、当前 review 信息、变更规模、最近人类或 maintainer 活动和数据缺口，不返回可能很大的 body、comment 或 review 原文。

### 按需补读

只有一个判断确实依赖被省略的内容时，才补读对应视图：

```bash
uv run python <SKILL_DIR>/scripts/evidence_reader.py item issue OWNER/REPO#NUMBER --view body <EVIDENCE>
uv run python <SKILL_DIR>/scripts/evidence_reader.py item issue OWNER/REPO#NUMBER --view comments <EVIDENCE>
uv run python <SKILL_DIR>/scripts/evidence_reader.py item pull_request OWNER/REPO#NUMBER --view reviews <EVIDENCE>
uv run python <SKILL_DIR>/scripts/evidence_reader.py item pull_request OWNER/REPO#NUMBER --view review_comments <EVIDENCE>
```

可用视图为：

- Issue：`body`、`previous_context`、`comments`；
- PR：`body`、`previous_context`、`comments`、`reviews`、`review_comments`；
- 两者都支持：`triage`、`full`。

默认每次最多返回 40,000 字节。标准错误会报告 `chunk N/M` 和下一块编号；仅在当前判断需要后续内容时，使用 `--chunk N` 继续读取。`full` 只用于无法由定向视图回答的问题，不作为常规读取方式。

## 分层处理流程

严格按以下顺序执行：

1. 使用 `overview` 记录周期、组织、事项数量、warning、失败请求和数据限制。
2. 分页处理 Issue 和 PR 索引。索引阶段只形成候选，不给出最终结论。
3. 文件超过 1 MB 或事项超过 100 个时，把不重叠的索引页交给独立 worker。每个索引 worker 最多返回 10 个候选，每个候选只包含一行：`ID | 索引信号 | 需要补读的原因`。
4. 主 agent 合并候选并持续替换低优先级项，候选池最多保留 24 个 Issue 和 24 个 PR。不要把每一页的候选无上限追加到列表。
5. 对候选运行 `triage`。事项 worker 返回 evidence card，不返回原始 Markdown。
6. 主 agent 根据 evidence card 选择需要进入周报或参与聚类的事项。只有信息缺口会影响判断时，才使用定向视图补读 body、comment 或 review。
7. 形成跨事项主题前，确认主题关联事项都已有 evidence card。至少两个事项才能形成主题。
8. 根据 overview 中的 warning 和限制调整置信度，按规定格式生成周报。未达到门槛的章节不要用低价值事项凑数。

每张 evidence card 最多 1,000 个中文字符，固定包含：

```text
ID 与 URL：
周期内可验证活动：
已知事实：
工程影响：
人类或 maintainer 信号：
信息缺口：
建议分类：
```

主 agent 保留候选清单、已读视图和 evidence card，避免重复读取。原始 body、comment 或 review 内容不要复制进主 agent；只保留支持结论的短摘要和 URL。

## 常见错误

- 先执行 `wc` 或 `rg`，随后仍直接打开整个文件：文件大小检查不能替代有界读取。
- 把全部索引页交给同一个 worker：单次输出受限，但累计上下文仍会持续增长。
- 每个候选都读取 `full`：先使用 `triage`，再只补读影响判断的具体视图。
- worker 把原始 Markdown 复制给主 agent：只返回规定格式的 evidence card。
- 忽略标准错误中的下一页 offset 或 `chunk N/M`：这会造成漏页、重复读取或误判信息完整性。

## 候选筛选

### Issue

重点考虑：

- 是否影响真实部署、生产用户或 Dynamia 客户；
- 是否为 regression；
- 是否涉及调度错误、资源隔离错误、崩溃或数据错误；
- 是否影响 GPU、NPU 或其他加速器核心能力；
- 是否涉及 Kubernetes、驱动、CUDA、容器运行时或硬件兼容性；
- 是否有多个独立用户确认和有效复现信息；
- 是否可能阻塞发布或持续增加支持成本；
- 是否与 Dynamia 的产品和技术路线相关。

### Pull Request

重点考虑：

- 是否修复重要问题或实现战略能力；
- 是否影响核心调度或资源隔离逻辑；
- 是否解决一个或多个活跃 Issue；
- 是否值得 Dynamia 帮助外部贡献者完成；
- 是否存在 maintainer review、架构决策或产品决策需求；
- 是否存在设计、兼容性或维护风险。

`mergeable` 只是 GitHub 当前的可空字段，不是 CI 或完整 merge readiness 结论。

## 聚类规则

主动识别以下关系：

- 相同硬件、驱动、运行时或 Kubernetes 兼容性问题；
- 相同调度、资源隔离或设备分配语义；
- 同一组件的多个故障；
- 同一功能的 Issue 与实现 PR；
- 多个用户对同一问题的独立报告。

一个主题至少关联两个具体事项。只有一个事项时按单项分析，不强行聚类。说明共同信号时区分：

- 「已知事实」：evidence 直接给出的状态、正文、评论、review 或时间；
- 「分析推断」：从多个事实形成的工程判断；
- 「信息缺口」：timeline、CI、commit、diff 或失败请求导致的未知项。

## 投入建议

建议必须具体到工程动作，并使用以下粗粒度投入规模之一：

- `quick review`
- `several engineer-hours`
- `one engineer-day`
- `multi-day investigation`
- `requires technical owner`

推荐动作应尽量是最小可验证下一步，例如复现、定位 owner、完成设计 review、补兼容性矩阵、检查当前 CI、验证 workaround 或与报告者确认环境。

## Markdown 格式合同

周报中的顶层分析条目统一使用有序列表。`Evidence limitations` 使用项目符号列表；分析条目内部的字段使用缩进项目符号。这三种结构分别承担固定语义，不互换。

### Issue 和 PR 链接

每一次 Issue 或 PR 引用都使用完整 Markdown 链接，不使用 `#1190`、`HAMi#1190` 或 `Project-HAMi/HAMi#1190` 纯文本缩写。

固定格式：

```markdown
[Project-HAMi/HAMi#1190](https://github.com/Project-HAMi/HAMi/issues/1190)
[Project-HAMi/HAMi#2066](https://github.com/Project-HAMi/HAMi/pull/2066)
```

链接文字固定为 `Project-HAMi/REPO#NUMBER`。Issue URL 使用 `/issues/NUMBER`，PR URL 使用 `/pull/NUMBER`。同一句包含多个事项时，每个事项分别提供链接。摘要、标题、字段、括号说明和 one engineer-week 回答中的重复引用也使用链接。

### 列表层级

以下章节的顶层条目都使用从 `1.` 开始、连续递增的有序列表：

- `Executive Summary`
- `Must Pay Attention`
- `Worth Engineering Investment`
- `Pull Requests Requiring Action`
- `Important Resolutions`
- `Emerging Engineering Themes`
- `Recommended Resource Allocation`
- `Active but Not Worth Investing This Week`

不要把条目写成 `### 1. 标题`。三级标题只用于 `Pull Requests Requiring Action` 的类别，例如 `### Review now`，以及固定的 `### One engineer-week priority`；跨 PR 类别编号在整个章节中继续递增。每个 PR 类别标题和该类别第一条有序列表之间也保留一个空行。

每个详细条目使用以下结构：

```markdown
1. **[Project-HAMi/HAMi#1190](https://github.com/Project-HAMi/HAMi/issues/1190)：glibc 兼容性导致资源隔离失效**

   - 相关事项：[Project-HAMi/HAMi#1190](https://github.com/Project-HAMi/HAMi/issues/1190)
   - 已知事实：周期内出现可验证的人类活动。
   - 工程影响：影响 GPU core 隔离。
   - 建议下一步：在受影响环境中复现。
   - 建议投入类型：`several engineer-hours`
```

规则如下：

- 章节标题和第一条有序列表之间保留一个空行。
- 顶层条目和缩进字段之间保留一个空行。
- 字段统一缩进 3 个空格并使用 `-`。
- `Evidence limitations` 保持顶层 `-` 列表，不使用序号。
- one engineer-week 问题使用 `### One engineer-week priority` 三级标题，下面直接写一个结论段落和一个理由段落，不再创建第二组序号列表。
- 投入字段名只使用 `建议投入类型` 或 `投入规模`，字段值只使用前文列出的 5 个精确值，不创造别名或拼写变体。

### 格式校验

成稿后运行：

```bash
uv run python <SKILL_DIR>/scripts/validate_report.py <REPORT>
```

校验器检查标题与章节是否完整且顺序一致、所有 Issue/PR 引用是否为匹配的 GitHub 链接、分析章节是否使用连续有序列表、字段缩进与列表空行是否正确、one engineer-week 回答是否为两个段落，以及投入字段和值是否来自固定词表。校验失败时修正报告并重新运行；只有输出 `Report format is valid.` 才能交付。

## 周报格式

严格使用以下标题和顺序：

```markdown
# Weekly HAMi Org Highlights

Period:
Organization:
Issues with activity:
Pull requests with activity:
Evidence limitations:

## Executive Summary

## Must Pay Attention

## Worth Engineering Investment

## Pull Requests Requiring Action

## Important Resolutions

## Emerging Engineering Themes

## Recommended Resource Allocation

## Active but Not Worth Investing This Week
```

### Executive Summary

最多 6 条。使用有序列表，概括最需要关注的问题、最值得投入的方向、最需要采取行动的 PR、共同技术风险和需要 Dynamia 决策的主题。

### Must Pay Attention

最多 5 项。只纳入严重影响真实使用、明显 regression、核心调度或资源隔离问题、重要兼容性风险、稳定性或数据风险，以及可能阻塞发布或重要用户的问题。

使用有序列表。每项包含：

- 相关 Issue 或 PR；
- 已知事实；
- 必须关注的原因；
- 延迟处理风险；
- 建议下一步；
- 建议投入类型。

没有符合标准的事项时写「本周未发现」，不要降低标准填充。

### Worth Engineering Investment

最多 8 个事项或主题。使用有序列表。每项包含相关 Issue 和 PR、工程价值、用户或社区证据、当前投入理由，以及最小下一步。

### Pull Requests Requiring Action

最多 8 项。使用以下类别之一：

- `Review now`
- `Help contributor finish`
- `Maintainer decision required`
- `Investigate before merge`
- `Check current CI and merge readiness`

各类别使用三级标题，条目在整个章节中使用连续有序列表。每项说明 PR 的目标、当前已知状态、阻塞点或信息缺口、Dynamia 应采取的行动，以及占用研发时间的理由。

### Important Resolutions

最多 6 项。使用有序列表，总结重要 Issue 的解决结论、明确 workaround、根因确认、重要设计决定、已合并的重要修复或能力，以及明确不支持的情况。不要只写「已关闭」或「已合并」。证据未给出结论时不要补写。

### Emerging Engineering Themes

最多 5 个主题。使用有序列表。每个主题关联至少两个具体 Issue 或 PR，说明共同信号、事实与推断边界，以及对 Dynamia 研发规划的意义。

### Recommended Resource Allocation

最多 5 条，按优先级使用有序列表。每条包含工程主题、关联事项、推荐动作、投入规模、预期结果和延迟处理风险。

最后单独、明确回答：

```text
If Dynamia can invest only one engineer-week in HAMi next week,
where should that effort go?
```

### Active but Not Worth Investing This Week

使用有序列表，列出少量活跃但当前不建议额外投入的事项，并给出证据支持的原因，例如等待报告者补充、信息不足、影响有限、已有其他 owner、只有 bot 活动、已有明确推进或低影响依赖/文档变更。

## 最终检查

- 是否只通过 `evidence_reader.py` 有界读取 evidence；
- 大文件是否使用独立 worker，主 agent 是否只接收 evidence card；
- 是否遵守索引 20,000 字节、事项单次 40,000 字节和 worker 累计 120,000 字节限制；
- 是否记录已读视图并避免重复加载；
- 每一次 Issue 或 PR 引用是否都使用匹配的 GitHub Markdown 链接；
- 分析章节是否统一使用连续有序列表，详细字段是否使用缩进项目符号；
- 是否存在 `### 1.` 数字标题或列表前缺少空行；
- 投入规模是否严格来自固定词表；
- 是否把索引事实误写成完整上下文；
- 是否把 bot 活动误当成人类或 maintainer 信号；
- 是否忽略了 Collection Summary 中仅因 `updated_at` 命中而被排除的事项数量；
- 是否出现 evidence 不支持的 CI、commit、diff 或 timeline 结论；
- 是否重复逐项罗列而没有聚类；
- 是否明确回答 one engineer-week 问题；
- 是否把 warning 和限制写入 `Evidence limitations`；
- `validate_report.py` 是否输出 `Report format is valid.`。
