# GitHub Agent Backend Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有 LangGraph GitHub 推荐 Agent 改造成具备 JWT 鉴权、PostgreSQL 持久化、Redis 可靠性能力、Celery 异步任务、文档入库、测试、监控和 Docker 交付能力的后端服务。

**Architecture:** FastAPI 负责 HTTP、鉴权和请求校验；PostgreSQL 保存用户、任务、结果、文档和仓库缓存；Redis 负责热点缓存、限流和任务锁，同时作为 Celery broker；Celery Worker 通过 `agent_runner` 调用现有 LangGraph。Web 搜索任务以数据库 `task_id` 为唯一业务引用，数据库是状态事实来源。

**Tech Stack:** Python 3.11, FastAPI, Pydantic Settings, SQLAlchemy 2.0 Async, asyncpg, Alembic, PostgreSQL, Redis, Celery, Argon2id, PyJWT, pytest, pytest-asyncio, httpx, Prometheus client, Locust, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-09-06-github-agent-backend-service-design.md`

## Global Constraints

- PostgreSQL 是任务和结果的事实来源；Celery 的消息状态不作为业务状态来源。
- 长任务通过任务 ID 传递，不把大段文档或完整 Agent 状态塞入消息队列。
- 首版使用 mock Agent，使测试和压测不依赖 GitHub、LLM 或 GPU。
- 首版只提供 `user` 和 `admin` 两个角色，不实现 Refresh Token、OAuth 或密码找回。
- 首版使用 Celery + Redis，暂不引入 Kafka、Kubernetes、pgvector 或 WebSocket。
- Docker 不阻塞前期功能开发，最后一个任务才添加 Docker Compose。
- 所有外部调用都必须有 timeout、错误分类和任务上下文日志。
- Web 搜索不使用跨进程 `MemorySaver`；CLI 的内存 checkpoint 与 Web 任务隔离。
- 所有时间使用 UTC；数据库主键使用 UUID；数据库访问使用 SQLAlchemy 2.0 Async + `asyncpg`。

## File Map

第一阶段创建以下边界，后续任务只在对应边界内扩展：

```text
pyproject.toml                 依赖、pytest、ruff 配置
.env.example                   开发配置模板
app/
  main.py                      FastAPI 应用工厂和路由注册
  config.py                    Settings 和环境变量
  db.py                        异步引擎、sessionmaker、事务依赖
  models.py                    PostgreSQL ORM 模型
  schemas/                     Pydantic 请求/响应模型
  auth.py                      Argon2id、JWT、当前用户依赖
  errors.py                    统一错误码和错误响应
  dependencies.py              用户、数据库、Redis 等依赖
  agent_runner.py              LangGraph/mock 适配层
  celery_app.py                Celery 应用配置
  tasks.py                     Celery 任务入口
  observability.py             request_id、结构化日志、Prometheus 指标
  repositories/                数据访问对象
  services/                    搜索、文档、缓存、限流、幂等服务
  api/                         auth、health、search、tasks、documents 路由
alembic/                       数据库迁移
tests/                         单元、API 和集成测试
locustfile.py                  Mock 模式压测
Dockerfile                     API/Worker 镜像
docker-compose.yml             API、Worker、PostgreSQL、Redis
```

---

### Task 1: 项目基础、配置和测试入口

**Files:**
- Create: `pyproject.toml`
- Create: `.env.example`
- Create: `app/__init__.py`
- Create: `app/config.py`
- Create: `tests/__init__.py`
- Create: `tests/test_config.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces `Settings` and `get_settings() -> Settings`.
- `Settings` 至少提供 `database_url`, `redis_url`, `celery_broker_url`, `jwt_secret`, `jwt_algorithm`, `jwt_expire_seconds`, `agent_mode`, `rate_limit_per_minute`, `mcp_server_script`, `mcp_server_python`。

- [ ] **Step 1: Write the failing configuration tests**

```python
def test_settings_has_safe_defaults(monkeypatch):
    monkeypatch.delenv("AGENT_MODE", raising=False)
    settings = get_settings()
    assert settings.agent_mode == "mock"
    assert settings.jwt_expire_seconds == 1800

def test_settings_reads_database_and_worker_urls(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    settings = get_settings()
    assert settings.database_url.endswith("/db")
    assert settings.redis_url.endswith("/0")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_config.py -q`
Expected: FAIL with `ModuleNotFoundError` or `NameError` because `app.config` is not defined.

- [ ] **Step 3: Implement configuration and dependency metadata**

```python
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "postgresql+asyncpg://github_agent:github_agent@localhost:5432/github_agent"
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str | None = None
    jwt_secret: str = "change-me-in-development"
    jwt_algorithm: str = "HS256"
    jwt_expire_seconds: int = 1800
    agent_mode: str = "mock"
    rate_limit_per_minute: int = 10
    mcp_server_script: str = ""
    mcp_server_python: str = ""

    @property
    def broker_url(self) -> str:
        return self.celery_broker_url or self.redis_url

@lru_cache
def get_settings() -> Settings:
    return Settings()
```

