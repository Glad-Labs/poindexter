"""
SQLAdmin — Lightweight admin panel for Poindexter.

Provides a web UI at /admin for browsing and managing:
- Content tasks (pipeline queue)
- Published posts
- Cost logs
- Quality evaluations
- Sites
- Settings

No React, no JavaScript — just Python + SQLAlchemy models
mapped to the existing asyncpg-managed tables.
"""

import os

from sqladmin import Admin, ModelView
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase

from services.site_config import site_config

# ---------------------------------------------------------------------------
# SQLAlchemy base + engine (read-only reflection of asyncpg-managed tables)
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    pass


def _get_sync_database_url() -> str:
    """Convert async DATABASE_URL to sync for SQLAlchemy."""
    url = os.getenv("DATABASE_URL", "")
    # asyncpg uses postgresql://, SQLAlchemy sync uses postgresql+psycopg2://
    # But SQLAdmin works with async too via asyncpg
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


# ---------------------------------------------------------------------------
# SQLAlchemy models (mapped to existing tables — no migrations needed)
# ---------------------------------------------------------------------------


class ContentTask(Base):
    __tablename__ = "content_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(255), unique=True)
    task_type = Column(String(100))
    content_type = Column(String(100))
    topic = Column(String(500))
    title = Column(String(500))
    status = Column(String(50))
    stage = Column(String(100))
    quality_score = Column(Float)
    approval_status = Column(String(50))
    publish_mode = Column(String(50))
    site_id = Column(UUID)
    model_used = Column(String(200))
    actual_cost = Column(Float)
    content = Column(Text)
    excerpt = Column(Text)
    featured_image_url = Column(String(1000))
    seo_title = Column(String(200))
    seo_description = Column(String(500))
    task_metadata = Column(JSONB)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)


class Post(Base):
    __tablename__ = "posts"

    id = Column(UUID, primary_key=True)
    title = Column(String(500))
    slug = Column(String(500), unique=True)
    content = Column(Text)
    excerpt = Column(Text)
    featured_image_url = Column(String(1000))
    status = Column(String(50))
    category_id = Column(UUID)
    site_id = Column(UUID)
    seo_title = Column(String(200))
    seo_description = Column(String(500))
    seo_keywords = Column(String(1000))
    published_at = Column(DateTime)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)


class CostLog(Base):
    __tablename__ = "cost_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(UUID)
    phase = Column(String(100))
    model = Column(String(200))
    provider = Column(String(100))
    input_tokens = Column(Integer)
    output_tokens = Column(Integer)
    cost_usd = Column(Float)
    duration_ms = Column(Integer)
    success = Column(Boolean)
    error_message = Column(Text)
    created_at = Column(DateTime)


class QualityEvaluation(Base):
    __tablename__ = "quality_evaluations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(255))
    overall_score = Column(Float)
    clarity = Column(Float)
    accuracy = Column(Float)
    completeness = Column(Float)
    relevance = Column(Float)
    seo_quality = Column(Float)
    readability = Column(Float)
    engagement = Column(Float)
    passing = Column(Boolean)
    feedback = Column(Text)
    created_at = Column(DateTime)


class Site(Base):
    __tablename__ = "sites"

    id = Column(UUID, primary_key=True)
    slug = Column(String(100), unique=True)
    name = Column(String(255))
    domain = Column(String(255))
    base_url = Column(String(500))
    config = Column(JSONB)
    is_active = Column(Boolean)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)


class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_type = Column(String(100))
    payload = Column(JSONB)
    delivered = Column(Boolean)
    delivery_attempts = Column(Integer)
    last_attempt_at = Column(DateTime)
    created_at = Column(DateTime)


class Category(Base):
    __tablename__ = "categories"

    id = Column(UUID, primary_key=True)
    name = Column(String(255))
    slug = Column(String(255), unique=True)
    description = Column(Text)


class WritingSample(Base):
    __tablename__ = "writing_samples"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(255))
    title = Column(String(500))
    description = Column(Text)
    content = Column(Text)
    is_active = Column(Boolean)
    word_count = Column(Integer)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)


# ---------------------------------------------------------------------------
# Admin views — control what's shown and how
# ---------------------------------------------------------------------------


class ContentTaskAdmin(ModelView, model=ContentTask):
    name = "Task"
    name_plural = "Tasks"
    icon = "fa-solid fa-list-check"
    column_list = [
        ContentTask.task_id,
        ContentTask.topic,
        ContentTask.status,
        ContentTask.stage,
        ContentTask.quality_score,
        ContentTask.approval_status,
        ContentTask.actual_cost,
        ContentTask.created_at,
    ]
    column_searchable_list = [ContentTask.topic, ContentTask.task_id]
    column_sortable_list = [
        ContentTask.created_at,
        ContentTask.status,
        ContentTask.quality_score,
    ]
    column_default_sort = ("created_at", True)
    page_size = 25


