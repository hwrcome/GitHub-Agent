# GitHub Agent 后端服务化设计规格

## 1. 文档状态

- 日期：2026-09-06
- 状态：已确认，进入实现计划阶段
- 范围：将现有 LangGraph GitHub 推荐 Agent 改造为可鉴权、可异步执行、可持久化和可观测的后端服务
- 非范围：本规格不包含具体实现代码；实现计划在本规格获批后单独编写

## 2. 背景与目标

当前项目的核心能力位于 `agent_new.py` 和 `tools/`：它可以完成意图分析、GitHub 仓库搜索、BM25/ColBERT 召回、Cross-Encoder 重排、活跃度/依赖/代码质量分析以及 Markdown 报告生成。当前入口主要是 CLI，任务状态保存在进程内，GitHub 文档缓存由 `tools/github2.py` 直接管理 SQLite，代码质量分析还依赖硬编码的 MCP 解释器路径。

本次改造的目标是增加一个清晰的后端边界，而不是重写 Agent 算法。系统应支持：

1. JWT 登录和基础用户权限控制。
2. `POST /search` 创建长耗时推荐任务，`GET /tasks/{id}` 查询任务状态和结果。
3. `POST /documents` 接收用户文档并异步完成持久化、去重和切片。
4. PostgreSQL 持久化用户、任务、结果、文档和 GitHub 文档缓存。
5. Redis 缓存、限流和任务锁。
6. Celery 后台任务队列、失败重试、幂等提交和任务恢复。
7. pytest 测试、Locust 压测、结构化日志、健康检查和 Prometheus 基础指标。
8. 在核心功能稳定后提供 Docker Compose 一键启动。

成功标准不是达到互联网生产系统的全部复杂度，而是形成一个可运行、可测试、可解释的后端闭环，并能在面试中说明关键工程取舍。

## 3. 设计原则与非目标

### 3.1 设计原则

- Agent 核心与 Web/队列基础设施解耦，通过 `agent_runner` 统一调用。
- PostgreSQL 是任务和结果的事实来源；Celery 的消息状态不作为业务状态来源。
- 所有外部调用都必须有超时、错误分类和日志上下文。
- 长任务通过任务 ID 传递，不把大段文档或完整 Agent 状态塞入消息队列。
- 先提供 mock Agent 模式，使测试和压测不依赖 GitHub、LLM 或 GPU。
- 每个阶段都有可运行的验收路径，避免一次性重构全部模块。

### 3.2 非目标

首版不包含：

- Refresh Token、OAuth 第三方登录和密码找回。
- 复杂组织/租户权限模型；只提供 `user` 和 `admin` 两个角色。
- Kubernetes、Kafka、服务网格和完整分布式链路追踪。
- pgvector 或新的向量数据库；继续使用现有检索实现。
- WebSocket 实时通信；进度查询通过轮询，后续可增加 SSE。
- 用户文档直接参与 GitHub 推荐检索。首版文档接口只完成独立的存储和切片闭环。
- 多 GPU 调度和跨机器模型推理编排。

## 4. 总体架构

```text
HTTP Client
    |
    v
FastAPI API
    |-- JWT / 权限 / 请求校验
    |-- PostgreSQL Repository
    |-- Redis Cache / Rate Limit / Lock
    '-- Celery enqueue
             |
             v
       Celery Worker
             |
             '-- agent_runner
                    '-- LangGraph Agent
                          |-- GitHub API
                          |-- LLM / vLLM
                          '-- MCP 代码质量服务
```

### 4.1 组件边界

```text
app/main.py                 FastAPI 应用和路由注册
app/config.py               环境变量和运行配置
app/db.py                   SQLAlchemy 异步引擎、会话和事务
app/models.py               PostgreSQL ORM 模型
app/schemas.py              Pydantic 请求/响应模型
app/auth.py                 Argon2 密码哈希、JWT 生成和解析
app/dependencies.py         当前用户、角色和基础设施依赖
app/repositories/           数据访问边界，不让路由直接写 SQL
app/services/               搜索、文档、幂等和缓存业务逻辑
app/tasks.py                Celery 应用和 Worker 任务入口
app/agent_runner.py         LangGraph Agent 的唯一服务调用适配层
app/observability.py        日志、request_id、task_id 和 Prometheus 指标
alembic/                    数据库迁移
tests/                      单元测试、API 测试和集成测试
locustfile.py               Mock 模式压测脚本
```

现有 `tools/` 保留为 Agent 的领域工具。`agent_new.py` 的 CLI 入口可以保留，但 Web/Worker 不得依赖 CLI 的交互逻辑。

## 5. API 契约