Add runtime dependencies for FastAPI, SQLAlchemy async, asyncpg, Alembic, Redis, Celery, PyJWT, `pwdlib[argon2]`, Prometheus client, and test/Locust tools. Do not add a second web framework.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_config.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml .env.example .gitignore app tests
git commit -m "chore: add backend project foundation"
```

### Task 2: PostgreSQL ORM、事务边界和 Alembic

**Files:**
- Create: `app/db.py`
- Create: `app/models.py`
- Create: `app/repositories/__init__.py`
- Create: `alembic.ini`
- Create: `alembic/env.py`
- Create: `alembic/versions/0001_initial_schema.py`
- Create: `tests/integration/__init__.py`
- Create: `tests/integration/test_database_schema.py`

**Interfaces:**
- Produces `engine`, `async_session_factory`, and `get_db() -> AsyncIterator[AsyncSession]`.
- Models expose `User`, `Task`, `SearchRequest`, `SearchResult`, `Document`, `DocumentChunk`, `RepoCache`, and `IdempotencyKey`.
- Task statuses are `PENDING`, `RUNNING`, `RETRYING`, `SUCCEEDED`, `FAILED`; task types are `SEARCH`, `DOCUMENT_INGEST`.
- The integration test module defines `db_session` and `user` fixtures against the configured test database.

- [ ] **Step 1: Write schema and transaction tests**

```python
@pytest.mark.integration
async def test_user_email_is_unique(db_session):
    db_session.add(User(email="same@example.com", password_hash="x", role="user"))
    await db_session.commit()
    db_session.add(User(email="same@example.com", password_hash="y", role="user"))
    with pytest.raises(IntegrityError):
        await db_session.commit()

@pytest.mark.integration
async def test_transaction_rolls_back_task_and_request(db_session, user):
    async with db_session.begin():
        task = Task(user_id=user.id, task_type="SEARCH", status="PENDING", progress="QUEUED")
        db_session.add(task)
        raise RuntimeError("rollback")
    result = await db_session.execute(select(func.count(Task.id)))
    assert result.scalar_one() == 0
```

The test module imports `select` and `func` from SQLAlchemy and `IntegrityError` from `sqlalchemy.exc`.

- [ ] **Step 2: Run the integration tests to verify they fail**

Run: `pytest -m integration tests/integration/test_database_schema.py -q`
Expected: FAIL because the async session, models, and migration are not present.

- [ ] **Step 3: Implement the async database layer and all schema constraints**

Implement the exact tables and indexes from the spec. Use `TIMESTAMP(timezone=True)`, UUID primary keys, JSONB for JSON fields, foreign keys with `ON DELETE CASCADE` only for document chunks, and a unique expression index for normalized user email. `get_db()` must yield one session per request and rollback on an uncaught exception.

```python
engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)

async def get_db() -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
```

Configure Alembic to import `Base.metadata`, generate the initial migration, and make `alembic upgrade head` create every table and index.

- [ ] **Step 4: Run migration and tests**

Run: `alembic upgrade head`; then `pytest -m integration tests/integration/test_database_schema.py -q`
Expected: migration succeeds and all schema/rollback tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/db.py app/models.py app/repositories alembic alembic.ini tests/integration
git commit -m "feat: add postgres models and migrations"
```

### Task 3: Argon2id、JWT 和认证 API

**Files:**
- Create: `app/schemas/auth.py`
- Create: `app/auth.py`
- Create: `app/repositories/users.py`
- Create: `app/api/auth.py`
- Create: `tests/test_auth.py`
- Create: `tests/test_auth_api.py`

**Interfaces:**
- `hash_password(password: str) -> str`
- `verify_password(password: str, password_hash: str) -> bool`
- `create_access_token(user_id: UUID, role: str) -> str`
- `decode_access_token(token: str) -> TokenClaims`
- `get_current_user(credentials: HTTPAuthorizationCredentials, session: AsyncSession) -> User`
- Routes: `POST /auth/register`, `POST /auth/login`.

- [ ] **Step 1: Write failing password, token, and API tests**

```python
def test_password_hash_is_not_plaintext():
    hashed = hash_password("correct horse battery staple")
    assert hashed != "correct horse battery staple"
    assert verify_password("correct horse battery staple", hashed)
    assert not verify_password("wrong", hashed)

async def test_register_login_and_duplicate_email(client):
    response = await client.post("/auth/register", json={"email": "A@Example.com", "password": "password-123"})
    assert response.status_code == 201
    assert response.json()["email"] == "a@example.com"
    assert (await client.post("/auth/register", json={"email": "a@example.com", "password": "password-123"})).status_code == 409
    login = await client.post("/auth/login", json={"email": "a@example.com", "password": "password-123"})
    assert login.status_code == 200
    assert login.json()["token_type"] == "bearer"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_auth.py tests/test_auth_api.py -q`
Expected: FAIL because auth functions and routes are absent.

- [ ] **Step 3: Implement Argon2id and JWT authentication**

Use `pwdlib` Argon2id hashing and PyJWT. Normalize email to lowercase, reject passwords shorter than 8 characters, return `409 USER_EXISTS` for duplicate email, return `401 INVALID_CREDENTIALS` for login failure, and never serialize `password_hash`.

