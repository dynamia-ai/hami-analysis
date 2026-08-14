---
schema_version: "1.0"
source_repository: "Project-HAMi/HAMi"
source_commit: "183239325af912a8ecd5cff19f99f1251c9acf8d"
source_blob: "8f6763dbe5df3d40324352b8fa3539801146df80"
source_path: "CONTRIBUTING.md"
source_anchor: "contribution-gates"
gate_ids: "author-understanding,hardware-validation,scope-and-commit-messages,review-replies,commit-trailer-hygiene,ai-generated-review-comments"
---

# HAMi Contribution Gates 判定规则

本文件固定周报使用的 Contribution Gates 版本。正式运行不得联网获取或自行改写规则；策略更新必须同时修改本文件、校验器和测试。

来源：[Project-HAMi/HAMi CONTRIBUTING.md](https://github.com/Project-HAMi/HAMi/blob/183239325af912a8ecd5cff19f99f1251c9acf8d/CONTRIBUTING.md#L75-L89)。

## 门禁目录

1. `author-understanding`：maintainer 询问改动原理时，作者无法解释实现。
2. `hardware-validation`：影响设备分配或容器内隔离的生产改动，未在真实 GPU 上验证，或未在 PR 中记录测试内容、设备类型和驱动版本。仅涉及 scheduler extender 的改动可以使用 mock 或单元测试。
3. `scope-and-commit-messages`：大规模 AI 生成 PR；超过小修复范围的 AI 辅助改动未先创建 Issue 或未拆分为可审阅的 commit；commit message 不是作者本人编写。
4. `review-replies`：作者回复不是本人编写、未回应具体 review 意见，或直接使用模板化、逐字 AI 回复。
5. `commit-trailer-hygiene`：把 AI 列为 co-author，或使用 `assisted-by`、`co-developed-by` 等类似 trailer。AI 使用情况只能在 PR 描述中披露。
6. `ai-generated-review-comments`：在 review thread 中发布 AI 生成的评论。仅允许把 AI 用于语言翻译或格式调整，且技术判断必须完全来自作者本人，并由作者完成核验和编辑。

## 判定边界

- 只有可归因的直接证据才能使用 `confirmed_non_compliant`。直接证据包括 maintainer 的明确结论、作者明确承认某个具体违规条件，或 evidence 已采集且能够直接验证的机器事实。每项结论必须引用已经完整读取的证据视图和该视图中的 GitHub 来源 URL。
- maintainer 提问后尚未收到回复、bot finding、文本风格、改动规模、标题中的 AI 关键词、缺少字段或未采集的数据都不能单独证明违规。
- 作者按要求披露使用 AI 是合规动作，本身绝不是违规证据。`scope-and-commit-messages` 只有在直接证据同时确认 AI 辅助，并确认「大规模生成」、改动超过小修复却未先建 Issue、未拆成可审阅 commit，或 commit message 由 AI 生成时才能失败；普通大型人工 PR 不因这些条件失败。`review-replies` 中「回复不是作者本人编写」以及「模板化或逐字 AI 回复」两个分支需要 AI 使用或作者身份的直接证据；maintainer 明确确认回复没有回应具体 review 意见时，可独立确认该门禁失败。`ai-generated-review-comments` 必须有证据表明相应评论本身由 AI 生成。不得从一般 AI 披露推导任何违规。
- 周报以 PR 为隔离单位。活动证据 URL 必须归属于 PR metadata 作者，或来自 maintainer 对该 PR 的明确结论。当前 evidence 不能证明普通第三方评论者是共同作者或代表 PR 作者，因此第三方自述只能证明该评论者自己的行为，不能单独把整个 PR 标为 `confirmed_non_compliant`；这类情况使用 `insufficient_evidence`，等待 maintainer 确认归属。
- 当前 evidence 不采集 commit 列表、commit message、trailer、文件 diff 或完整 review reply 关系，因此不能证明所有门禁均已满足。未发现直接违规证据使用 `no_confirmed_violation`；数据缺口影响判断时使用 `insufficient_evidence`。这两个状态都不是合规证明。
- 六项门禁均针对 PR、改动、commit 或 review thread。普通 Issue 使用 `not_applicable`；Issue 可以作为 `scope-and-commit-messages` 的前置证据，但 Issue 本身不因模板字段缺失或信息不足而违反这些门禁。
- Draft、maintainer 创建、已合并或已关闭都不是豁免条件，也不是合规或违规证明。
- `hardware-validation` 必须先确认改动确实影响生产设备分配或容器内隔离。仅涉及 scheduler extender 的改动适用 mock 或单元测试例外；证据缺失或内容截断时不得按「未测试」判定。
- 不得从写作风格推断 AI 作者身份。人工 `Co-authored-by` 不违反 `commit-trailer-hygiene`。

## 周报范围

门禁评估覆盖本周期 evidence 中的全部 PR；「活跃」是指采集周期内存在活动，包含当前已经关闭或合并的 PR。当前六项门禁不适用于普通 Issue，因此 Issue 只参与原有工程价值筛选。只把 `confirmed_non_compliant` PR 写入独立章节；其余状态仍按工程影响进入普通章节或被排除。