所有需要用户数据的接口均要求：

```text
Authorization: Bearer <access_token>
```

错误响应统一为：

```json
{
  "error": {
    "code": "TASK_NOT_FOUND",
    "message": "Task does not exist or is not accessible"
  },
  "request_id": "..."
}
```

### 5.1 认证接口

`POST /auth/register`

请求：

```json
{
  "email": "user@example.com",
  "password": "at-least-8-chars"
}
```

返回 `201 Created`，只返回用户公开信息，不返回密码或密码哈希。

`POST /auth/login`

请求使用 JSON body，返回 `200 OK`：

```json
{
  "access_token": "jwt",
  "token_type": "bearer",
  "expires_in": 1800,
  "user": {
    "id": "uuid",
    "email": "user@example.com",
    "role": "user"
  }
}
```

首版 access token 有效期 30 分钟。JWT secret、算法和有效期均由环境变量配置。

### 5.2 搜索接口

`POST /search`

请求：

```json
{
  "query": "寻找适合低显存 GPU 的推理框架",
  "max_results": 20,
  "per_page": 10,
  "include_code_quality": true
}
```

约束：`query` 长度 1-2000；分页和候选数量限制在服务端允许范围内，避免用户提交不受控的资源消耗。

可选请求头：

```text
Idempotency-Key: client-generated-key
```

成功返回 `202 Accepted`：

```json
{
  "task_id": "uuid",
  "status": "PENDING",
  "created_at": "2026-09-06T10:00:00Z"
}
```

服务端同时返回 `Location: /tasks/{task_id}`。如果相同用户、相同接口和相同 `Idempotency-Key` 已提交过相同请求，则返回原任务；相同 Key 对应不同请求体时返回 `409 IDEMPOTENCY_KEY_REUSED`。

### 5.3 任务接口

`GET /tasks/{task_id}`

普通用户只能查看自己的任务，管理员可以查看全部任务。执行中返回：

```json
{
  "task_id": "uuid",
  "task_type": "SEARCH",
  "status": "RUNNING",
  "progress": "RERANKING",
  "retry_count": 0,
  "result": null,
  "error": null,
  "created_at": "...",
  "updated_at": "...",
  "finished_at": null
}
```

成功时 `result` 包含：

```json
{
  "final_results": "Markdown report",
  "repositories": [],
  "filtered_candidates": [],
  "search_history": [],
  "metadata": {}
}
```

首版不额外提供 `/tasks/{id}/result`，避免重复接口；结果只在任务成功时返回。

### 5.4 文档接口

`POST /documents`

首版只接收 JSON，不处理文件上传：

```json
{
  "title": "项目说明",
  "content": "文档正文",
  "metadata": {}
}
```

返回 `202 Accepted`：

```json
{
  "document_id": "uuid",
  "task_id": "uuid",
  "status": "PENDING"
}
```

文档按用户隔离。服务端限制标题、正文和 metadata 大小。使用 `SHA-256(user_id + content)` 去重；同一用户重复提交相同内容时返回已有文档和已有处理任务，不重复切片。

### 5.5 健康检查

`GET /health/live` 只检查 API 进程是否存活，不能依赖数据库。

`GET /health/ready` 检查 PostgreSQL 和 Redis 是否可连接，并返回各依赖状态。依赖不可用时返回 `503`。

## 6. 鉴权和权限

- 密码使用 Argon2id 哈希，数据库只保存哈希值。
- JWT payload 至少包含 `sub`、`role`、`iat`、`exp`。
- 用户默认角色为 `user`；管理员通过受控配置或迁移脚本创建。
- `/search`、`/documents` 和任务查询要求登录。
- 用户不能读取、修改或删除其他用户的任务、文档和文档切片。
- 管理员只能获得跨用户查询能力，不绕过任务状态和数据一致性规则。
- 日志和错误响应不得泄漏密码、JWT、GitHub Token、LLM Key 或完整用户文档。

## 7. PostgreSQL 数据模型

使用 SQLAlchemy 2.0 Async + `asyncpg`，通过 Alembic 管理迁移。时间统一使用带时区的 UTC timestamp；主键使用 UUID。

### 7.1 `users`

```text
id              UUID PRIMARY KEY
email           VARCHAR(320) NOT NULL
password_hash   TEXT NOT NULL
role            VARCHAR(20) NOT NULL DEFAULT 'user'
created_at      TIMESTAMPTZ NOT NULL
updated_at      TIMESTAMPTZ NOT NULL
```

索引：`UNIQUE(lower(email))`。注册时统一保存小写 email。

### 7.2 `tasks`

通用任务表同时承载搜索和文档处理任务：