```python
class TokenClaims(BaseModel):
    sub: UUID
    role: str
    iat: int
    exp: int
```

Register creates a `User` in a transaction. Login returns `access_token`, `token_type`, `expires_in`, and public user data exactly as specified.

```python
password_hash = PasswordHash.recommended()
bearer_scheme = HTTPBearer(auto_error=False)

def hash_password(password: str) -> str:
    return password_hash.hash(password)

def verify_password(password: str, encoded: str) -> bool:
    return password_hash.verify(password, encoded)

async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer_scheme)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    claims = decode_access_token(credentials.credentials)
    user = await session.get(User, claims.sub)
    if user is None:
        raise ApiError(401, "INVALID_TOKEN", "Invalid access token")
    return user
```

The auth API tests build a minimal `FastAPI()` instance and include `auth_router` directly; Task 4 later registers the same router in the production application factory.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_auth.py tests/test_auth_api.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/auth.py app/schemas/auth.py app/repositories/users.py app/api/auth.py tests/test_auth.py tests/test_auth_api.py
git commit -m "feat: add jwt authentication"
```

### Task 4: FastAPI 应用工厂、统一错误和健康检查

**Files:**
- Create: `app/errors.py`
- Create: `app/dependencies.py`
- Create: `app/api/__init__.py`
- Create: `app/api/health.py`
- Create: `app/main.py`
- Create: `app/observability.py`
- Create: `tests/test_health_api.py`

**Interfaces:**
- `create_app() -> FastAPI`
- `GET /health/live`
- `GET /health/ready`
- `require_user()` and `require_admin()` dependencies.
- Error envelope: `{"error": {"code": str, "message": str}, "request_id": str}`.
- Produces `ApiError`, `RequestIdMiddleware`, `api_error_handler`, and `validation_error_handler` for use by later routers.

- [ ] **Step 1: Write failing app and health tests**

```python
async def test_liveness_does_not_require_dependencies(client):
    response = await client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

async def test_readiness_reports_dependency_failure(client, monkeypatch):
    async def fake_failure():
        raise ConnectionError("redis unavailable")
    monkeypatch.setattr("app.api.health.check_redis", fake_failure)
    response = await client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "NOT_READY"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_health_api.py -q`
Expected: FAIL because the application factory and routes are absent.

- [ ] **Step 3: Implement the application factory and error handlers**

Register auth and health routers, install request ID middleware, map Pydantic validation to `VALIDATION_ERROR`, and map uncaught exceptions to `INTERNAL_ERROR` without returning stack traces. `GET /health/live` must not touch PostgreSQL or Redis. `GET /health/ready` performs `SELECT 1` and `PING` with a short timeout.

```python
def create_app() -> FastAPI:
    app = FastAPI(title="GitHub Agent API")
    app.add_middleware(RequestIdMiddleware)
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.include_router(auth_router)
    app.include_router(health_router)
    return app

app = create_app()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_health_api.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/main.py app/errors.py app/dependencies.py app/api tests/test_health_api.py
git commit -m "feat: add fastapi app and health checks"
```

### Task 5: Agent Runner 和确定性 Mock 模式

**Files:**
- Create: `app/schemas/agent.py`
- Create: `app/agent_runner.py`
- Create: `tests/test_agent_runner.py`
- Modify: `agent_new.py`

**Interfaces:**
- `SearchRunResult` with `final_results`, `repositories`, `filtered_candidates`, `search_history`, and `metadata`.
- `run_search(task_id: UUID, *, mode: Literal["mock", "real"], progress_callback: Callable[[str], None] | None = None) -> SearchRunResult`.
- `load_search_input(task_id: UUID) -> SearchInput`, `run_mock_search(input: SearchInput, progress_callback=None) -> SearchRunResult`, and `PermanentAgentError` are internal runner interfaces.

- [ ] **Step 1: Write failing runner tests**

```python
def test_mock_runner_is_deterministic(task_id):
    first = run_search(task_id, mode="mock")
    second = run_search(task_id, mode="mock")
    assert first == second
    assert first.final_results

def test_runner_does_not_prompt_for_credentials_on_import():
    importlib.import_module("app.agent_runner")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_agent_runner.py -q`
Expected: FAIL because the runner does not exist and importing the current Agent can invoke `getpass`.

- [ ] **Step 3: Implement the adapter and remove import side effects**

The mock runner returns three fixed repository records and emits progress values in order: `QUERY_ANALYZED`, `REPOS_FETCHED`, `RERANKING`, `REPORT_GENERATING`, `DONE`. Real mode lazily imports `agent_new.graph`, invokes it with the task query, and converts the graph output to `SearchRunResult`.

```python
def run_search(task_id: UUID, *, mode: Literal["mock", "real"], progress_callback=None) -> SearchRunResult:
    task_input = load_search_input(task_id)
    if mode == "mock":
        return run_mock_search(task_input, progress_callback)
    if mode != "real":
        raise PermanentAgentError(f"Unsupported agent mode: {mode}")
    from agent_new import graph
    raw = graph.invoke(
        {"user_query": task_input.query},
        config={"configurable": {"thread_id": str(task_id), **task_input.config}},
    )
    return SearchRunResult.model_validate(raw)
