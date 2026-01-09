# Backend Implementation Example - Writing Style Endpoints

## Overview

This file provides reference implementations for the FastAPI backend endpoints needed to support the Writing Style System frontend.

## Project Structure

```
src/cofounder_agent/
├── routes/
│   └── writing_style.py         (NEW - Add these endpoints)
├── models/
│   └── writing_style.py         (NEW - Add Pydantic models)
├── services/
│   └── writing_style_service.py (NEW - Business logic)
└── main.py                       (MODIFIED - Import routes)
```

## 1. Pydantic Models (`models/writing_style.py`)

```python
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from uuid import UUID

class WritingSampleCreate(BaseModel):
    """Model for creating a new writing sample"""
    title: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=1000)
    content: Optional[str] = Field(None)
    set_as_active: bool = False

class WritingSampleUpdate(BaseModel):
    """Model for updating a writing sample"""
    title: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=1000)
    content: Optional[str] = Field(None)

class WritingSampleResponse(BaseModel):
    """Response model for writing sample"""
    id: UUID
    title: str
    description: Optional[str]
    word_count: int
    is_active: bool
    created_at: datetime
    updated_at: datetime
    preview: Optional[str] = None  # First 200 chars

    class Config:
        from_attributes = True

class WritingSampleDetailResponse(WritingSampleResponse):
    """Detailed response including full content"""
    content: str

class WritingSamplesListResponse(BaseModel):
    """Response for list of samples"""
    samples: List[WritingSampleResponse]
    total: int

class ActiveSampleResponse(BaseModel):
    """Response for active sample"""
    sample: Optional[WritingSampleDetailResponse] = None
```

## 2. Database Models (SQLAlchemy)

Add to `models/database.py` or create `models/writing_style_models.py`:

```python
from sqlalchemy import Column, String, Text, Integer, Boolean, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID
from datetime import datetime
import uuid

from database_service import Base

class WritingSample(Base):
    """Model for user writing samples"""
    __tablename__ = "writing_samples"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    content = Column(Text, nullable=False)
    word_count = Column(Integer, default=0)
    is_active = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    embeddings = relationship("WritingSampleEmbedding", back_populates="sample", cascade="all, delete-orphan")
    user = relationship("User", back_populates="writing_samples")

    __table_args__ = (
        UniqueConstraint('user_id', 'title', name='unique_user_sample_title'),
    )

    def calculate_word_count(self):
        """Calculate and update word count"""
        self.word_count = len(self.content.split())
        return self.word_count

    @property
    def preview(self):
        """Get first 200 characters of content"""
        return self.content[:200] + "..." if len(self.content) > 200 else self.content

class WritingSampleEmbedding(Base):
    """Model for vector embeddings of writing samples (for RAG)"""
    __tablename__ = "writing_sample_embeddings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sample_id = Column(UUID(as_uuid=True), ForeignKey("writing_samples.id", ondelete="CASCADE"), nullable=False)
    chunk_index = Column(Integer, nullable=False)
    chunk_text = Column(Text, nullable=False)
    embedding = Column(Vector(1536), nullable=False)  # Requires pgvector extension
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    sample = relationship("WritingSample", back_populates="embeddings")
```

## 3. Service Layer (`services/writing_style_service.py`)