```text
id              UUID PRIMARY KEY
user_id         UUID NOT NULL REFERENCES users(id)
task_type       VARCHAR(32) NOT NULL
status          VARCHAR(20) NOT NULL
progress        VARCHAR(64) NOT NULL
retry_count     INTEGER NOT NULL DEFAULT 0
celery_task_id  VARCHAR(255)
error_code      VARCHAR(64)
error_message   TEXT
created_at      TIMESTAMPTZ NOT NULL
updated_at      TIMESTAMPTZ NOT NULL
started_at      TIMESTAMPTZ
finished_at     TIMESTAMPTZ
```

允许的 `task_type` 为 `SEARCH` 和 `DOCUMENT_INGEST`。索引：`(user_id, created_at DESC)`、`(status, updated_at)`。

### 7.3 `search_requests`

```text
task_id         UUID PRIMARY KEY REFERENCES tasks(id)
query           TEXT NOT NULL
config          JSONB NOT NULL DEFAULT '{}'
```

### 7.4 `search_results`

```text
task_id                  UUID PRIMARY KEY REFERENCES tasks(id)
final_results            TEXT NOT NULL
repositories_json        JSONB NOT NULL DEFAULT '[]'
filtered_candidates_json JSONB NOT NULL DEFAULT '[]'
search_history_json      JSONB NOT NULL DEFAULT '[]'
metadata_json             JSONB NOT NULL DEFAULT '{}'
created_at               TIMESTAMPTZ NOT NULL
```

结果保存和任务状态切换必须在同一个事务中完成，避免出现任务显示成功但结果不存在的状态。

### 7.5 `documents`

```text
id              UUID PRIMARY KEY
user_id         UUID NOT NULL REFERENCES users(id)
title           VARCHAR(255) NOT NULL
content         TEXT NOT NULL
metadata        JSONB NOT NULL DEFAULT '{}'
checksum        CHAR(64) NOT NULL
status          VARCHAR(20) NOT NULL
ingest_task_id  UUID REFERENCES tasks(id)
created_at      TIMESTAMPTZ NOT NULL
updated_at      TIMESTAMPTZ NOT NULL
```

索引：`UNIQUE(user_id, checksum)`、`(user_id, created_at DESC)`。

### 7.6 `document_chunks`

```text
id              BIGSERIAL PRIMARY KEY
document_id     UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE
chunk_index     INTEGER NOT NULL
content         TEXT NOT NULL
metadata        JSONB NOT NULL DEFAULT '{}'
created_at      TIMESTAMPTZ NOT NULL
```

索引：`UNIQUE(document_id, chunk_index)`。

### 7.7 `repo_cache`

替代 `tools/github2.py` 当前直接操作的 SQLite 表：

```text
repo_name       VARCHAR(255) PRIMARY KEY
combined_doc    TEXT NOT NULL
expires_at      TIMESTAMPTZ NOT NULL
updated_at      TIMESTAMPTZ NOT NULL
```

该缓存是公开 GitHub 仓库文档缓存，不按用户隔离。读取时检查 `expires_at`，写入使用 upsert。具体数据访问通过 Repository 层完成，Agent 工具不在 import 时建表。

### 7.8 `idempotency_keys`

```text
user_id         UUID NOT NULL REFERENCES users(id)
endpoint        VARCHAR(64) NOT NULL
key             VARCHAR(255) NOT NULL
request_hash    CHAR(64) NOT NULL
task_id         UUID NOT NULL REFERENCES tasks(id)
created_at      TIMESTAMPTZ NOT NULL
expires_at      TIMESTAMPTZ NOT NULL
```

索引：`UNIQUE(user_id, endpoint, key)`。创建任务和写入幂等记录必须在一个事务中执行。

## 8. 搜索任务数据流

1. FastAPI 校验 JWT、请求参数和 Redis 搜索限流。
2. 计算规范化请求的 hash；如果带有 `Idempotency-Key`，在数据库事务中检查并写入幂等记录。
3. 在同一事务中创建 `tasks(PENDING)` 和 `search_requests`，提交事务。
4. 事务提交后投递 Celery 任务；任务 ID 只传数据库 `task_id`。
5. Worker 使用行锁或条件更新将任务从 `PENDING` 改为 `RUNNING`，重复投递不能重复执行。
6. Worker 通过 `agent_runner` 使用 `task_id` 对应的 query/config 调用 LangGraph。
7. 在每个重要阶段更新 `progress`，例如 `QUERY_ANALYZED`、`REPOS_FETCHED`、`RERANKING`、`QUALITY_ANALYSIS`、`REPORT_GENERATING`、`DONE`。
8. 成功时在一个事务中写入 `search_results` 并将任务改为 `SUCCEEDED`。
9. 不可恢复错误将任务改为 `FAILED` 并保存不含敏感信息的错误摘要。
10. 数据库提交后才确认 Celery 任务完成。