```

Move interactive GitHub token prompting behind the CLI `__main__` path. Importing `agent_new` from an API or Worker must never call `getpass`, read stdin, or print configuration diagnostics.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_agent_runner.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/agent_runner.py app/schemas/agent.py agent_new.py tests/test_agent_runner.py
git commit -m "refactor: isolate agent runner from cli"
```

### Task 6: Celery 应用、任务状态机和搜索 Worker

**Files:**
- Create: `app/celery_app.py`
- Create: `app/tasks.py`
- Create: `app/services/task_service.py`
- Create: `tests/test_task_service.py`
- Create: `tests/test_worker_tasks.py`

**Interfaces:**
- `celery_app: Celery`
- `run_search_task(task_id: str) -> None` Celery task.
- `transition_task(session, task_id: UUID, from_statuses: set[str], to_status: str, progress: str | None = None) -> Task`.
- `TaskService.create_search_task(session, user_id: UUID, query: str, config: dict) -> Task`.
- `execute_search_task(task_id: UUID) -> None`, `mark_retrying(task_id: UUID, retry_count: int) -> None`, `mark_failed(task_id: UUID, error_message: str) -> None`, and `sanitize_error(exc: Exception) -> str`.
- `TransientAgentError` identifies retryable GitHub/LLM/network failures; `enqueue_search_after_commit(task_id: UUID) -> None` publishes only after the database transaction commits.
- Worker tests define `create_pending_task`, `load_task`, `eager_celery`, and `task_id` fixtures/helpers in the same test module.

- [ ] **Step 1: Write failing state and Worker tests**

```python
async def test_task_transitions_are_conditional(db_session):
    task = await create_pending_task(db_session)
    await transition_task(db_session, task.id, {"PENDING"}, "RUNNING", "QUERY_ANALYZED")
    with pytest.raises(InvalidTaskTransition):
        await transition_task(db_session, task.id, {"PENDING"}, "RUNNING")

def test_worker_mock_mode_persists_success(eager_celery, task_id):
    run_search_task.delay(str(task_id)).get()
    task = load_task(task_id)
    assert task.status == "SUCCEEDED"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_task_service.py tests/test_worker_tasks.py -q`
Expected: FAIL because Celery configuration and task transitions are absent.

- [ ] **Step 3: Implement the Worker and state machine**

Configure Celery with `broker=get_settings().broker_url`, JSON serialization, UTC, and a bounded task time limit. The Worker receives only `task_id`, opens its own database session, conditionally claims `PENDING` tasks, calls `run_search`, writes progress updates, and in one transaction inserts `SearchResult` plus changes the task to `SUCCEEDED`. A terminal task is a no-op when redelivered.

Use Celery autoretry only for a typed `TransientAgentError`, with maximum 3 retries and exponential backoff. Before raising `self.retry`, set the database task to `RETRYING` and increment `retry_count`.

```python
@celery_app.task(bind=True, acks_late=True, max_retries=3)
def run_search_task(self, task_id: str) -> None:
    try:
        asyncio.run(execute_search_task(UUID(task_id)))
    except TransientAgentError as exc:
        asyncio.run(mark_retrying(UUID(task_id), self.request.retries + 1))
        raise self.retry(exc=exc, countdown=2 ** self.request.retries)
    except Exception as exc:
        asyncio.run(mark_failed(UUID(task_id), sanitize_error(exc)))
        raise
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_task_service.py tests/test_worker_tasks.py -q`
Expected: PASS in eager/mock mode.

- [ ] **Step 5: Commit**

```bash
git add app/celery_app.py app/tasks.py app/services/task_service.py tests/test_task_service.py tests/test_worker_tasks.py
git commit -m "feat: add celery search worker and task state machine"
```

### Task 7: 搜索提交、任务查询和资源权限

**Files:**
- Create: `app/schemas/search.py`
- Create: `app/schemas/tasks.py`
- Create: `app/api/search.py`
- Create: `app/api/tasks.py`
- Create: `app/services/search_service.py`
- Create: `tests/test_search_api.py`
- Create: `tests/test_tasks_api.py`
- Modify: `app/main.py`

**Interfaces:**
- `POST /search` returns `202` with `{task_id, status, created_at}` and `Location` header.
- `GET /tasks/{task_id}` returns status, progress, retry count, result, error, and timestamps.
- `SearchRequestCreate` validates `query: str`, `max_results: int | None`, `per_page: int | None`, and `include_code_quality: bool`.
- `TaskCreated` serializes `task_id: UUID`, `status: str`, and `created_at: datetime`; `TaskView` adds progress, retry count, result, error, and timestamps.
- `SearchService.submit(user_id: UUID, request: SearchRequestCreate, idempotency_key: str | None = None) -> Task`.
- `TaskService.get_for_user(task_id: UUID, user: User) -> TaskView`.

- [ ] **Step 1: Write failing endpoint and permission tests**