class PostAdmin(ModelView, model=Post):
    name = "Post"
    name_plural = "Posts"
    icon = "fa-solid fa-newspaper"
    column_list = [
        Post.title,
        Post.slug,
        Post.status,
        Post.seo_title,
        Post.published_at,
        Post.created_at,
    ]
    column_searchable_list = [Post.title, Post.slug]
    column_sortable_list = [Post.published_at, Post.created_at, Post.status]
    column_default_sort = ("published_at", True)
    page_size = 25


class CostLogAdmin(ModelView, model=CostLog):
    name = "Cost Log"
    name_plural = "Cost Logs"
    icon = "fa-solid fa-dollar-sign"
    column_list = [
        CostLog.task_id,
        CostLog.phase,
        CostLog.model,
        CostLog.provider,
        CostLog.cost_usd,
        CostLog.input_tokens,
        CostLog.output_tokens,
        CostLog.duration_ms,
        CostLog.success,
        CostLog.created_at,
    ]
    column_sortable_list = [CostLog.created_at, CostLog.cost_usd]
    column_default_sort = ("created_at", True)
    page_size = 50


class QualityAdmin(ModelView, model=QualityEvaluation):
    name = "Quality Score"
    name_plural = "Quality Scores"
    icon = "fa-solid fa-star"
    column_list = [
        QualityEvaluation.task_id,
        QualityEvaluation.overall_score,
        QualityEvaluation.clarity,
        QualityEvaluation.accuracy,
        QualityEvaluation.passing,
        QualityEvaluation.created_at,
    ]
    column_sortable_list = [
        QualityEvaluation.created_at,
        QualityEvaluation.overall_score,
    ]
    column_default_sort = ("created_at", True)
    page_size = 25


class SiteAdmin(ModelView, model=Site):
    name = "Site"
    name_plural = "Sites"
    icon = "fa-solid fa-globe"
    column_list = [Site.name, Site.slug, Site.domain, Site.is_active]


class SettingAdmin(ModelView, model=Setting):
    name = "Setting"
    name_plural = "Settings"
    icon = "fa-solid fa-gear"
    column_list = [Setting.key, Setting.value, Setting.description, Setting.updated_at]
    column_searchable_list = [Setting.key]


class WebhookEventAdmin(ModelView, model=WebhookEvent):
    name = "Webhook Event"
    name_plural = "Webhook Events"
    icon = "fa-solid fa-bell"
    column_list = [
        WebhookEvent.event_type,
        WebhookEvent.delivered,
        WebhookEvent.delivery_attempts,
        WebhookEvent.created_at,
    ]
    column_sortable_list = [WebhookEvent.created_at, WebhookEvent.delivered]
    column_default_sort = ("created_at", True)
    page_size = 50


class CategoryAdmin(ModelView, model=Category):
    name = "Category"
    name_plural = "Categories"
    icon = "fa-solid fa-folder"
    column_list = [Category.name, Category.slug]


class WritingSampleAdmin(ModelView, model=WritingSample):
    name = "Writing Sample"
    name_plural = "Writing Samples"
    icon = "fa-solid fa-pen"
    column_list = [
        WritingSample.title,
        WritingSample.is_active,
        WritingSample.word_count,
        WritingSample.created_at,
    ]
    column_sortable_list = [WritingSample.created_at]
    column_default_sort = ("created_at", True)


# ---------------------------------------------------------------------------
# Setup function — called from main.py lifespan
# ---------------------------------------------------------------------------


def setup_admin(app):
    """Mount SQLAdmin on /admin. Call after FastAPI app is created.

    SECURITY: Disabled in production — no authentication backend configured.
    Only available when ENVIRONMENT != 'production' (i.e., local development).
    """
    import os
    if os.getenv("ENVIRONMENT", "development") == "production":
        import logging
        logging.getLogger(__name__).info("[ADMIN] SQLAdmin disabled in production (no auth backend)")
        return

    database_url = _get_sync_database_url()
    if not database_url:
        return

    engine = create_engine(database_url, echo=False)

    admin = Admin(
        app,
        engine,
        title=f"{site_config.get('site_name', 'Content Pipeline')} Admin",
        base_url="/admin",
    )

    admin.add_view(ContentTaskAdmin)
    admin.add_view(PostAdmin)
    admin.add_view(CostLogAdmin)
    admin.add_view(QualityAdmin)
    admin.add_view(SiteAdmin)
    admin.add_view(SettingAdmin)
    admin.add_view(WebhookEventAdmin)
    admin.add_view(CategoryAdmin)
    admin.add_view(WritingSampleAdmin)