事务提交和消息投递之间存在极短的崩溃窗口。为避免任务永久停留在 `PENDING`，增加一个恢复扫描器：定期查找超过阈值且没有 `celery_task_id` 的任务并重新投递。该方案首版不引入完整 Outbox 表，但保留后续升级为 Outbox 的边界。

首版不把 `MemorySaver` 当作跨请求会话存储。Web 搜索是独立任务，数据库任务状态是事实来源；CLI 可以继续使用自己的内存 checkpoint。跨请求对话记忆另行设计。

## 9. Redis 设计

### 9.1 缓存

- GitHub 文档缓存优先查 PostgreSQL `repo_cache`，Redis 作为热点缓存。
- 推荐结果缓存 key：`search:v1:{request_hash}:{agent_version}`。
- 缓存命中时仍创建当前用户自己的任务和结果记录，随后直接标记为 `SUCCEEDED`，不绕过权限和审计链路。
- 所有 key 带版本号，Agent 算法或报告格式变化时递增版本。
- 缓存设置 TTL；缓存不可用时降级为直接访问 PostgreSQL 或执行任务，不能让 API 整体不可用。

### 9.2 限流

首版限制：单用户搜索每分钟 10 次，可通过环境变量调整。使用 Redis Lua 脚本原子执行 `INCR + EXPIRE`，超限返回 `429 RATE_LIMITED`，同时返回 `Retry-After`。

### 9.3 任务锁

使用 `lock:search:{request_hash}`，带 TTL 和 token 校验，防止相同请求的多个 Worker 同时运行昂贵 Agent。锁失效或 Redis 短暂不可用时，数据库条件更新仍必须阻止同一任务重复执行。

## 10. 失败处理和重试

Celery 任务按错误类型处理：

- GitHub 429、连接超时、临时网络错误、临时 LLM 5xx：自动重试，指数退避，最多 3 次。
- 单个仓库文档抓取或代码质量分析失败：记录该仓库的局部错误，继续处理其他候选。
- 请求参数错误、鉴权失败、仓库明确不存在：不重试，任务直接失败或跳过该仓库。
- Worker 未捕获异常：更新任务为 `RETRYING` 或 `FAILED`，不能留下永久 `RUNNING`。
- 任务超过最大运行时：撤销/终止由 Worker 执行的任务，并标记为 `FAILED`。

重试必须是幂等的：任务开始前检查终态，结果写入使用主键约束和事务；重复成功回调不得产生重复结果。

## 11. 现有 Agent 的适配要求

### 11.1 `agent_runner`

提供一个稳定接口：

```python
run_search(task_id: UUID, *, mode: Literal["mock", "real"]) -> SearchRunResult
```

适配层负责：

- 从数据库读取 query/config；
- 将 `agent_new.graph` 的输出转换为稳定的 `SearchRunResult`；
- 注入任务进度回调；
- 捕获异常并分类；
- 在 mock 模式返回确定性、可测试的候选结果；
- 延迟导入重量级 Torch、SentenceTransformer 和真实 LLM 模块。

### 11.2 配置解耦

移除 `agent_new.py` import 时的 `getpass()`。缺少 Token 时，真实模式应返回配置错误；只有 CLI 可以选择交互式输入。

将以下路径和参数改为环境变量：

```text
GITHUB_API_KEY
OPENROUTER_API_KEY
OPENROUTER_BASE_URL
MCP_SERVER_SCRIPT
MCP_SERVER_PYTHON
CROSS_ENCODER_MODEL_NAME
AGENT_MODE
```

### 11.3 外部 HTTP 调用

统一为可配置 timeout 的客户端；`tools/activity_analysis.py` 中的同步 `requests` 调用必须补 timeout，后续可迁移到 `httpx`。外部错误不得把 Token 或完整响应体写入日志。

## 12. 文档处理流程

1. API 在事务中创建 `documents(PENDING)` 和 `tasks(DOCUMENT_INGEST)`。
2. Worker 校验 checksum、规范化 metadata 并切分正文。
3. 使用稳定的 chunk size 和 overlap 配置生成 `document_chunks`。
4. 事务性地写入全部 chunks，并将文档状态改为 `READY`。
5. 处理失败时将文档和任务标为 `FAILED`，保留可重试的错误信息。

首版切片结果只提供持久化能力，不改变现有 GitHub Agent 的召回链路；未来接入 RAG 时以 `document_chunks` 为稳定输入边界。

