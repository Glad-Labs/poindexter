"""
Sample Upload Service
Phase 3.1 Implementation

Handles file validation, parsing, metadata extraction, and database storage
for writing samples.
"""

import csv
import json
import io
from typing import Optional, Tuple
from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import insert
from datetime import datetime
import re

from ..models.database_models import WritingSample


class SampleUploadService:
    """Service for uploading and managing writing samples"""
    
    # Configuration
    MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB
    MIN_CONTENT_LENGTH = 100
    MAX_CONTENT_LENGTH = 50000
    ALLOWED_MIME_TYPES = {
        'text/plain': 'txt',
        'text/csv': 'csv',
        'application/json': 'json'
    }
    
    async def validate_file(
        self,
        file: UploadFile
    ) -> Tuple[bool, str]:
        """
        Validate uploaded file.
        
        Checks:
        - File size
        - File type
        - Filename
        
        Returns:
        (is_valid, error_message)
        """
        try:
            # Check filename
            if not file.filename:
                return False, "No filename provided"
            
            # Check file extension
            filename_lower = file.filename.lower()
            valid_extensions = ('.txt', '.csv', '.json')
            if not filename_lower.endswith(valid_extensions):
                return False, f"File type not supported. Use: {', '.join(valid_extensions)}"
            
            # Check content type
            if file.content_type not in self.ALLOWED_MIME_TYPES:
                return False, f"Content type '{file.content_type}' not supported"
            
            # For file size, we'll check after reading
            return True, ""
            
        except Exception as e:
            return False, f"File validation error: {str(e)}"
    
    async def parse_file(
        self,
        file: UploadFile,
        content_type: str
    ) -> Optional[str]:
        """
        Parse file content based on file type.
        
        Supported formats:
        - TXT: Plain text
        - CSV: CSV with 'content' column
        - JSON: JSON array or object with 'content' field
        
        Returns:
        Extracted content or None if parsing fails
        """
        try:
            content = await file.read()
            
            # Check file size
            if len(content) > self.MAX_FILE_SIZE:
                raise ValueError(f"File too large. Max size: {self.MAX_FILE_SIZE / 1024 / 1024}MB")
            
            if content_type == 'text/plain':
                text = content.decode('utf-8')
                
            elif content_type == 'text/csv':
                # Parse CSV
                csv_reader = csv.DictReader(io.StringIO(content.decode('utf-8')))
                rows = list(csv_reader)
                
                if not rows:
                    raise ValueError("CSV file is empty")
                
                # Get content from 'content' column
                if 'content' not in rows[0]:
                    raise ValueError("CSV must have 'content' column")
                
                # Combine all content from CSV
                text = '\n'.join(row['content'] for row in rows if row.get('content'))
                
            elif content_type == 'application/json':
                # Parse JSON
                data = json.loads(content.decode('utf-8'))
                
                if isinstance(data, list):
                    # Array of objects
                    text = '\n'.join(
                        item.get('content', str(item))
                        for item in data
                        if item
                    )
                elif isinstance(data, dict):
                    # Single object
                    text = data.get('content', json.dumps(data))
                else:
                    # Primitive value
                    text = str(data)
            else:
                raise ValueError(f"Unsupported content type: {content_type}")
            
            # Validate content length
            if len(text) < self.MIN_CONTENT_LENGTH:
                raise ValueError(f"Content too short. Min: {self.MIN_CONTENT_LENGTH} characters")
            
            if len(text) > self.MAX_CONTENT_LENGTH:
                raise ValueError(f"Content too long. Max: {self.MAX_CONTENT_LENGTH} characters")
            
            return text.strip()
            
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON: {str(e)}")
        except UnicodeDecodeError:
            raise ValueError("File encoding not supported. Use UTF-8")
        except Exception as e:
            raise ValueError(f"File parsing error: {str(e)}")
    
    async def extract_metadata(
        self,
        content: str,
        style: Optional[str] = None,
        tone: Optional[str] = None
    ) -> dict:
        """
        Extract metadata from content.
        
        Extracts:
        - word_count: Integer
        - char_count: Integer
        - avg_word_length: Float
        - sentence_count: Integer
        - paragraphs: Integer
        - avg_sentence_length: Float
        - tone_detected: String (if not provided)
        - style_detected: String (if not provided)
        - tone_markers: List of detected tone words
        - style_characteristics: List of style features
        """
        try:
            metadata = {}
            
            # Basic metrics
            words = content.split()
            sentences = re.split(r'[.!?]+', content)
            paragraphs = content.split('\n\n')
            
            metadata['word_count'] = len(words)
            metadata['char_count'] = len(content)
            metadata['avg_word_length'] = (
                sum(len(w) for w in words) / len(words)
                if words else 0
            )
            metadata['sentence_count'] = len([s for s in sentences if s.strip()])
            metadata['paragraphs'] = len([p for p in paragraphs if p.strip()])
            metadata['avg_sentence_length'] = (
                metadata['word_count'] / metadata['sentence_count']
                if metadata['sentence_count'] > 0 else 0
            )
            
            # Detect tone (if not provided)
            if not tone:
                metadata['tone_detected'] = self._detect_tone(content)
            else:
                metadata['tone_detected'] = tone
            
            # Detect style (if not provided)
            if not style:
                metadata['style_detected'] = self._detect_style(content)
            else:
                metadata['style_detected'] = style
            
            # Extract tone markers
            metadata['tone_markers'] = self._extract_tone_markers(content)
            
            # Extract style characteristics
            metadata['style_characteristics'] = self._extract_style_characteristics(content)
            
            return metadata
            
        except Exception as e:
            raise ValueError(f"Metadata extraction error: {str(e)}")
    
    def _detect_tone(self, content: str) -> str:
        """Detect tone from content"""
        content_lower = content.lower()
        
        # Tone indicators
        professional_indicators = ['therefore', 'furthermore', 'consequently', 'however']
        casual_indicators = ['really', 'pretty', 'like', 'awesome', 'cool']
        authoritative_indicators = ['must', 'essential', 'critical', 'imperative']
        conversational_indicators = ["i'm", "we're", "you'll", "don't", "isn't"]
        
        scores = {
            'professional': sum(1 for word in professional_indicators if word in content_lower),
            'casual': sum(1 for word in casual_indicators if word in content_lower),
            'authoritative': sum(1 for word in authoritative_indicators if word in content_lower),
            'conversational': sum(1 for word in conversational_indicators if word in content_lower)
        }
        
        detected = max(scores, key=scores.get) if max(scores.values()) > 0 else 'neutral'
        return detected
    
    def _detect_style(self, content: str) -> str:
        """Detect writing style from content"""
        content_lower = content.lower()
        
        # Style indicators
        technical_indicators = ['algorithm', 'implementation', 'framework', 'architecture']
        narrative_indicators = ['story', 'journey', 'experience', 'believe']
        listicle_indicators = ['here are', 'top 10', 'best ways', 'must know']
        educational_indicators = ['learn', 'understand', 'explain', 'guide']
        
        scores = {
            'technical': sum(1 for word in technical_indicators if word in content_lower),
            'narrative': sum(1 for word in narrative_indicators if word in content_lower),
            'listicle': sum(1 for word in listicle_indicators if word in content_lower),
            'educational': sum(1 for word in educational_indicators if word in content_lower),
            'thought-leadership': 0  # Check for industry insights, perspectives
        }
        
        detected = max(scores, key=scores.get) if max(scores.values()) > 0 else 'general'
        return detected
    
    def _extract_tone_markers(self, content: str) -> list:
        """Extract tone-indicating words from content"""
        tone_words = {
            'professional': ['therefore', 'furthermore', 'moreover', 'consequently'],
            'casual': ['pretty', 'really', 'awesome', 'cool', 'literally'],
            'formal': ['notwithstanding', 'moreover', 'regarding', 'pursuant'],
            'friendly': ['hey', 'great', 'wonderful', 'excited', 'love']
        }
        
        markers = []
        content_lower = content.lower()
        
        for tone, words in tone_words.items():
            for word in words:
                if word in content_lower:
                    markers.append(f"{tone}:{word}")
        
        return markers[:10]  # Return first 10
    
    def _extract_style_characteristics(self, content: str) -> list:
        """Extract style-defining characteristics"""
        characteristics = []
        
        # Check for headings
        if '\n#' in content or '\n##' in content:
            characteristics.append('uses_markdown_headings')
        
        # Check for code blocks
        if '```' in content:
            characteristics.append('includes_code_blocks')
        
        # Check for lists
        if '\n-' in content or '\n*' in content or '\n1.' in content:
            characteristics.append('uses_lists')
        
        # Check for quotes
        if '>' in content or '"' in content:
            characteristics.append('uses_quotes')
        
        # Check for short paragraphs
        paragraphs = content.split('\n\n')
        avg_para_length = sum(len(p.split()) for p in paragraphs) / len(paragraphs) if paragraphs else 0
        if avg_para_length < 50:
            characteristics.append('short_paragraphs')
        
        # Check for technical terms
        tech_terms = ['function', 'algorithm', 'protocol', 'database', 'api']
        if any(term in content.lower() for term in tech_terms):
            characteristics.append('technical_vocabulary')
        
        # Check for storytelling
        if any(word in content.lower() for word in ['once', 'then', 'suddenly', 'finally']):
            characteristics.append('narrative_flow')
        
        return characteristics
    
    async def store_sample(
        self,
        user_id: str,
        title: str,
        content: str,
        style: Optional[str] = None,
        tone: Optional[str] = None,
        metadata: Optional[dict] = None,
        db: AsyncSession = None
    ) -> int:
        """
        Store writing sample in database.
        
        Returns:
        Sample ID
        """
        try:
            if not db:
                raise ValueError("Database session required")
            
            # Prepare data
            word_count = len(content.split())
            char_count = len(content)
            
            # Merge metadata
            sample_metadata = metadata or {}
            if style:
                sample_metadata['style'] = style
            if tone:
                sample_metadata['tone'] = tone
            
            # Insert into database
            stmt = insert(WritingSample).values(
                user_id=user_id,
                title=title,
                content=content,
                word_count=word_count,
                char_count=char_count,
                metadata=sample_metadata,
                is_active=False,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            ).returning(WritingSample.id)
            
            result = await db.execute(stmt)
            sample_id = result.scalar()
            await db.commit()
            
            return sample_id
            
        except Exception as e:
            await db.rollback()
            raise ValueError(f"Failed to store sample: {str(e)}")
