#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Test script for Gemini model selection with fixed deduplication
"""
import sys
import asyncio
import os

sys.path.insert(0, 'src/cofounder_agent')

from services.content_router_service import process_content_generation_task
from services.database_service import DatabaseService

os.environ['GOOGLE_API_KEY'] = 'AIzaSyAZosXRkhSkHd8B1ownpX_B6bWEed7iCk8'

async def test_gemini():
    # Create database service
    db_service = DatabaseService()
    
    print("=== Testing Gemini 2.5 Pro Selection ===\n")
    print(f"Topic: AI Model Testing and Validation")
    print(f"Draft model selected: gemini-2.5-pro")
    print("\nGenerating blog post...")
    
    try:
        result = await process_content_generation_task(
            topic="AI Model Testing and Validation",
            style="Technical",
            tone="Professional",
            target_length=500,
            tags=["testing", "ai", "gemini"],
            generate_featured_image=False,
            database_service=db_service,
            models_by_phase={
                "draft": "gemini-2.5-pro",
                "assess": "auto",
                "outline": "auto",
                "refine": "auto",
                "finalize": "auto",
                "research": "auto"
            }
        )
        
        print(f"\nSUCCESS!")
        print(f"Task ID: {result.get('task_id')}")
        print(f"Status: {result.get('status')}")
        
        if "content" in result:
            print(f"Content length: {len(result['content'])} characters")
        if "model_used" in result:
            print(f"Model used: {result['model_used']}")
            if "Gemini" in result['model_used'] or "google" in result['model_used'].lower():
                print("\nGEMINI WAS USED FOR GENERATION!")
            else:
                print(f"\nDifferent model was used: {result['model_used']}")
        if "quality_score" in result:
            print(f"Quality score: {result['quality_score']}")
            
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_gemini())
