"""Create the initial service schema.

Revision ID: 0001_initial_schema
Revises:
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0001_initial_schema"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    jsonb = postgresql.JSONB()

    op.create_table(
        "users",
        sa.Column("id", uuid, primary_key=True, nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.String(length=20), server_default="user", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("role IN ('user', 'admin')", name="ck_users_role"),
    )
    op.create_index("uq_users_email_lower", "users", [sa.text("lower(email)")], unique=True)

    op.create_table(
        "tasks",
        sa.Column("id", uuid, primary_key=True, nullable=False),
        sa.Column("user_id", uuid, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("task_type", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="PENDING", nullable=False),
        sa.Column("progress", sa.String(length=64), server_default="QUEUED", nullable=False),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("celery_task_id", sa.String(length=255), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("task_type IN ('SEARCH', 'DOCUMENT_INGEST')", name="ck_tasks_task_type"),
        sa.CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'RETRYING', 'SUCCEEDED', 'FAILED')",
            name="ck_tasks_status",
        ),
    )
    op.create_index("ix_tasks_user_created_at", "tasks", ["user_id", sa.text("created_at DESC")])
    op.create_index("ix_tasks_status_updated_at", "tasks", ["status", "updated_at"])

    op.create_table(
        "search_requests",
        sa.Column("task_id", uuid, sa.ForeignKey("tasks.id"), primary_key=True),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("config", jsonb, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    op.create_table(
        "search_results",
        sa.Column("task_id", uuid, sa.ForeignKey("tasks.id"), primary_key=True),
        sa.Column("final_results", sa.Text(), nullable=False),
        sa.Column("repositories_json", jsonb, server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("filtered_candidates_json", jsonb, server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("search_history_json", jsonb, server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("metadata_json", jsonb, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    op.create_table(
        "documents",
        sa.Column("id", uuid, primary_key=True, nullable=False),
        sa.Column("user_id", uuid, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata", jsonb, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="PENDING", nullable=False),
        sa.Column("ingest_task_id", uuid, sa.ForeignKey("tasks.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("user_id", "checksum", name="uq_documents_user_checksum"),
    )
    op.create_index("ix_documents_user_created_at", "documents", ["user_id", sa.text("created_at DESC")])

    op.create_table(
        "document_chunks",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("document_id", uuid, sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata", jsonb, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("document_id", "chunk_index", name="uq_document_chunks_document_index"),
    )

    op.create_table(
        "repo_cache",
        sa.Column("repo_name", sa.String(length=255), primary_key=True, nullable=False),
        sa.Column("combined_doc", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    op.create_table(
        "idempotency_keys",
        sa.Column("id", uuid, primary_key=True, nullable=False),
        sa.Column("user_id", uuid, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("endpoint", sa.String(length=64), nullable=False),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("task_id", uuid, sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "endpoint", "key", name="uq_idempotency_user_endpoint_key"),
    )
    op.create_index("ix_idempotency_expires_at", "idempotency_keys", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_idempotency_expires_at", table_name="idempotency_keys")
    op.drop_table("idempotency_keys")
    op.drop_table("repo_cache")
    op.drop_table("document_chunks")
    op.drop_index("ix_documents_user_created_at", table_name="documents")
    op.drop_table("documents")
    op.drop_table("search_results")
    op.drop_table("search_requests")
    op.drop_index("ix_tasks_status_updated_at", table_name="tasks")
    op.drop_index("ix_tasks_user_created_at", table_name="tasks")
    op.drop_table("tasks")
    op.drop_index("uq_users_email_lower", table_name="users")
    op.drop_table("users")