```python
import asyncio
from typing import Optional, List
from uuid import UUID
from datetime import datetime
import os

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.exc import IntegrityError

from models.writing_style_models import WritingSample, WritingSampleEmbedding
from models.writing_style import WritingSampleCreate, WritingSampleUpdate

class WritingStyleService:
    """Service for managing user writing samples"""

    def __init__(self, db: AsyncSession, embeddings_service=None):
        self.db = db
        self.embeddings_service = embeddings_service

    async def create_sample(self, user_id: UUID, sample_data: WritingSampleCreate) -> WritingSample:
        """Create a new writing sample"""
        # If setting as active, deactivate others
        if sample_data.set_as_active:
            await self._deactivate_all_for_user(user_id)

        sample = WritingSample(
            user_id=user_id,
            title=sample_data.title,
            description=sample_data.description,
            content=sample_data.content or "",
            is_active=sample_data.set_as_active
        )

        # Calculate word count
        sample.calculate_word_count()

        try:
            self.db.add(sample)
            await self.db.commit()
            await self.db.refresh(sample)

            # Generate embeddings asynchronously
            if self.embeddings_service:
                asyncio.create_task(self._generate_embeddings(sample.id, sample.content))

            return sample
        except IntegrityError:
            await self.db.rollback()
            raise ValueError(f"Writing sample titled '{sample_data.title}' already exists for this user")

    async def get_user_samples(self, user_id: UUID) -> List[WritingSample]:
        """Get all writing samples for a user"""
        query = select(WritingSample).where(WritingSample.user_id == user_id).order_by(WritingSample.updated_at.desc())
        result = await self.db.execute(query)
        return result.scalars().all()

    async def get_active_sample(self, user_id: UUID) -> Optional[WritingSample]:
        """Get the currently active sample for a user"""
        query = select(WritingSample).where(
            WritingSample.user_id == user_id,
            WritingSample.is_active == True
        )
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def set_active_sample(self, user_id: UUID, sample_id: UUID) -> WritingSample:
        """Set a sample as active (deactivates others)"""
        # Verify ownership
        sample = await self._get_user_sample(user_id, sample_id)
        if not sample:
            raise ValueError("Sample not found or access denied")

        # Deactivate all others
        await self._deactivate_all_for_user(user_id)

        # Activate this one
        sample.is_active = True
        sample.updated_at = datetime.utcnow()
        self.db.add(sample)
        await self.db.commit()
        await self.db.refresh(sample)
        return sample

    async def update_sample(self, user_id: UUID, sample_id: UUID, update_data: WritingSampleUpdate) -> WritingSample:
        """Update a writing sample"""
        sample = await self._get_user_sample(user_id, sample_id)
        if not sample:
            raise ValueError("Sample not found or access denied")

        if update_data.title:
            sample.title = update_data.title
        if update_data.description is not None:
            sample.description = update_data.description
        if update_data.content:
            sample.content = update_data.content
            sample.calculate_word_count()
            # Regenerate embeddings
            if self.embeddings_service:
                await self._delete_embeddings(sample_id)
                asyncio.create_task(self._generate_embeddings(sample_id, update_data.content))

        sample.updated_at = datetime.utcnow()
        self.db.add(sample)
        await self.db.commit()
        await self.db.refresh(sample)
        return sample

    async def delete_sample(self, user_id: UUID, sample_id: UUID) -> bool:
        """Delete a writing sample"""
        sample = await self._get_user_sample(user_id, sample_id)
        if not sample:
            raise ValueError("Sample not found or access denied")

        await self.db.delete(sample)
        await self.db.commit()
        return True

    # Private helper methods

    async def _get_user_sample(self, user_id: UUID, sample_id: UUID) -> Optional[WritingSample]:
        """Get a sample and verify ownership"""
        query = select(WritingSample).where(
            WritingSample.id == sample_id,
            WritingSample.user_id == user_id
        )
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def _deactivate_all_for_user(self, user_id: UUID) -> None:
        """Deactivate all samples for a user"""
        query = select(WritingSample).where(
            WritingSample.user_id == user_id,
            WritingSample.is_active == True
        )
        result = await self.db.execute(query)
        samples = result.scalars().all()
        for sample in samples:
            sample.is_active = False
        await self.db.commit()

    async def _delete_embeddings(self, sample_id: UUID) -> None:
        """Delete embeddings for a sample"""
        query = select(WritingSampleEmbedding).where(WritingSampleEmbedding.sample_id == sample_id)
        result = await self.db.execute(query)
        embeddings = result.scalars().all()
        for embedding in embeddings:
            await self.db.delete(embedding)
        await self.db.commit()

    async def _generate_embeddings(self, sample_id: UUID, content: str) -> None:
        """Generate and store embeddings for a sample (runs async)"""
        if not self.embeddings_service:
            return

        try:
            # Split content into chunks (500 char chunks with overlap)
            chunks = self._chunk_text(content, chunk_size=500, overlap=50)

            for idx, chunk in enumerate(chunks):
                # Generate embedding using your embedding service
                embedding_vector = await self.embeddings_service.embed_text(chunk)

                embedding = WritingSampleEmbedding(
                    sample_id=sample_id,
                    chunk_index=idx,
                    chunk_text=chunk,
                    embedding=embedding_vector
                )
                self.db.add(embedding)

            await self.db.commit()
        except Exception as e:
            print(f"Error generating embeddings for sample {sample_id}: {e}")

    @staticmethod
    def _chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> List[str]:
        """Split text into overlapping chunks"""
        chunks = []
        for i in range(0, len(text), chunk_size - overlap):
            chunks.append(text[i:i + chunk_size])
        return chunks
```