```python
async def test_search_returns_202_and_task_location(auth_client):
    response = await auth_client.post("/search", json={"query": "python inference"})
    assert response.status_code == 202
    task_id = response.json()["task_id"]
    assert response.headers["location"] == f"/tasks/{task_id}"

async def test_user_cannot_read_another_users_task(client, user_a_token, user_b_task_id):
    response = await client.get(
        f"/tasks/{user_b_task_id}",
        headers={"Authorization": f"Bearer {user_a_token}"},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "TASK_NOT_FOUND"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_search_api.py tests/test_tasks_api.py -q`
Expected: FAIL because search and task routers are not registered.

- [ ] **Step 3: Implement request validation, task creation, and ownership checks**

Validate `query` length 1-2000, clamp `max_results` and `per_page` to configured limits, and reject unknown oversized configuration values. Create the task and `search_requests` in one database transaction, commit, then enqueue `run_search_task.delay(str(task.id))`. If enqueue fails, leave the task `PENDING` for the recovery scanner and return the created task only when the database transaction succeeded.

Return result data only for `SUCCEEDED`; return a structured error for `FAILED`; do not expose `error_message` from another user’s task. Admin users may query any task.

```python
@router.post("/search", status_code=202, response_model=TaskCreated)
async def submit_search(
    payload: SearchRequestCreate,
    response: Response,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> TaskCreated:
    task = await SearchService(session).submit(user.id, payload, idempotency_key)
    response.headers["Location"] = f"/tasks/{task.id}"
    enqueue_search_after_commit(task.id)
    return TaskCreated.model_validate(task)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_search_api.py tests/test_tasks_api.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/api/search.py app/api/tasks.py app/schemas/search.py app/schemas/tasks.py app/services/search_service.py app/main.py tests/test_search_api.py tests/test_tasks_api.py
git commit -m "feat: add search and task APIs"
```

### Task 8: Redis 客户端、缓存、限流和任务锁

**Files:**
- Create: `app/redis_client.py`
- Create: `app/services/cache_service.py`
- Create: `app/services/rate_limit_service.py`
- Create: `app/services/lock_service.py`
- Create: `tests/test_redis_services.py`

**Interfaces:**
- `get_redis() -> redis.asyncio.Redis`.
- `CacheService.get_json(key)`, `set_json(key, value, ttl)`, `delete(key)`.
- `RateLimitService.check(scope: str, limit: int, window_seconds: int) -> RateLimitDecision`.
- `LockService.acquire(key, ttl_seconds) -> LockLease`; lease release verifies its token.

- [ ] **Step 1: Write failing Redis behavior tests**

```python
async def test_rate_limit_allows_limit_then_rejects(fake_redis):
    limiter = RateLimitService(fake_redis)
    assert (await limiter.check("user:1", 2, 60)).allowed
    assert (await limiter.check("user:1", 2, 60)).allowed
    decision = await limiter.check("user:1", 2, 60)
    assert not decision.allowed
    assert decision.retry_after > 0

async def test_lock_release_does_not_delete_another_owner(fake_redis):
    first = await LockService(fake_redis).acquire("search:x", 30)
    second = await LockService(fake_redis).acquire("search:x", 30)
    assert first is not None and second is None
    await first.release()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_redis_services.py -q`
Expected: FAIL because the Redis services do not exist.

- [ ] **Step 3: Implement Redis services**

Use `redis.asyncio`. Rate limiting must use one Lua script that performs `INCR` and `EXPIRE` atomically and returns current count and TTL. Use keys `rl:search:user:{user_id}`. Cache keys must include `search:v1:{request_hash}:{agent_version}`. Lock acquisition uses a random token; release uses a compare-and-delete Lua script.

Redis failures must be logged and degraded: rate-limit failures use a configured fail-open policy for this interview demo, cache failures bypass the cache, and database conditional updates remain the duplicate-execution guard.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_redis_services.py -q`
Expected: PASS with fakeredis or a test Redis instance.

- [ ] **Step 5: Commit**

```bash
git add app/redis_client.py app/services/cache_service.py app/services/rate_limit_service.py app/services/lock_service.py tests/test_redis_services.py
git commit -m "feat: add redis cache rate limit and locks"
```

### Task 9: 幂等提交、缓存命中和任务恢复

**Files:**
- Create: `app/services/idempotency_service.py`
- Create: `app/services/recovery_service.py`
- Create: `tests/test_idempotency.py`
- Create: `tests/test_task_recovery.py`
- Modify: `app/services/search_service.py`
- Modify: `app/tasks.py`

**Interfaces:**
- `IdempotencyService.find_or_reserve(user_id, endpoint, key, request_hash) -> IdempotencyRecord | None`.
- `normalize_search_request(request) -> str` and `hash_request(value) -> str`.
- `recover_pending_tasks() -> int`.

- [ ] **Step 1: Write failing idempotency and recovery tests**

```python
async def test_same_key_returns_same_task(auth_client):
    headers = {"Idempotency-Key": "fixed-key"}
    first = await auth_client.post("/search", headers=headers, json={"query": "python"})
    second = await auth_client.post("/search", headers=headers, json={"query": "python"})
    assert first.json()["task_id"] == second.json()["task_id"]

