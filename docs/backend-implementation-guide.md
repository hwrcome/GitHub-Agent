
# GitHub Agent 后端改造实现说明

本文档对应分支 feat/backend-service，用于说明这次后端改造新增或修改的代码、运行链路、数据结构、测试方式和部署方式。

对比基线为 main 分支。本次改造不是重写原有 Agent，而是在保留 LangGraph 推荐流程的基础上，增加一个可以被客户端调用、异步执行、持久化查询状态并且可以部署的后端服务。

## 1. 改造后的整体结构

~~~text
客户端
  |
  | HTTP + Bearer JWT
  v
FastAPI (app/main.py)
  |
  +-- 认证与权限检查 (app/auth.py, app/dependencies.py)
  +-- 参数校验 (app/schemas/*)
  +-- API 路由 (app/api/*)
  |      |
  |      +-- PostgreSQL: 用户、任务、请求、结果、文档
  |      +-- Redis: 限流、缓存、分布式锁
  |      +-- Celery: 投递后台任务
  |
  +--------------------------+
                             |
                             v
                    Celery Worker (app/tasks.py)
                             |
                             +-- 搜索任务 -> app/services/task_service.py
                             |                -> app/agent_runner.py
                             |                -> mock Agent 或 agent_new.py
                             |
                             +-- 文档任务 -> app/services/document_service.py
                                              -> 分块并写入 document_chunks
~~~

API 进程只负责认证、校验、写入任务和返回任务 ID；耗时的 Agent 搜索和文档切分由 Celery worker 执行，因此请求线程不会被 GitHub、LLM、MCP 或本地模型调用阻塞。

## 2. 对外 API

### 2.1 注册和登录

实现文件：app/api/auth.py、app/auth.py、app/schemas/auth.py。

#### POST /auth/register

处理逻辑：

1. RegisterRequest 将邮箱去空格并转为小写，密码限制为 8 到 256 个字符。
2. 通过 get_user_by_email 检查已有用户。
3. 使用 pwdlib 的 Argon2 实现密码哈希，数据库只保存 password_hash，不保存明文密码。
4. 提交 User 事务。并发注册相同邮箱时，数据库唯一索引触发的 IntegrityError 会被转换为 USER_EXISTS。
5. 返回 UserPublic，不返回密码哈希。

#### POST /auth/login

1. 按规范化邮箱查询用户。
2. 使用 Argon2 校验密码。
3. 校验失败统一返回 401 INVALID_CREDENTIALS，不区分用户不存在和密码错误。
4. 使用 create_access_token 签发 HS256 JWT。JWT 包含 sub、role、iat、exp。
5. 返回访问令牌、令牌类型、过期秒数和公开用户信息。

### 2.2 POST /search

实现文件：app/api/search.py、app/services/search_service.py。

处理顺序：

1. require_user 从 Authorization: Bearer <token> 中解析当前用户。
2. SearchRequestCreate 校验 query、max_results、per_page 和 include_code_quality；纯空白查询会被拒绝。
3. RateLimitService 使用 Redis Lua 脚本执行固定窗口限流。默认每个用户每分钟最多 10 次搜索，超限返回 429 RATE_LIMITED 和 Retry-After。Redis 不可用时按当前策略降级为放行并记录告警。
4. SearchService.submit 对规范化请求计算 SHA-256，并处理 Idempotency-Key：
   - 已存在且请求哈希相同：直接返回原任务。
   - 已存在但请求哈希不同：返回 409 IDEMPOTENCY_KEY_REUSED。
   - 并发请求在唯一约束处冲突时，回滚当前事务并重新读取胜出的幂等记录，返回同一任务。
5. TaskService.create_search_task 在同一事务中写入 tasks 和 search_requests。
6. 数据库提交成功后调用 enqueue_search_after_commit，投递 Celery 任务并把返回的 Celery ID 写入 tasks.celery_task_id。
7. 返回 202 Accepted、任务 UUID、初始状态和 Location: /tasks/{id}。

先提交数据库、后投递队列，可以避免 worker 在数据库记录尚未提交时读取不到任务；保存 Celery ID 后，恢复扫描器能判断任务是否已经发布。

### 2.3 GET /tasks/{id}

实现文件：app/api/tasks.py、app/schemas/tasks.py。

1. 解析路径中的 UUID。
2. 普通用户只能读取自己的任务；管理员可以读取任意任务。
3. 任务不存在或不属于当前用户时统一返回 404 TASK_NOT_FOUND，不暴露资源是否存在。
4. 任务成功时从 search_results 读取 final_results、repositories、filtered_candidates、search_history 和 metadata。
5. 任务失败时返回 error.code 和经过脱敏、长度限制的 error.message。

### 2.4 POST /documents

实现文件：app/api/documents.py、app/services/document_service.py。

1. DocumentCreate 校验标题和正文不能为空白，正文最多 2,000,000 个字符，metadata 序列化后最多 16 KiB。
2. 根据 user_id + content 计算 SHA-256 checksum。同一用户重复提交相同正文时直接返回已有文档和任务。
3. 新文档在一次事务中创建 DOCUMENT_INGEST 任务和 PENDING 文档。
4. 数据库唯一约束处理并发重复提交；失败的一方回滚后重新读取已存在文档，返回原任务。
5. 事务提交后投递 process_document_task，并保存 Celery ID。
6. 返回 202 Accepted、document_id、task_id 和状态。

### 2.5 统一错误格式

实现文件：app/errors.py、app/main.py。

应用层错误统一为：

~~~json
{
  "error": {
    "code": "TASK_NOT_FOUND",
    "message": "Task does not exist or is not accessible"
  },
  "request_id": "8a3e..."
}
~~~

ApiError 保存 HTTP 状态码、业务错误码、用户可见消息和可选响应头。FastAPI 注册三类处理器：

- ApiError：返回业务错误。
- RequestValidationError：统一为 422 VALIDATION_ERROR。
- 未捕获异常：统一为 500 INTERNAL_ERROR，避免向客户端泄露堆栈。

## 3. 任务执行和状态机

### 3.1 状态定义

Task.status 的数据库约束只允许：

| 状态 | 含义 |
| --- | --- |
| PENDING | 已创建，等待 worker 执行 |
| RUNNING | worker 已领取并正在执行 |
| RETRYING | 暂时性错误，等待 Celery 再次执行 |
| SUCCEEDED | 执行完成并已经持久化结果 |
| FAILED | 已达到失败条件，记录错误信息 |

progress 是面向客户端的阶段字符串。Mock Agent 会产生 QUERY_ANALYZED、REPOS_FETCHED、RERANKING、REPORT_GENERATING、DONE。当前数据库最终至少会保存 STARTING 或 DONE，中间回调暂存在 worker 内存列表中。

### 3.2 Celery 配置

实现文件：app/celery_app.py、app/tasks.py。

- broker 和 result backend 默认使用 Redis。
- 任务序列化和结果序列化使用 JSON。
- acks_late=True：任务完成后确认，worker 崩溃时允许重新投递。
- task_reject_on_worker_lost=True：worker 丢失时拒绝任务。
- 单任务硬超时 900 秒，软超时 840 秒。
- 搜索任务最多重试 3 次，退避时间为 1、2、4 秒。
- 文档任务最多重试 2 次，异常会被标记为 DOCUMENT_INGEST_FAILED。

### 3.3 搜索 worker

run_search_task 的流程：

1. 将字符串任务 ID 转成 UUID。
2. 调用 execute_search_task。
3. 成功后增加 task_success_total，标签为 SEARCH。
4. 捕获 TransientAgentError 时增加重试指标；尚有次数时写为 RETRYING 并调用 self.retry；达到上限时先写为 FAILED 再抛出异常。
5. 其他异常经过 sanitize_error 脱敏后写入 FAILED。

execute_search_task 通过 SELECT ... FOR UPDATE 抢占任务。只有 PENDING 或 RETRYING 可以进入执行，已完成任务和正在运行任务直接返回，保证重复投递不会重复写结果。

### 3.4 文档 worker

process_document_task 调用 execute_document_task：

1. 锁定任务和文档行。
2. 任务已经 SUCCEEDED/FAILED，或文档已经 READY/FAILED 时直接返回。
3. split_document 默认使用 1000 字符 chunk、100 字符 overlap。
4. 删除该文档旧 chunks，再批量插入新 chunks。
5. 文档改为 READY，任务改为 SUCCEEDED/DONE。
6. 异常时同时将任务和文档标记为失败。

### 3.5 失败任务恢复

app/services/recovery_service.py 提供 recover_pending_tasks：

1. 查询创建时间超过阈值、状态为 PENDING 且 celery_task_id IS NULL 的任务。
2. 使用 skip_locked 避免多个恢复进程重复处理同一行。
3. 根据 task_type 选择搜索或文档 Celery task。
4. 投递成功后保存 Celery ID 并提交事务。

恢复函数已经实现，但尚未绑定周期调度器；生产环境可接入 Celery Beat、Windows 任务计划或外部 cron。

## 4. Redis 能力

实现文件：app/redis_client.py、app/services/cache_service.py、app/services/rate_limit_service.py、app/services/lock_service.py。

### 4.1 搜索缓存

worker 根据 query 和 config 的稳定 JSON 序列化结果计算 SHA-256，缓存键格式为：

~~~text
search:v1:<sha256>:agent
~~~

缓存值是 SearchRunResult.model_dump(mode="json")，TTL 为 3600 秒。命中时跳过 Agent，直接把结果写入当前任务的 search_results。缓存读写失败会记录告警并降级为正常执行。

### 4.2 限流

限流键格式为：

~~~text
rl:search:user:<user_id>
~~~

Redis Lua 脚本原子执行 INCR + EXPIRE + TTL，避免并发计数竞态。API 根据 RateLimitDecision.allowed 决定是否返回 429。

### 4.3 分布式锁

锁键格式为：

~~~text
lock:search:v1:<sha256>:agent
~~~

锁值使用随机 token，TTL 为 900 秒。释放锁时使用 Lua 脚本比较 token 后再删除，防止持锁者过期后误删其他 worker 的锁。

- 成功获取锁：执行 Agent，最后释放锁。
- 锁被其他 worker 占用：抛出 TransientAgentError，交给 Celery 重试。
- Redis 不可用：抛出 LockUnavailable，当前实现允许降级执行。

## 5. PostgreSQL 数据模型

实现文件：app/models.py、alembic/versions/0001_initial_schema.py。

### 5.1 表和用途

| 表 | 关键字段 | 用途 |
| --- | --- | --- |
| users | id、email、password_hash、role | 账号、密码哈希和角色 |
| tasks | id、user_id、task_type、status、progress、celery_task_id | 所有异步任务的统一状态 |
| search_requests | task_id、query、config | 搜索任务输入 |
| search_results | task_id、final_results 和 JSONB 字段 | 搜索任务输出 |
| documents | id、user_id、content、checksum、status | 用户上传文档 |
| document_chunks | document_id、chunk_index、content | 文档切分结果 |
| repo_cache | repo_name、combined_doc、expires_at | 仓库文档缓存持久化边界 |
| idempotency_keys | user_id、endpoint、key、request_hash、task_id、expires_at | 防止客户端重试生成重复任务 |

### 5.2 约束和索引

- users.email 使用 lower(email) 唯一索引，实现大小写不敏感的邮箱唯一性。
- users.role 只允许 user/admin。
- tasks.task_type 只允许 SEARCH/DOCUMENT_INGEST。
- tasks.status 只允许五种状态。
- tasks(user_id, created_at DESC) 支持用户任务列表查询。
- tasks(status, updated_at) 支持恢复扫描和状态筛选。
- documents(user_id, checksum) 唯一，防止同一用户重复保存相同正文。
- document_chunks(document_id, chunk_index) 唯一，防止一个文档出现重复序号。
- idempotency_keys(user_id, endpoint, key) 唯一，保证幂等键只能对应一个请求。
- 所有时间字段使用带时区的 UTC 时间。

### 5.3 事务边界

- 搜索创建：任务和搜索请求必须在同一事务中成功。
- 文档创建：任务和文档必须在同一事务中成功。
- 搜索结果：任务状态和 search_results 在最终写入阶段同一事务提交。
- 文档 chunks：删除旧 chunks、写入新 chunks、更新文档和任务状态在同一事务完成。
- API session 在依赖退出时遇到未捕获异常会自动 rollback。

迁移命令：

~~~bash
alembic upgrade head
alembic check
~~~

## 6. Agent 适配层

实现文件：app/agent_runner.py、agent_new.py 以及 tools/* 的相关修改。

### 6.1 app/agent_runner.py

后端不直接在路由中导入 LangGraph，而是通过统一函数调用：

~~~python
run_search(task_id, mode="mock" | "real", progress_callback=callback)
~~~

主要职责：

- 从 search_requests 加载 SearchInput。
- mock 模式返回固定、可重复的三条仓库结果，方便测试和演示。
- real 模式才延迟导入 agent_new.graph，避免 API 启动时强制加载 GPU、LangChain、MCP 等重依赖。
- 将 Agent 原始输出转换为 SearchRunResult。
- build_mcp_command 从 MCP_SERVER_PYTHON 和 MCP_SERVER_SCRIPT 读取 MCP 启动命令，未配置时抛出明确的 PermanentAgentError。

### 6.2 agent_new.py

原实现会在模块导入时调用 getpass 请求 GitHub Token，这会导致 API 或 worker 启动被交互输入阻塞。现在只有直接执行 python agent_new.py 时才允许进入 CLI 密钥输入；作为后端模块导入时不会提示。

### 6.3 外部请求超时

- tools/activity_analysis.py 的 GitHub 提交、PR、最新提交请求统一设置 10 秒 timeout。
- tools/mcp_adapter.py 的 HTTP 请求统一设置 10 秒 timeout。
- tools/github2.py 的 httpx.AsyncClient 默认设置 10 秒 timeout。

### 6.4 代码质量 MCP

tools/code_quality.py 不再使用机器相关的硬编码 Python 和脚本路径，而是调用 build_mcp_command，可在本机、容器和 CI 中使用不同 MCP Server。

### 6.5 仓库缓存边界

app/repositories/repo_cache.py 新增异步 RepoCacheStore，使用 PostgreSQL INSERT ... ON CONFLICT DO UPDATE 保存仓库文档，并以 expires_at 判断过期。

原 tools/github2.py 中的 SQLite import-time 建表已移除，不再在导入模块时创建 github_cache.db。兼容旧 Agent 函数的 _LEGACY_CACHE 仍是进程内缓存；若要让真实 GitHub 抓取使用持久化缓存，应把抓取函数进一步接到 RepoCacheStore 或 Redis。

## 7. 认证、权限和安全边界

- JWT 使用配置中的 secret、算法和过期时间。
- require_user 是普通登录依赖。
- require_admin 检查 role == admin，否则返回 403 FORBIDDEN。
- 普通用户读取任务时会附加 Task.user_id == user.id 条件。
- 密码使用 Argon2 哈希。
- sanitize_error 会移除 Bearer Token、token、API key 和 password 的疑似值，并截断到 500 字符。
- Docker Compose 要求通过环境变量提供 JWT_SECRET 和 POSTGRES_PASSWORD，不把密钥写入 compose 文件。

生产环境应使用随机生成且长度不少于 32 字节的 JWT secret，并通过密钥管理系统注入。

## 8. 可观测性

实现文件：app/observability.py、app/api/health.py。

### 8.1 请求 ID 和日志

RequestIdMiddleware 优先复用请求头 X-Request-ID，没有时生成 UUID。请求结束时记录 request ID、事件名和路径，增加请求计数与耗时指标，并将 request ID 写回响应头。

### 8.2 Prometheus 指标

GET /metrics 暴露：

- http_requests_total
- http_request_duration_seconds
- task_success_total
- task_failure_total
- task_retry_total
- agent_duration_seconds
- external_errors_total
- cache_hits_total
- cache_misses_total
- running_tasks

### 8.3 健康检查

- GET /health/live：只表示 API 进程仍能响应。
- GET /health/ready：执行 PostgreSQL SELECT 1 和 Redis PING，任一依赖失败返回 503 NOT_READY。

## 9. Docker Compose 部署

实现文件：Dockerfile、docker-compose.yml、docker-entrypoint.sh、.dockerignore。

Compose 启动四个服务：

| 服务 | 作用 |
| --- | --- |
| postgres | PostgreSQL 16，保存业务数据 |
| redis | Redis 7，同时作为缓存、限流、锁和 Celery broker/backend |
| api | FastAPI HTTP 服务，启动时执行 Alembic 迁移 |
| worker | Celery worker，使用 solo pool 运行任务 |

依赖启动顺序由 healthcheck 保证：PostgreSQL 和 Redis 健康后 API 才启动，API 健康后 worker 才启动。

Dockerfile 使用 python:3.11-slim，创建非 root 用户 app。镜像会复制 app、Alembic、agent_new.py、tools 和 skills，避免真实 Agent 源码在构建上下文中遗漏。

启动：

~~~bash
copy .env.example .env
docker compose up --build
~~~

停止：

~~~bash
docker compose down
~~~

当前 Compose 默认 AGENT_MODE=mock。真实 Agent 还需要额外安装 LangGraph、LangChain、MCP、模型和相关凭证；这些重量级依赖没有放进后端基础依赖中。

## 10. 测试和压测

### 10.1 测试依赖和分类

pyproject.toml 定义 test 和 load 可选依赖：

~~~bash
pip install -e ".[test]"
pip install -e ".[load]"
~~~

测试分为默认快速套件和需要 PostgreSQL、Redis 的 integration 套件：

~~~bash
pytest -q
pytest -m integration -q
~~~

### 10.2 测试覆盖范围

| 文件 | 覆盖内容 |
| --- | --- |
| tests/test_auth.py | Argon2 哈希、JWT 创建和解析 |
| tests/test_auth_api.py | 注册、登录、重复用户、错误凭证 |
| tests/test_auth_errors.py | 未登录请求的统一错误 envelope |
| tests/test_agent_runner.py | Mock 稳定性、进度回调、导入不触发 getpass、非法模式 |
| tests/test_real_agent_adapter.py | MCP 路径、外部请求 timeout、仓库缓存异步边界 |
| tests/test_config.py | Settings 默认值和 broker URL |
| tests/test_search_api.py | 搜索参数、限流响应、位置头和任务投递 |
| tests/test_documents_api.py | 文档校验和创建响应 |
| tests/test_idempotency.py | 相同幂等键复用、不同请求冲突 |
| tests/test_redis_services.py | JSON 缓存、固定窗口限流、锁 token 校验 |
| tests/test_task_service.py | 条件状态转换和错误脱敏 |
| tests/test_tasks_api.py | 任务读取、结果和失败错误 |
| tests/test_worker_tasks.py | 搜索 worker 成功、失败和重试 |
| tests/test_document_worker.py | 文档切分、chunk 写入和重复执行保护 |
| tests/test_task_recovery.py | 老旧 pending 任务只恢复一次 |
| tests/test_health_api.py | live/ready 健康检查 |
| tests/test_observability.py | 请求 ID 和指标 |
| tests/integration/test_database_schema.py | 数据库约束和事务回滚 |
| tests/integration/test_permissions_and_transactions.py | 管理员跨用户读任务、事务语义 |
| tests/integration/test_search_flow.py | 注册、登录、提交搜索、worker 执行、轮询结果 |

tests/api_fixtures.py 提供 API 客户端、数据库依赖覆盖、假的 Celery 结果和限流 mock；tests/integration/conftest.py 提供 PostgreSQL 清理、异步 session、eager Celery 和轮询辅助函数；tests/conftest.py 让不带 -m integration 的快速测试自动跳过集成用例。

### 10.3 Locust

locustfile.py 定义 SearchUser：

1. 启动时登录已有账号。
2. 发送带随机 Idempotency-Key 的 POST /search。
3. 循环轮询 GET /tasks/{id}，直到 SUCCEEDED 或 FAILED。

示例：

~~~bash
set LOCUST_EMAIL=load-test@example.com
set LOCUST_PASSWORD=password-123
locust -f locustfile.py --headless -u 5 -r 1 -t 30s --host http://127.0.0.1:8000
~~~

该压测只代表 Mock Agent 路径的 API、数据库、Redis 和 Celery 吞吐，不代表真实 GitHub、LLM、GPU 或 MCP 的生产性能。

## 11. 配置项说明

配置类位于 app/config.py，环境变量会覆盖默认值。

| 环境变量 | 默认值/示例 | 作用 |
| --- | --- | --- |
| DATABASE_URL | postgresql+asyncpg://... | PostgreSQL 异步连接 |
| REDIS_URL | redis://localhost:6379/0 | Redis 缓存、限流和锁 |
| CELERY_BROKER_URL | 空，回退到 REDIS_URL | Celery broker/backend |
| JWT_SECRET | 开发默认值 | JWT 签名 secret，生产必须覆盖 |
| JWT_ALGORITHM | HS256 | JWT 算法 |
| JWT_EXPIRE_SECONDS | 1800 | JWT 有效期 |
| AGENT_MODE | mock | mock 或 real |
| RATE_LIMIT_PER_MINUTE | 10 | 每个用户每分钟搜索次数 |
| MCP_SERVER_SCRIPT | 空 | MCP server 脚本路径 |
| MCP_SERVER_PYTHON | 空 | 启动 MCP server 的 Python 路径 |
| POSTGRES_PASSWORD | .env 中设置 | Compose 数据库密码 |
| LOCUST_EMAIL | load-test@example.com | Locust 登录账号 |
| LOCUST_PASSWORD | password-123 | Locust 登录密码 |

## 12. 文件职责索引

下面的索引覆盖本次相对 main 新增或修改的文件。

### 12.1 根目录和基础设施

| 文件 | 职责 |
| --- | --- |
| .dockerignore | 排除虚拟环境、缓存、测试和文档，缩小 Docker 构建上下文 |
| .env.example | 数据库、Redis、JWT、Agent 和限流配置模板 |
| .gitattributes | 统一 shell 文件使用 LF 换行 |
| .gitignore | 忽略 .env、数据库文件、Python 缓存、虚拟环境和 worktree |
| Dockerfile | 构建非 root Python API/worker 镜像 |
| docker-compose.yml | 编排 API、worker、PostgreSQL 和 Redis |
| docker-entrypoint.sh | 根据 RUN_MIGRATIONS=1 执行迁移 |
| alembic.ini | Alembic 脚本位置、数据库默认地址和日志配置 |
| pyproject.toml | Python 包元数据、运行/测试/压测依赖和 pytest 配置 |
| README.md | 后端服务、本地测试、Compose 和 API 入口说明 |
| locustfile.py | 搜索压测用户和提交/轮询场景 |

### 12.2 应用核心

| 文件 | 职责 |
| --- | --- |
| app/__init__.py | 声明后端 Python 包 |
| app/main.py | 创建 FastAPI、注册中间件、异常处理器和路由 |
| app/config.py | Pydantic Settings 和缓存的 get_settings |
| app/db.py | SQLAlchemy 异步 engine、session factory 和数据库依赖 |
| app/models.py | 9 个 ORM 模型、约束、索引和 UTC 时间字段 |
| app/errors.py | ApiError 和统一 JSON 错误处理 |
| app/dependencies.py | require_user、require_admin 权限依赖 |
| app/auth.py | Argon2 密码、JWT 签发/解析和当前用户解析 |
| app/redis_client.py | 创建异步 Redis 客户端 |
| app/observability.py | 请求 ID 中间件、日志和 Prometheus 指标 |
| app/agent_runner.py | mock/real Agent 统一入口和结果标准化 |
| app/celery_app.py | Celery 实例、broker/backend 和超时配置 |
| app/tasks.py | 搜索/文档 Celery 任务、重试、失败状态和指标 |

### 12.3 HTTP API

| 文件 | 职责 |
| --- | --- |
| app/api/__init__.py | API 包声明 |
| app/api/auth.py | 注册和登录 |
| app/api/search.py | 搜索限流、幂等、任务创建和 Celery 投递 |
| app/api/documents.py | 文档创建和文档任务投递 |
| app/api/tasks.py | 任务查询、结果组装和资源归属控制 |
| app/api/health.py | live、ready 和 /metrics |

### 12.4 Pydantic Schema

| 文件 | 职责 |
| --- | --- |
| app/schemas/__init__.py | Schema 包声明 |
| app/schemas/auth.py | 注册、登录、公开用户和登录响应 |
| app/schemas/search.py | 搜索请求校验和 Agent config |
| app/schemas/documents.py | 文档输入校验和创建响应 |
| app/schemas/tasks.py | 创建任务、任务错误和详情响应 |
| app/schemas/agent.py | Agent 输入和标准化搜索结果 |

### 12.5 Service 层

| 文件 | 职责 |
| --- | --- |
| app/services/__init__.py | Service 包声明 |
| app/services/search_service.py | 搜索任务创建和幂等并发冲突恢复 |
| app/services/document_service.py | 文档 checksum、创建、切分、worker 和失败处理 |
| app/services/task_service.py | 状态转换、搜索执行、缓存、锁、结果持久化和脱敏 |
| app/services/idempotency_service.py | 请求规范化、哈希和幂等记录 |
| app/services/cache_service.py | Redis JSON get/set/delete 和缓存指标 |
| app/services/rate_limit_service.py | Redis Lua 固定窗口限流 |
| app/services/lock_service.py | token 校验的 Redis 分布式锁 |
| app/services/recovery_service.py | 扫描旧 pending 任务并按类型恢复 |

### 12.6 Repository 层

| 文件 | 职责 |
| --- | --- |
| app/repositories/__init__.py | Repository 包声明 |
| app/repositories/users.py | 按邮箱查询用户 |
| app/repositories/documents.py | 按用户和 checksum 查询文档 |
| app/repositories/repo_cache.py | PostgreSQL 仓库缓存异步读写和 upsert |

### 12.7 数据库迁移

| 文件 | 职责 |
| --- | --- |
| alembic/env.py | 获取异步数据库地址并绑定 Base.metadata |
| alembic/script.py.mako | Alembic revision 模板 |
| alembic/versions/0001_initial_schema.py | 创建全部业务表、约束和索引 |

### 12.8 Agent 和工具修改

| 文件 | 修改内容 |
| --- | --- |
| agent_new.py | 移除导入时 getpass，CLI 模式才请求 GitHub Token |
| tools/activity_analysis.py | GitHub 请求增加 10 秒 timeout |
| tools/mcp_adapter.py | MCP HTTP 请求增加 10 秒 timeout |
| tools/code_quality.py | 从环境配置 MCP Python/脚本路径，移除硬编码路径 |
| tools/github2.py | 移除 import-time SQLite 建表，增加 HTTP timeout，保留兼容进程内缓存 |

### 12.9 测试和测试夹具

| 文件 | 职责 |
| --- | --- |
| tests/__init__.py | 测试包声明 |
| tests/conftest.py | 默认跳过集成测试的 pytest 收集钩子 |
| tests/api_fixtures.py | API 客户端、数据库覆盖、假的 Celery 结果和限流 mock |
| tests/test_auth.py | 认证底层函数 |
| tests/test_auth_api.py | 注册/登录 API |
| tests/test_auth_errors.py | 未认证错误 envelope |
| tests/test_agent_runner.py | Agent runner 单元测试 |
| tests/test_real_agent_adapter.py | real Agent 配置和外部边界 |
| tests/test_config.py | Settings |
| tests/test_search_api.py | 搜索 API |
| tests/test_documents_api.py | 文档 API |
| tests/test_idempotency.py | 幂等行为 |
| tests/test_redis_services.py | Redis 服务 |
| tests/test_task_service.py | 状态机和错误脱敏 |
| tests/test_tasks_api.py | 任务查询 API |
| tests/test_worker_tasks.py | 搜索 worker |
| tests/test_document_worker.py | 文档 worker |
| tests/test_task_recovery.py | 任务恢复 |
| tests/test_health_api.py | 健康检查 |
| tests/test_observability.py | 请求 ID 和指标 |
| tests/integration/__init__.py | 集成测试包声明 |
| tests/integration/conftest.py | PostgreSQL、异步 session、eager Celery 和 API 集成夹具 |
| tests/integration/test_database_schema.py | 数据库约束和事务回滚 |
| tests/integration/test_permissions_and_transactions.py | 管理员权限和事务 |
| tests/integration/test_search_flow.py | 完整搜索链路 |

### 12.10 设计、计划和压测说明

| 文件 | 职责 |
| --- | --- |
| docs/backend-implementation-guide.md | 本文档，汇总全部实现逻辑、文件职责、运行方式和当前边界 |
| docs/superpowers/specs/2026-09-06-github-agent-backend-service-design.md | 后端设计规格、API、数据模型、可靠性和验收标准 |
| docs/superpowers/plans/2026-09-06-github-agent-backend-service-implementation.md | 分任务实现计划和测试步骤 |
| docs/benchmarking.md | Mock Agent 压测运行方式和指标记录要求 |

## 13. 当前实现边界和后续建议

这版改造已经覆盖后端岗位常见的核心能力，但以下内容仍是明确的后续工作：

1. recover_pending_tasks 已实现，但没有内置周期调度，需要接入 Celery Beat 或外部定时器。
2. 当前恢复器只扫描 PENDING + celery_task_id IS NULL，长期处于 RUNNING 的租约恢复还需要 heartbeat/lease 字段。
3. RepoCacheStore 已建立 PostgreSQL repository 边界，但旧 Agent 的 GitHub 抓取函数仍使用兼容性的进程内 _LEGACY_CACHE。
4. AGENT_MODE=real 需要安装 LangGraph、LangChain、MCP、模型和原 Agent 的完整依赖；后端基础依赖默认只保证 mock 服务。
5. 中间 progress callback 目前只在 worker 内存中收集；实时进度可增加事件表、Redis Pub/Sub 或 WebSocket/SSE。
6. Prometheus 指标已经暴露，但还没有附带 Grafana dashboard、告警规则和集中式日志采集配置。
7. 真实 GitHub/LLM 调用的重试分类仍可按网络错误、限流错误、鉴权错误和业务错误进一步细分。

这些边界不影响当前 mock 模式的 API、任务、数据库、Redis、测试和 Compose 验收链路，但在生产化真实 Agent 前应逐项补齐。