## 13. 可观测性

### 13.1 日志

使用结构化日志，至少包含：

```text
timestamp, level, logger, request_id, task_id, user_id, event, duration_ms
```

记录 API 请求、任务状态转换、Celery 重试、外部依赖错误和缓存命中率。禁止记录密码、JWT、API Key 和完整文档。

### 13.2 指标

`GET /metrics` 暴露 Prometheus 格式指标，首版包含：

- API 请求总数和按路由/状态码分类的计数；
- API 延迟 histogram；
- 搜索任务成功、失败、重试数量；
- Agent 执行耗时；
- GitHub、LLM、MCP 调用失败计数；
- Redis 缓存命中/未命中；
- 当前运行任务数量。

## 14. 测试策略

### 14.1 快速测试

使用 pytest、pytest-asyncio、httpx；通过依赖覆盖和 fake Agent/fake Celery 测试：

- 注册、登录、JWT 过期和错误密码；
- 用户只能访问自己的任务和文档；
- `POST /search` 返回 `202` 和 task ID；
- 状态查询、成功结果和失败错误；
- 幂等 Key 重复提交及冲突请求；
- 参数校验和限流响应；
- Repository 层事务和状态转换；
- Agent 异常后任务进入 `FAILED` 或 `RETRYING`。

### 14.2 集成测试

标记为 `integration`，连接测试 PostgreSQL 和 Redis，验证：

- Alembic 迁移可重复执行；
- 唯一索引和外键约束；
- 事务回滚；
- Redis Lua 限流和锁；
- Celery eager 模式下的任务状态闭环。

真实 GitHub API、真实 LLM、GPU 模型和 MCP 进程不进入默认测试套件。

## 15. 压测策略

新增 `locustfile.py`，默认使用 mock Agent：

```text
登录 -> POST /search -> 轮询 GET /tasks/{id} -> 读取完成结果
```

记录 QPS、平均延迟、P95/P99、错误率、任务吞吐量、数据库连接使用和 Redis 命中率。压测报告必须注明 mock 模式与真实 Agent 模式不能直接比较。

## 16. 本地运行和 Docker 交付

Docker 不参与前几个里程碑的功能开发。核心服务稳定后再提供：

```text
api
worker
postgres
redis
```

并支持：

```bash
docker compose up --build
```

Compose 需要包含数据库/Redis 健康检查、迁移执行、API 与 Worker 共用环境变量，以及持久化数据卷。开发阶段若不用 Docker，必须提供本机 PostgreSQL 和 Redis 的连接配置与启动说明。

## 17. 交付里程碑

### M1：服务骨架

Agent 可安全导入；FastAPI、PostgreSQL、Alembic、JWT、健康检查和 mock 模式可运行。

### M2：搜索任务闭环

`POST /search`、`GET /tasks/{id}`、Celery Worker、任务状态、结果事务和基础权限完成。

### M3：可靠性能力

Redis 缓存/限流/锁、幂等 Key、失败重试、任务恢复和真实 Agent 适配完成。

### M4：文档与质量保障

`POST /documents`、去重、切片、pytest、结构化日志、健康检查和 `/metrics` 完成。

### M5：压测和交付

Locust 脚本、压测记录、Docker Compose 和 README 运行文档完成。

## 18. 验收标准

在 mock 模式下完成以下流程即视为核心闭环通过：

```text
注册用户
-> 登录获取 JWT
-> POST /search 返回 task_id
-> Worker 将任务置为 RUNNING
-> GET /tasks/{id} 可看到进度
-> 任务成功后返回 final_results 和仓库数据
-> 另一用户访问该 task_id 返回 404/无权限错误
-> 相同 Idempotency-Key 不创建第二个任务
-> 失败任务按策略重试并最终进入 FAILED 或 SUCCEEDED
-> POST /documents 返回 document_id/task_id
-> 文档完成去重、切片和 READY 状态
-> pytest 默认套件通过
-> Locust 能完成基础压测
-> /health/live、/health/ready、/metrics 可访问
```

## 19. 已确认的关键取舍

- 先做单 API 服务 + 单 Worker 逻辑，不拆成多个业务微服务。
- PostgreSQL 是业务状态来源，Redis 故障时允许降级，不把 Redis 当唯一数据源。
- 首版使用 Celery + Redis，暂不引入 Kafka。
- 首版不把用户上传文档接入推荐召回，先确保文档服务本身完整。
- 首版不依赖跨进程 LangGraph MemorySaver；搜索任务通过数据库状态实现可恢复性。
- Docker 延后作为交付层，不阻塞前期 API、任务和测试开发。