async def test_same_key_with_different_payload_is_conflict(auth_client):
    headers = {"Idempotency-Key": "fixed-key-2"}
    await auth_client.post("/search", headers=headers, json={"query": "python"})
    response = await auth_client.post("/search", headers=headers, json={"query": "rust"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_idempotency.py tests/test_task_recovery.py -q`
Expected: FAIL because idempotency storage and recovery scanning are absent.

- [ ] **Step 3: Implement the transactional idempotency flow**

Within the same PostgreSQL transaction as task creation, lock the `(user_id, endpoint, key)` row. Return the existing task for the same request hash; return `409` for a different hash. Expire keys after 24 hours. On cache hit, create the current user’s own task and result row, then mark it `SUCCEEDED`; never return another user’s task ID.

Implement `recover_pending_tasks()` to find `PENDING` tasks older than 60 seconds with no `celery_task_id`, enqueue them, and update `celery_task_id`. Invoke it from a Celery beat schedule or an explicitly runnable recovery command; the command must be safe to run repeatedly.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_idempotency.py tests/test_task_recovery.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/idempotency_service.py app/services/recovery_service.py app/services/search_service.py app/tasks.py tests/test_idempotency.py tests/test_task_recovery.py
git commit -m "feat: add idempotency cache hits and task recovery"
```

### Task 10: 文档入库、去重和切片 Worker

**Files:**
- Create: `app/schemas/documents.py`
- Create: `app/api/documents.py`
- Create: `app/repositories/documents.py`
- Create: `app/services/document_service.py`
- Create: `tests/test_documents_api.py`
- Create: `tests/test_document_worker.py`
- Modify: `app/tasks.py`
- Modify: `app/main.py`

**Interfaces:**
- `POST /documents` returns `{document_id, task_id, status}` with `202`.
- `DocumentService.create(user_id, title, content, metadata, idempotency_key=None) -> DocumentSubmission`.
- `process_document_task(task_id: str) -> None` Celery task.
- `split_document(content: str, chunk_size: int = 1000, overlap: int = 100) -> list[str]`.

- [ ] **Step 1: Write failing document tests**

```python
def test_split_document_has_stable_overlap():
    chunks = split_document("a" * 2500, chunk_size=1000, overlap=100)
    assert [len(chunk) for chunk in chunks] == [1000, 1000, 700]
    assert chunks[0][-100:] == chunks[1][:100]

async def test_duplicate_document_is_not_reprocessed(auth_client):
    payload = {"title": "spec", "content": "same content", "metadata": {}}
    first = await auth_client.post("/documents", json=payload)
    second = await auth_client.post("/documents", json=payload)
    assert first.json()["document_id"] == second.json()["document_id"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_documents_api.py tests/test_document_worker.py -q`
Expected: FAIL because document routes, checksum handling, and Worker processing are absent.

- [ ] **Step 3: Implement document creation and processing**

Validate title length, content length, and metadata size. Compute `SHA-256(user_id + "\\0" + content)`. Enforce `UNIQUE(user_id, checksum)` and return the existing document/task for duplicates. Create `Document(PENDING)` and `Task(DOCUMENT_INGEST)` in one transaction. The Worker writes all chunks and changes the document/task to `READY`/`SUCCEEDED` in one transaction; on failure, both become `FAILED`.

首版不把 `document_chunks` 接入 GitHub 推荐召回。文档只能由所属用户访问，Worker 消息只传 task ID。

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_documents_api.py tests/test_document_worker.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/schemas/documents.py app/api/documents.py app/repositories/documents.py app/services/document_service.py app/tasks.py app/main.py tests/test_documents_api.py tests/test_document_worker.py
git commit -m "feat: add document ingestion endpoint"
```

### Task 11: 真实 LangGraph 适配、MCP 配置化和外部 HTTP 超时

**Files:**
- Create: `tests/test_real_agent_adapter.py`
- Create: `app/repositories/repo_cache.py`
- Modify: `app/agent_runner.py`
- Modify: `agent_new.py`
- Modify: `tools/code_quality.py`
- Modify: `tools/github2.py`
- Modify: `tools/activity_analysis.py`
- Modify: `tools/mcp_adapter.py`

**Interfaces:**
- Real Agent mode is selected by calling `run_search(task_id, mode="real")`.
- `build_mcp_command() -> list[str]` returns `[MCP_SERVER_PYTHON, MCP_SERVER_SCRIPT]`.
- MCP paths read `MCP_SERVER_SCRIPT` and `MCP_SERVER_PYTHON`.
- GitHub and LLM clients use configured connect/read timeout values.
- Repository cache access is exposed through an async `RepoCacheStore` boundary instead of module-import SQLite initialization.
- `RepoCacheStore.get(repo_name: str) -> Awaitable[str | None]` and `RepoCacheStore.save(repo_name: str, combined_doc: str, expires_at: datetime) -> Awaitable[None]` are the cache boundary methods.

- [ ] **Step 1: Write failing adapter and timeout tests**

```python
def test_real_mode_uses_environment_mcp_paths(monkeypatch):
    monkeypatch.setenv("MCP_SERVER_SCRIPT", "C:/agent/mcp_server.py")
    monkeypatch.setenv("MCP_SERVER_PYTHON", "C:/venv/python.exe")
    assert build_mcp_command() == ["C:/venv/python.exe", "C:/agent/mcp_server.py"]

def test_github_requests_define_timeout():
    source = Path("tools/activity_analysis.py").read_text(encoding="utf-8")
    assert "timeout=" in source
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_real_agent_adapter.py -q`
Expected: FAIL because current code has hardcoded MCP paths and import-time SQLite initialization.

- [ ] **Step 3: Implement the compatibility boundary**

Remove `getpass` and diagnostic `print` calls from import paths. Keep interactive token input only in CLI code. Replace hardcoded MCP constants with Settings values and return a clear configuration error when real mode lacks required secrets.

Add timeout to every `requests`/`httpx` call in the touched modules. Preserve the existing LangGraph nodes and result fields. Refactor `tools/github2.py` so `RepoCacheStore.get/save` are passed through its async fetch pipeline; no `init_db()` call is allowed at import time. Implement a PostgreSQL-backed store using the existing async session factory, with Redis as the optional hot-cache layer from Task 8.

- [ ] **Step 4: Run tests and a mock import smoke test**

Run: `pytest tests/test_real_agent_adapter.py tests/test_agent_runner.py -q`; then `python -c "import app.main; print('ok')"`
Expected: PASS and the import command exits without prompting or loading GPU models.

- [ ] **Step 5: Commit**

```bash
git add app/agent_runner.py agent_new.py tools/code_quality.py tools/github2.py tools/activity_analysis.py tools/mcp_adapter.py tests/test_real_agent_adapter.py
git commit -m "refactor: configure real agent integrations"
```

### Task 12: 结构化日志、request/task ID 和 Prometheus 指标

**Files:**
- Modify: `app/observability.py`
- Modify: `app/main.py`
- Modify: `app/tasks.py`
- Modify: `app/api/health.py`
- Create: `tests/test_observability.py`

**Interfaces:**
- `request_id` middleware adds/propagates `X-Request-ID`.
- `GET /metrics` returns Prometheus text format.
- Metrics include API request count/latency, task success/failure/retry, Agent duration, external errors, cache hit/miss, and running tasks.

- [ ] **Step 1: Write failing observability tests**

```python
async def test_request_id_is_returned(client):
    response = await client.get("/health/live", headers={"X-Request-ID": "req-123"})
    assert response.headers["X-Request-ID"] == "req-123"

async def test_metrics_endpoint_contains_http_counter(client):
    response = await client.get("/metrics")
    assert response.status_code == 200
    assert "http_requests_total" in response.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_observability.py -q`
Expected: FAIL because middleware, metrics, and `/metrics` are absent.

- [ ] **Step 3: Implement logs and metrics**

Use standard logging with a JSON formatter or a small structured adapter. Every request log includes `request_id`; every Worker log includes `task_id` and `user_id` when available. Add counters/histograms using `prometheus_client`; do not log JWTs, API keys, passwords, or complete document content. Wrap task execution and external calls with timers and error counters.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_observability.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/observability.py app/main.py app/tasks.py app/api/health.py tests/test_observability.py
git commit -m "feat: add structured logs and prometheus metrics"
```

### Task 13: 测试夹具、端到端闭环和集成测试命令

**Files:**
- Create: `tests/conftest.py`
- Create: `tests/integration/test_search_flow.py`
- Create: `tests/integration/test_permissions_and_transactions.py`
- Modify: `pyproject.toml`
- Modify: `README.md`

**Interfaces:**
- `pytest -q` runs fast tests without real GitHub, LLM, GPU, Celery broker, or MCP.
- `pytest -m integration -q` runs PostgreSQL/Redis/Celery eager integration tests.
- `AGENT_MODE=mock` is the default test mode.

- [ ] **Step 1: Write the end-to-end acceptance tests**

```python
async def test_register_search_poll_and_read_result(client, eager_worker):
    user = await register_and_login(client)
    submit = await client.post("/search", headers=user.headers, json={"query": "python"})
    assert submit.status_code == 202
    task_id = submit.json()["task_id"]
    result = await poll_until_terminal(client, task_id, user.headers)
    assert result["status"] == "SUCCEEDED"
    assert result["result"]["final_results"]
```

- [ ] **Step 2: Run the acceptance tests to verify they fail**

Run: `pytest tests/integration/test_search_flow.py -q`
Expected: FAIL until all previous service boundaries are wired into one app and the test fixtures exist.

- [ ] **Step 3: Implement reusable fixtures and test markers**

Provide app dependency overrides, a fake Agent, eager Celery configuration, database cleanup per test, fakeredis, JWT helper, and a `poll_until_terminal` helper with a 5-second test timeout. Register the `integration` marker and configure `asyncio_mode = "auto"`.

- [ ] **Step 4: Run both test tiers**

Run: `pytest -q`; then `pytest -m integration -q` with `DATABASE_URL`, `REDIS_URL`, and PostgreSQL/Redis services available.
Expected: fast suite passes without external model/API calls; integration suite passes with the declared dependencies.

- [ ] **Step 5: Commit**

```bash
git add tests pyproject.toml README.md
git commit -m "test: add backend acceptance and integration fixtures"
```

### Task 14: Locust Mock 模式压测

**Files:**
- Create: `locustfile.py`
- Create: `docs/benchmarking.md`

**Interfaces:**
- Locust user flow: login -> `POST /search` -> poll `GET /tasks/{id}` -> observe terminal result.
- Configuration: `LOCUST_BASE_URL`, `LOCUST_EMAIL`, `LOCUST_PASSWORD`, `AGENT_MODE=mock`.

- [ ] **Step 1: Write a smoke command and expected benchmark assertions**

```text
locust -f locustfile.py --headless -u 5 -r 1 -t 30s --host http://127.0.0.1:8000
```

The benchmark document must record request count, failure count, average latency, P95 latency, and completed task count. It must explicitly label results as Mock Agent results.

- [ ] **Step 2: Run the smoke command to verify the script fails**

Run: `locust -f locustfile.py --headless -u 1 -r 1 -t 5s --host http://127.0.0.1:8000`
Expected: FAIL because `locustfile.py` and the running service are not present.

- [ ] **Step 3: Implement the Locust workflow**

Create one `HttpUser` that logs in during `on_start`, submits a bounded query with an `Idempotency-Key`, polls at 200 ms intervals, stops after 30 seconds, and marks non-2xx responses as failures. Do not call real GitHub or LLM services from the load test.

- [ ] **Step 4: Run the smoke benchmark**

Run the command from Step 1 against a local mock-mode API and save the summary in `docs/benchmarking.md`.
Expected: the flow creates tasks and reaches terminal states without API errors.

- [ ] **Step 5: Commit**

```bash
git add locustfile.py docs/benchmarking.md
git commit -m "test: add locust search workload"
```

### Task 15: Docker Compose 交付

**Files:**
- Create: `Dockerfile`
- Create: `docker-compose.yml`
- Create: `docker-entrypoint.sh`
- Modify: `.env.example`
- Modify: `README.md`

**Interfaces:**
- `docker compose up --build` starts `api`, `worker`, `postgres`, and `redis`.
- API container runs migrations before starting Uvicorn.
- Worker container runs Celery against the same code/config.

- [ ] **Step 1: Write the deployment smoke checklist**

```text
docker compose up --build
curl http://127.0.0.1:8000/health/live
curl http://127.0.0.1:8000/health/ready
pytest -q
```

- [ ] **Step 2: Run the checklist to verify it fails**

Run: `docker compose config`
Expected: FAIL because Compose files and service definitions are absent.

- [ ] **Step 3: Implement the Compose services**

Use a non-root Python image, install the locked project dependencies, mount no source code in the production-like service, and pass configuration through environment variables. Add health checks for PostgreSQL and Redis, API/Worker dependency ordering, a PostgreSQL volume, a Redis volume if persistence is enabled, and `alembic upgrade head` in `docker-entrypoint.sh`. Do not place secrets in the image or Compose file.

- [ ] **Step 4: Run the deployment smoke test**

Run: `docker compose config`; then `docker compose up --build -d`; then the three commands from Step 1; finally `docker compose down`.
Expected: all services become healthy, the API responds, the mock search flow works, and shutdown preserves the database volume.

- [ ] **Step 5: Commit**

```bash
git add Dockerfile docker-compose.yml docker-entrypoint.sh .env.example README.md
git commit -m "chore: add docker compose deployment"
```

## Spec Coverage Map

| Spec requirement | Implemented by |
|---|---|
| FastAPI app, API contracts, error envelope | Tasks 1, 4, 7, 10 |
| JWT, Argon2id, user/admin permissions | Task 3 and Task 7 |
| PostgreSQL tables, indexes, transactions, migrations | Task 2 and Tasks 3, 6, 9, 10 |
| Celery background tasks and task state machine | Task 6 and Task 7 |
| Redis cache, rate limit, lock | Task 8 and Task 9 |
| Idempotency and task recovery | Task 9 |
| Document deduplication and chunks | Task 10 |
| Existing Agent integration and MCP configuration | Tasks 5 and 11 |
| Retry, timeout, partial failure isolation | Tasks 6 and 11 |
| Structured logs, health, Prometheus metrics | Tasks 4 and 12 |
| pytest fast/integration suites | Task 13 plus tests in every preceding task |
| Locust workload and benchmark notes | Task 14 |
| Docker Compose delivery | Task 15 |

## Execution Notes

- Execute tasks in order; each task ends with a focused test run and a commit.
- Use `AGENT_MODE=mock` until Task 11 has passed its real-mode adapter tests.
- Before execution, create an isolated worktree with `using-git-worktrees`; do not implement this plan directly on `main`.
- When a task exposes a design contradiction, stop and revisit the spec instead of silently changing API or schema contracts.
- After all tasks pass, use `verification-before-completion` and then `finishing-a-development-branch` before merging or opening a PR.
