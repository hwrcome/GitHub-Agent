## 项目简介

这是一个 **GitHub Agent**：输入用户需求后，自动在 GitHub 上检索候选仓库、抓取文档、进行多维度分析与排序，如果用户需要，可以生成一份 **结构化 Markdown 推荐报告**。
并引入langsmith对agent的能力进行评测。
项目的核心特点：

- **检索 + 语义召回 + 交叉编码器重排**：先广撒网，再精排。
- **多维度分析**：依赖分析、活跃度分析、（可选）代码质量分析。
- **Skill（技能手册）驱动的报告生成**：用 `skills/*/SKILL.md` 约束报告结构与“禁止编造”规则，输出更稳定、更可维护。

---

## 工作流（Pipeline）

入口文件为 `agent.py`，内部通过 LangGraph 构建工作流，整体链路如下：

- 用户输入 `user_query`
- 'analyze_intent'先分析用户的问题是否清晰，若不清晰，进行追问
- `convert_searchable_query`：把自然语言需求转换为 GitHub 搜索标签（`tag1:tag2:...`）
- `ingest_github_repos`：调用 GitHub API 搜索仓库，并为每个仓库构建 `combined_doc`（带缓存与语义提纯）
  - **SQLite 缓存**：使用 `github_cache.db` 持久化缓存 `combined_doc`，默认 **7 天过期**；命中则跳过 GitHub API 拉取与 LLM 调用
  - **规则去噪**：清洗 README/docs 中的徽章、图片、HTML 标签、空链接、过多空行等噪声
  - **LLM 提纯语义**：对清洗后的文档生成 `[AI Summary: ...]` + `[Tags: ...]`，并拼接在 `combined_doc` 开头，提升后续语义召回/重排质量
- `hybrid_dense_retrieval`：语义召回（Top-K）
- `cross_encoder_reranking`：交叉编码器重排（Top-N）
- `threshold_filtering`：阈值过滤 + 最小 star 过滤
- 引入条件路由，如果过滤后合适的仓库数量为0，则重新到`convert_searchable_query`节点生成新的搜索词
- 并行分支：
  - `dependency_analysis`：依赖侧信号
  - `repository_activity_analysis`：活跃度信号（PR/Issue/提交频率/最近提交）
  - `decision_maker`：是否运行代码质量分析（当前实现里可能强制开启）
- `code_quality_analysis`（可选）：通过 MCP 服务克隆仓库并运行 flake8
- `merge_analysis`：合并各分支产物
- `multi_factor_ranking`：多因素归一化加权，计算 `final_score`
- `report_generation`：读取 `skills/.../SKILL.md`，把 Top-N 的结构化数据喂给大模型，如果用户需要，根据SKILL指导生成最终 Markdown 报告（`final_results`）

---

## 目录结构

- `agent.py`：主工作流（LangGraph）与 CLI 入口
- `tools/`：各节点实现
  - `github.py`：GitHub 搜索与文档抓取
  - `activity_analysis.py`：活跃度指标抓取与打分
  - `code_quality.py`：代码质量分析（通过 MCP 调用 `server.py`）
  - `report_generation.py`：最终报告生成节点（LLM + Skill）
  - `skill_manager.py`：Skill 加载工具 `load_skill` 与中间件
- `skills/`：技能手册（每个子目录一个 SKILL）
  - `skills/report_generation/SKILL.md`：推荐报告输出规范（中文）
- `server.py`：MCP 服务端（可选），提供 flake8 静态检查能力

---

## 环境准备

### 1）Python 版本

建议 Python 3.10+。

### 2）安装依赖

本项目依赖较多（LangGraph/LangChain/HTTP 客户端/向量模型等）。你可以先按需安装核心依赖：

```bash
pip install -U python-dotenv pydantic httpx requests
pip install -U langgraph langchain langchain-openai
```

如果你启用了语义召回/交叉编码器（默认会走），通常还需要（按你的实现文件实际 import 为准）：

```bash
pip install -U sentence-transformers torch transformers
```

如果你启用 MCP + flake8 代码质量分析：

```bash
pip install -U mcp flake8
```


---

## 配置（.env）

`agent.py` 会在项目根目录寻找 `.env`：

- 路径：`./.env`（与 `agent.py` 同级）

如果找不到，会在启动时提示你输入 `GITHUB_API_KEY`（getpass 交互输入）。

### 必需：GitHub API Key

```env
GITHUB_API_KEY=ghp_xxx_your_token
```

### 报告生成（LLM）相关

`tools/report_generation.py` 会从环境变量读取模型配置（不同版本实现可能支持 OpenRouter/其他网关；以你当前文件为准）。

常见配置示例（OpenRouter）：

```env
OPENROUTER_API_KEY=your_openrouter_key
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
REPORT_LLM_MODEL=deepseek/deepseek-r1
REPORT_LLM_TEMPERATURE=0.1
REPORT_LLM_MAX_TOKENS=2048
REPORT_LLM_TIMEOUT=180
```

### 文档提纯（LLM Enrichment）相关（可选但推荐）

`tools/github.py` 在抓取仓库 README/docs 后，会调用轻量模型生成摘要与标签（用于增强 `combined_doc` 的语义信号）。对应配置：

```env
# 用于 enrich_documentation_with_llm 的模型
ENRICH_LLM_MODEL=deepseek/deepseek-chat
```

> 注意：文档提纯与报告生成可以使用同一个网关 Key（例如 `OPENROUTER_API_KEY`），也可以拆分为不同 Key（按你的环境变量策略实现）。

---

## 运行方式

### 1）命令行运行

直接运行 `agent.py`（默认会用文件里写死的示例 query）：

```bash
python agent.py
```

如果你希望用自己的 query，建议你改 `agent.py` 里 `AgentStateInput(user_query=...)`，或自行加一个 CLI 参数解析（可后续扩展）。

### 2）启用 MCP 代码质量分析（可选）

`server.py` 是一个 MCP server，用于“克隆仓库 + flake8 检查”。它与主 agent **进程分离**，适合把重任务隔离出去。

注意：

- `tools/code_quality.py` 里 `SERVER_SCRIPT` / `SERVER_PYTHON` 目前是硬编码路径，需要根据你的机器修改为本地实际路径。
- Windows 下 clone / 删除只读文件等问题，`server.py` 已做了一部分防御处理。

---

## Skill 机制：如何扩展你的“报告规范”

项目用 `skills/<skill_name>/SKILL.md` 存放技能手册。

- `tools/skill_manager.py` 提供工具：`load_skill(skill_name)`，可把指定 Skill 注入到模型上下文
- `skills/report_generation/SKILL.md` 约束了最终报告的**章节顺序、对比表列、禁止编造、引用字段规范**

如果你想新增一个 Skill：

1. 新建目录：`skills/<your-skill-name>/`
2. 放入 `SKILL.md`
3. 在 `report_generation` 的 system prompt/策略里引导模型选择加载对应 skill（或你在代码里强制加载）

---

## 常见问题（FAQ）

- **Q：我没找到 `.env` 在哪？**  
  A：默认不存在，需要你自己在项目根目录（与 `agent.py` 同级）新建一个名为 `.env` 的文件。

- **Q：为什么运行时报 `No module named 'langgraph'`？**  
  A：说明依赖未安装。请先 `pip install -U langgraph`（建议后续补依赖清单文件）。

- **Q：为什么报告生成提示没有 API Key？**  
  A：请在 `.env` 里配置你对应网关的 Key（例如 `OPENROUTER_API_KEY`），并重开终端或重新运行程序。

---