## 4. FastAPI Routes (`routes/writing_style.py`)

```python
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status
from fastapi.security import HTTPBearer
from typing import Optional
from uuid import UUID
import shutil
import os
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession
from models.writing_style import (
    WritingSampleCreate,
    WritingSampleUpdate,
    WritingSampleResponse,
    WritingSampleDetailResponse,
    WritingSamplesListResponse,
    ActiveSampleResponse
)
from services.writing_style_service import WritingStyleService
from services.database_service import get_db
from middleware.auth import get_current_user  # Your auth middleware

router = APIRouter(prefix="/api/writing-style", tags=["writing-style"])
security = HTTPBearer()

# Configuration
UPLOAD_DIR = "uploads/writing_samples"
MAX_FILE_SIZE = 1024 * 1024  # 1MB
ALLOWED_EXTENSIONS = {".txt", ".md", ".pdf"}

@router.post("/upload")
async def upload_writing_sample(
    title: str = Form(...),
    description: Optional[str] = Form(None),
    content: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    set_as_active: bool = Form(False),
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Upload a new writing sample (file or text content)"""

    # Validate that either file or content is provided
    if not file and not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either file or content must be provided"
        )

    # Extract content from file if provided
    sample_content = content
    if file:
        # Validate file
        if file.size > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File must be less than {MAX_FILE_SIZE / 1024 / 1024}MB"
            )

        file_ext = Path(file.filename).suffix.lower()
        if file_ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Only {', '.join(ALLOWED_EXTENSIONS)} files allowed"
            )

        # Read file content
        content_bytes = await file.read()
        sample_content = content_bytes.decode('utf-8')

    try:
        service = WritingStyleService(db)
        sample_data = WritingSampleCreate(
            title=title,
            description=description,
            content=sample_content,
            set_as_active=set_as_active
        )
        sample = await service.create_sample(current_user.id, sample_data)
        return WritingSampleResponse.from_orm(sample)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

@router.get("/samples")
async def get_user_samples(
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get all writing samples for the current user"""
    try:
        service = WritingStyleService(db)
        samples = await service.get_user_samples(current_user.id)
        return WritingSamplesListResponse(
            samples=[WritingSampleResponse.from_orm(s) for s in samples],
            total=len(samples)
        )
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

@router.get("/active")
async def get_active_sample(
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get the currently active writing sample"""
    try:
        service = WritingStyleService(db)
        sample = await service.get_active_sample(current_user.id)
        if sample:
            return ActiveSampleResponse(sample=WritingSampleDetailResponse.from_orm(sample))
        return ActiveSampleResponse(sample=None)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

@router.put("/{sample_id}/set-active")
async def set_active_sample(
    sample_id: UUID,
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Set a writing sample as active"""
    try:
        service = WritingStyleService(db)
        sample = await service.set_active_sample(current_user.id, sample_id)
        return WritingSampleResponse.from_orm(sample)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

@router.put("/{sample_id}")
async def update_sample(
    sample_id: UUID,
    update_data: WritingSampleUpdate,
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Update a writing sample"""
    try:
        service = WritingStyleService(db)
        sample = await service.update_sample(current_user.id, sample_id, update_data)
        return WritingSampleResponse.from_orm(sample)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

@router.delete("/{sample_id}")
async def delete_sample(
    sample_id: UUID,
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Delete a writing sample"""
    try:
        service = WritingStyleService(db)
        await service.delete_sample(current_user.id, sample_id)
        return {"message": "Writing sample deleted successfully"}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
```

