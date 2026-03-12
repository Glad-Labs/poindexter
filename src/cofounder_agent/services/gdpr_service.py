"""GDPR service for data subject request workflows."""

import csv
import io
import json
from services.logger_config import get_logger
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from uuid import uuid4

from services.database_service import DatabaseService

logger = get_logger(__name__)
class GDPRService:
    """Handles GDPR request persistence, verification, export, and audit logging."""

    def __init__(self, db_service: DatabaseService):
        self.db_service = db_service
        self._schema_ready = False

    def _get_pool(self):
        """Return initialized DB pool or raise a clear error."""
        pool = self.db_service.pool
        if pool is None:
            raise RuntimeError("Database pool is not initialized")
        return pool

    async def ensure_schema(self) -> None:
        """Create GDPR tables if they do not exist."""
        if self._schema_ready:
            return

        create_requests = """
        CREATE TABLE IF NOT EXISTS gdpr_requests (
            id UUID PRIMARY KEY,
            user_id UUID,
            request_type VARCHAR(50) NOT NULL,
            status VARCHAR(50) NOT NULL,
            email VARCHAR(320) NOT NULL,
            name VARCHAR(255),
            details TEXT,
            data_categories JSONB DEFAULT '[]'::jsonb,
            verification_token VARCHAR(255) UNIQUE,
            verification_sent_at TIMESTAMP WITH TIME ZONE,
            verified_at TIMESTAMP WITH TIME ZONE,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            deadline_at TIMESTAMP WITH TIME ZONE,
            completed_at TIMESTAMP WITH TIME ZONE,
            metadata JSONB DEFAULT '{}'::jsonb
        )
        """

        create_audit = """
        CREATE TABLE IF NOT EXISTS gdpr_audit_log (
            id BIGSERIAL PRIMARY KEY,
            request_id UUID REFERENCES gdpr_requests(id) ON DELETE CASCADE,
            operation VARCHAR(100) NOT NULL,
            status VARCHAR(50) NOT NULL,
            metadata JSONB DEFAULT '{}'::jsonb,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        )
        """

        create_indexes = """
        CREATE INDEX IF NOT EXISTS idx_gdpr_requests_email ON gdpr_requests(email);
        CREATE INDEX IF NOT EXISTS idx_gdpr_requests_status ON gdpr_requests(status);
        CREATE INDEX IF NOT EXISTS idx_gdpr_requests_created_at ON gdpr_requests(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_gdpr_requests_deadline_at ON gdpr_requests(deadline_at);
        CREATE INDEX IF NOT EXISTS idx_gdpr_audit_request_id ON gdpr_audit_log(request_id);
        CREATE INDEX IF NOT EXISTS idx_gdpr_audit_created_at ON gdpr_audit_log(created_at DESC);
        """

        try:
            async with self._get_pool().acquire() as conn:
                await conn.execute(create_requests)
                await conn.execute(create_audit)
                await conn.execute(create_indexes)
            self._schema_ready = True
        except Exception as e:
            logger.error(
                f"[gdpr_ensure_schema] Failed to initialize GDPR schema: {e}", exc_info=True
            )
            raise

    async def create_request(
        self,
        request_type: str,
        email: str,
        name: Optional[str],
        details: Optional[str],
        data_categories: Optional[list[str]],
    ) -> Dict[str, Any]:
        """Create a GDPR request and return metadata including verification token."""
        await self.ensure_schema()

        request_id = str(uuid4())
        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        deadline_at = now + timedelta(days=30)

        sql = """
        INSERT INTO gdpr_requests (
            id, request_type, status, email, name, details, data_categories,
            verification_token, created_at, deadline_at, metadata
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9, $10, $11::jsonb)
        RETURNING id, request_type, status, email, name, details, data_categories,
                  verification_sent_at, verified_at, created_at, deadline_at, completed_at
        """

        metadata = {
            "source": "api",
            "workflow": "gdpr_data_subject_rights",
        }

        try:
            async with self._get_pool().acquire() as conn:
                row = await conn.fetchrow(
                    sql,
                    request_id,
                    request_type,
                    "pending_verification",
                    email,
                    name,
                    details,
                    json.dumps(data_categories or []),
                    token,
                    now,
                    deadline_at,
                    json.dumps(metadata),
                )

            await self.audit(
                request_id=request_id,
                operation="request_created",
                status="success",
                metadata={"request_type": request_type, "email": email},
            )

            if row is None:
                raise RuntimeError("Failed to create GDPR request")

            request_data = dict(row)
            request_data["verification_token"] = token
            return request_data
        except Exception as e:
            logger.error(
                f"[gdpr_create_request] Failed to create GDPR request for {email}: {e}",
                exc_info=True,
            )
            raise

    async def mark_verification_sent(self, request_id: str) -> None:
        """Mark verification email as sent."""
        await self.ensure_schema()

        try:
            async with self._get_pool().acquire() as conn:
                await conn.execute(
                    """
                    UPDATE gdpr_requests
                    SET verification_sent_at = CURRENT_TIMESTAMP
                    WHERE id = $1
                    """,
                    request_id,
                )
            await self.audit(request_id, "verification_sent", "success", {})
        except Exception as e:
            logger.error(
                f"[gdpr_mark_verification_sent] Failed for request {request_id}: {e}", exc_info=True
            )
            raise

    async def verify_request(self, token: str) -> Optional[Dict[str, Any]]:
        """Verify a GDPR request using one-time token."""
        await self.ensure_schema()

        try:
            async with self._get_pool().acquire() as conn:
                row = await conn.fetchrow(
                    """
                    UPDATE gdpr_requests
                    SET status = 'verified', verified_at = CURRENT_TIMESTAMP
                    WHERE verification_token = $1
                      AND status = 'pending_verification'
                    RETURNING id, request_type, status, email, created_at, deadline_at, verified_at
                    """,
                    token,
                )

            if row is None:
                return None

            request_data = dict(row)
            await self.audit(str(request_data["id"]), "request_verified", "success", {})
            return request_data
        except Exception as e:
            logger.error("[gdpr_verify_request] Failed to verify GDPR request token", exc_info=True)
            raise

    async def get_request(self, request_id: str) -> Optional[Dict[str, Any]]:
        """Get request status by request ID."""
        await self.ensure_schema()

        try:
            async with self._get_pool().acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT id, request_type, status, email, name, details, data_categories,
                           verification_sent_at, verified_at, created_at, deadline_at, completed_at
                    FROM gdpr_requests
                    WHERE id = $1
                    """,
                    request_id,
                )
            return dict(row) if row else None
        except Exception as e:
            logger.error(
                f"[gdpr_get_request] Failed to fetch request {request_id}: {e}", exc_info=True
            )
            raise

    async def export_user_data(self, request_id: str, fmt: str = "json") -> Dict[str, Any]:
        """Export user data for verified access/portability requests."""
        await self.ensure_schema()

        request_data = await self.get_request(request_id)
        if request_data is None:
            raise ValueError("Request not found")
        if request_data["status"] != "verified":
            raise ValueError("Request must be verified before export")
        if request_data["request_type"] not in {"access", "portability"}:
            raise ValueError("Export is only available for access/portability requests")

        email = request_data["email"]

        async with self._get_pool().acquire() as conn:
            await conn.execute(
                "UPDATE gdpr_requests SET status = 'processing' WHERE id = $1",
                request_id,
            )

            user_row = await conn.fetchrow(
                "SELECT id, email, username, role, created_at, updated_at FROM users WHERE email = $1 LIMIT 1",
                email,
            )

            user_id = user_row["id"] if user_row else None

            oauth_rows = []
            task_rows = []
            sample_rows = []
            if user_id:
                oauth_rows = await conn.fetch(
                    "SELECT provider, provider_user_id, created_at, last_used FROM oauth_accounts WHERE user_id = $1",
                    user_id,
                )
                task_rows = await conn.fetch(
                    "SELECT id, status, topic, category, created_at, updated_at FROM content_tasks WHERE owner_id = $1 ORDER BY created_at DESC LIMIT 500",
                    user_id,
                )
                sample_rows = await conn.fetch(
                    "SELECT id, title, description, is_active, word_count, char_count, created_at, updated_at FROM writing_samples WHERE user_id = $1 ORDER BY created_at DESC",
                    user_id,
                )

            data = {
                "request": request_data,
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "user": dict(user_row) if user_row else None,
                "oauth_accounts": [dict(r) for r in oauth_rows],
                "tasks": [dict(r) for r in task_rows],
                "writing_samples": [dict(r) for r in sample_rows],
            }

            await conn.execute(
                """
                UPDATE gdpr_requests
                SET status = 'completed', completed_at = CURRENT_TIMESTAMP
                WHERE id = $1
                """,
                request_id,
            )

        await self.audit(request_id, "data_export_completed", "success", {"format": fmt})

        if fmt == "csv":
            return {
                "request_id": request_id,
                "format": "csv",
                "filename": f"gdpr_export_{request_id}.csv",
                "content": self._to_csv(data),
            }

        return {
            "request_id": request_id,
            "format": "json",
            "data": data,
        }

    async def record_deletion_processing(self, request_id: str) -> Dict[str, Any]:
        """Mark deletion request as processing with deadline tracking."""
        await self.ensure_schema()

        request_data = await self.get_request(request_id)
        if request_data is None:
            raise ValueError("Request not found")
        if request_data["status"] not in {"verified", "processing"}:
            raise ValueError("Deletion request must be verified before processing")
        if request_data["request_type"] != "deletion":
            raise ValueError("This endpoint is only for deletion requests")

        now = datetime.now(timezone.utc)
        deadline = request_data.get("deadline_at")
        if deadline and now > deadline:
            await self.audit(request_id, "deletion_deadline_missed", "warning", {})

        try:
            async with self._get_pool().acquire() as conn:
                await conn.execute(
                    "UPDATE gdpr_requests SET status = 'processing' WHERE id = $1",
                    request_id,
                )
            await self.audit(request_id, "deletion_processing_started", "success", {})
            updated = await self.get_request(request_id)
            if updated is None:
                raise RuntimeError("Failed to load updated request")
            return updated
        except Exception as e:
            logger.error(
                f"[gdpr_record_deletion_processing] Failed for request {request_id}: {e}",
                exc_info=True,
            )
            raise

    async def audit(
        self,
        request_id: str,
        operation: str,
        status: str,
        metadata: Dict[str, Any],
    ) -> None:
        """Write GDPR audit event."""
        await self.ensure_schema()

        try:
            async with self._get_pool().acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO gdpr_audit_log (request_id, operation, status, metadata)
                    VALUES ($1, $2, $3, $4::jsonb)
                    """,
                    request_id,
                    operation,
                    status,
                    json.dumps(metadata),
                )
        except Exception as e:
            logger.error(
                f"[gdpr_audit] Failed to write audit event {operation} for request {request_id}: {e}",
                exc_info=True,
            )
            raise

    @staticmethod
    def _to_csv(data: Dict[str, Any]) -> str:
        """Flatten export payload to a simple CSV representation."""
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["section", "payload"])

        for key, value in data.items():
            writer.writerow([key, json.dumps(value, default=str)])

        return output.getvalue()
