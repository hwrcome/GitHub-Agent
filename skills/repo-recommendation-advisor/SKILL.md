---
name: repo-recommendation-advisor
description: "引导代理将各种分析指标（检索得分、硬件兼容性、活跃度、代码质量）综合成一份全面专业的 GitHub 代码库推荐报告。在最后阶段使用此功能向用户展示排名后的代码库。"
---
# 报告生成 SKILL（GitHub 仓库推荐报告）

## 目标

基于**输入中提供的候选仓库及其已计算指标**，为用户问题生成一份**可直接用于决策的高质量 Markdown 报告**。

该 SKILL 用于排序完成之后（例如已经有 `state.final_ranked`）。

## 输入（契约）

你会获得：

- **用户问题**：用户的原始需求描述。
- **仓库列表**：Top-N 仓库（已按最终得分排好序）。每个仓库包含下列字段中的部分或全部。

### 你可能看到的仓库字段

- 标识信息：`title`, `full_name`, `link`, `clone_url`
- 热度：`stars`
- 相关性：`semantic_similarity`, `cross_encoder_score`
- 活跃度：`activity_score`, `commit_frequency`, `pr_count`, `latest_commit_days`, `open_issues_count`
- 代码质量：`code_quality_score`, `code_quality_issues`, `python_files`
- 证据片段：`combined_doc`（可能被截断）
- 最终得分：`final_score`

如果某个字段缺失，视为**未知**（不得猜测/编造）。

## 硬性规则（必须遵守）

- **禁止幻觉**：不得编造输入里没有的事实（许可证、维护者背景、benchmark、融资/采用情况、发布节奏等）。
- **可追溯性**：每个关键结论必须能回指到具体输入字段或 `combined_doc` 的短引用。
- **必须给链接**：对推荐项必须输出仓库 `link`。
- **明确不确定性**：信息缺失时写“未提供/未知”，不要猜。
- **不要长引用文档**：`combined_doc` 只用于简短证据，引用要短。

## 输出格式（严格 Markdown）

你必须输出一个 Markdown 文档，并且必须按以下章节顺序排列（顺序不可变）。

### 1）执行摘要（Executive summary）

- 1–3 条要点，说明最终推荐及其原因（用指标支撑）。

### 2）推荐清单（Top recommendations）

给出：

- **首推（1 个仓库）**：最符合用户需求的方案
- **备选（2 个仓库）**：不同权衡下的优质替代

每个推荐仓库必须包含：

- 仓库名称 + 链接
- 面向用户问题的 1 句定位（它解决什么/适用什么）
- 证据要点：引用可用指标（相关性/活跃度/质量/Stars），尽量给出数字
- 一行“**最适合 / 不太适合**”说明

### 3）对比表（Top-N）

给出 Top-N 的对比表（按输入顺序/排名），列为：

| Rank | Repo | Stars | Relevance（semantic, CE） | Activity | Quality | Why it’s here |

规则：

- `Repo` 必须是使用 `link` 的 Markdown 链接。
- Relevance 尽量同时展示两项：`semantic_similarity` 与 `cross_encoder_score`（若缺失则写未提供）。
- Activity 总结 `activity_score`，并在可用时补充 `(commit_frequency, pr_count, latest_commit_days)`。
- Quality 使用 `code_quality_score`（可用时）。
- “Why it’s here” 为短语（建议不超过 12 个词/一行）。

### 4）深度分析：首推仓库（Deep dive: primary pick）

需要从以下角度展开（都要尽量引用字段）：

- **与需求的匹配度**（结合 `combined_doc` 的简短证据 + 相关性分数）
- **维护/活跃信号**（活跃度指标）
- **质量信号**（质量指标）
- **风险点**（例如质量分偏低、提交陈旧、Issues 过多、关键信息缺失等）

### 5）整体风险与权衡（Risks & trade-offs）

列出跨仓库/跨方案的风险清单，以及如何缓解；必须尽量落到可用指标上。

### 6）下一步（可执行）（Next steps）

给出验证清单（Checklist），例如：

- 跑一个最小 PoC
- 验证文档覆盖度与可用性
- 本地复现关键步骤/示例
- 定义并确认验收标准

## 写作风格要求

- 语言简洁,信息密度高。
- 能给数字就给数字；不能给就写“未提供/未知”。
- 避免空泛表述（如“看起来不错”），要用具体证据支撑。