## 5. Register Routes in Main (`main.py`)

```python
from fastapi import FastAPI
from routes.writing_style import router as writing_style_router

app = FastAPI()

# ... existing code ...

# Register writing style routes
app.include_router(writing_style_router)

# ... rest of startup code ...
```

## 6. Database Migrations

Create a migration file using Alembic:

```bash
alembic revision --autogenerate -m "Add writing_samples tables"
```

Generated migration should include:

```python
# tables to create
- writing_samples
- writing_sample_embeddings

# pgvector extension
- CREATE EXTENSION IF NOT EXISTS vector;
```

## Testing the Implementation

### Test with cURL

```bash
# 1. Upload sample via text content
curl -X POST http://localhost:8000/api/writing-style/upload \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "title=My Blog Style" \
  -F "description=Personal blog writing" \
  -F "content=This is my writing sample..." \
  -F "set_as_active=true"

# 2. Upload via file
curl -X POST http://localhost:8000/api/writing-style/upload \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "title=Email Style" \
  -F "file=@sample.txt"

# 3. Get all samples
curl http://localhost:8000/api/writing-style/samples \
  -H "Authorization: Bearer YOUR_TOKEN"

# 4. Get active sample
curl http://localhost:8000/api/writing-style/active \
  -H "Authorization: Bearer YOUR_TOKEN"

# 5. Set as active
curl -X PUT http://localhost:8000/api/writing-style/{sample-id}/set-active \
  -H "Authorization: Bearer YOUR_TOKEN"

# 6. Update sample
curl -X PUT http://localhost:8000/api/writing-style/{sample-id} \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"title": "Updated Title", "description": "Updated description"}'

# 7. Delete sample
curl -X DELETE http://localhost:8000/api/writing-style/{sample-id} \
  -H "Authorization: Bearer YOUR_TOKEN"
```

## Integration with Content Agent

```python
# In src/agents/content_agent/agent.py

from services.writing_style_service import WritingStyleService

class ContentAgent:
    def __init__(self, db_session, embeddings_service):
        self.writing_style_service = WritingStyleService(db_session, embeddings_service)

    async def generate_content(self, task):
        # Get writing style if specified
        style_sample = None
        if task.writing_style_id:
            style_sample = await self.writing_style_service.get_active_sample(task.user_id)

        # Use in prompt engineering
        style_context = ""
        if style_sample:
            style_context = f"""
            Please match the following writing style:

            {style_sample.preview}...

            Key characteristics:
            - Tone: [analyze from sample]
            - Length: [analyze from sample]
            - Structure: [analyze from sample]
            """

        # Include in RAG context
        rag_context = await self.retrieval_service.retrieve(
            query=task.description,
            style_sample=style_sample
        )

        # Generate content with style guidance
        prompt = f"""
        {style_context}

        Content Request: {task.description}

        Context: {rag_context}

        Generate content matching the specified style...
        """

        return await self.llm_service.generate(prompt)
```

## Summary

This implementation provides:

- ✅ Complete API endpoints
- ✅ Database models
- ✅ Service layer with business logic
- ✅ File upload handling
- ✅ User isolation/authentication
- ✅ Embedding generation pipeline
- ✅ Error handling
- ✅ Integration with content agent

Estimated implementation time: 2-3 weeks including testing and refinement.
