#!/usr/bin/env python3
"""
Comprehensive Blog Post Generation Pipeline Test
Tests content length, quality scores, and featured images
"""
import requests
import json
import time
import re
from datetime import datetime

BASE_URL = "http://localhost:8000"
HEADERS = {
    "Authorization": "Bearer dev-token-123",
    "Content-Type": "application/json"
}

class BlogPipelineTest:
    """Test suite for blog post generation pipeline quality"""

    def __init__(self):
        self.created_tasks = []
        self.test_results = {
            "total_tests": 0,
            "passed": 0,
            "failed": 0,
            "tasks_tested": [],
            "quality_metrics": {}
        }

    def count_words(self, text):
        """Count words in text"""
        if not text:
            return 0
        return len(text.split())

    def create_task(self, topic, style="narrative", tone="professional", target_length=1500):
        """Create a new blog post task"""
        print(f"\n[CREATE] Blog post: {topic}")

        payload = {
            "task_type": "blog_post",
            "topic": topic,
            "style": style,
            "tone": tone,
            "target_length": target_length,
            "generate_featured_image": True,
            "quality_preference": "balanced"
        }

        try:
            resp = requests.post(
                f"{BASE_URL}/api/tasks/unified",
                headers=HEADERS,
                json=payload,
                timeout=10
            )

            if resp.status_code in [200, 202]:
                data = resp.json()
                task_id = data.get("task_id") or data.get("id") or data.get("data", {}).get("id")
                print(f"  [OK] Task created: {task_id}")
                self.created_tasks.append(task_id)
                return task_id
            else:
                print(f"  [ERROR] {resp.status_code}: {resp.text[:200]}")
                return None
        except Exception as e:
            print(f"  [ERROR] {e}")
            return None

    def wait_for_completion(self, task_id, max_wait=120):
        """Wait for task to complete (awaiting_approval or published)"""
        print(f"\n[WAIT] Waiting for task completion: {task_id}")

        for attempt in range(max_wait):
            try:
                resp = requests.get(
                    f"{BASE_URL}/api/tasks/{task_id}",
                    headers=HEADERS,
                    timeout=10
                )

                if resp.status_code == 200:
                    task = resp.json()
                    status = task.get("status")
                    stage = task.get("stage", "unknown")

                    # Task is done when it reaches awaiting_approval or published
                    if status in ["awaiting_approval", "published", "approved"]:
                        print(f"  [OK] Task {status} after {attempt}s")
                        return task

                    # Still processing
                    if attempt % 10 == 0:
                        print(f"  [STATUS] {status} - {stage} ({attempt}s elapsed)")

                    time.sleep(1)
                else:
                    print(f"  [ERROR] {resp.status_code}")
                    return None

            except Exception as e:
                print(f"  [ERROR] {e}")
                return None

        print(f"  [TIMEOUT] Task did not complete in {max_wait}s")
        return None

    def validate_task_result(self, task):
        """Validate task result structure and content"""
        task_id = task.get("id") or task.get("task_id")
        print(f"\n[VALIDATE] Task result: {task_id}")

        result = task.get("result")

        # Parse result if it's a string
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except:
                result = {}

        if not result or not isinstance(result, dict):
            print(f"  [ERROR] Result is missing or invalid (type: {type(result)})")
            return None

        # Extract content information
        content = result.get("content", "")
        word_count = self.count_words(content)

        seo_title = result.get("seo_title")
        seo_description = result.get("seo_description")
        seo_keywords = result.get("seo_keywords")
        featured_image = result.get("featured_image_url")
        quality_score = result.get("quality_score")
        qa_feedback = result.get("qa_feedback")
        post_id = result.get("post_id")

        # Validation checks
        checks = {
            "has_content": len(content) > 100,
            "content_word_count": word_count >= 1000,
            "content_word_count_target": word_count >= task.get("target_length", 1500) * 0.8,
            "has_seo_title": bool(seo_title),
            "has_seo_description": bool(seo_description),
            "has_seo_keywords": bool(seo_keywords),
            "has_featured_image": bool(featured_image),
            "featured_image_valid_url": featured_image and featured_image.startswith("http"),
            "quality_score_threshold": quality_score and float(quality_score) >= 60,
            "has_qa_feedback": bool(qa_feedback),
            "has_post_id": bool(post_id)
        }

        # Print results
        print(f"  Content: {word_count} words (target: {task.get('target_length', 1500)})")
        print(f"  Quality: {quality_score}")
        print(f"  Featured Image: {featured_image[:50] if featured_image else 'MISSING'}...")
        print(f"  SEO Title: {seo_title[:50] if seo_title else 'MISSING'}...")
        print(f"  SEO Keywords: {seo_keywords[:50] if seo_keywords else 'MISSING'}...")
        print(f"  QA Feedback: {'Present' if qa_feedback else 'Missing'}")
        print(f"  Post ID: {post_id if post_id else 'NOT CREATED'}")

        passed = sum(1 for v in checks.values() if v)
        total = len(checks)
        print(f"\n  [CHECKS] {passed}/{total} passed")

        if passed == total:
            print(f"  [PASS] All quality checks passed")
        else:
            print(f"  [FAIL] Some checks failed:")
            for check, result in checks.items():
                if not result:
                    print(f"    - {check}")

        return {
            "task_id": task_id,
            "word_count": word_count,
            "quality_score": float(quality_score) if quality_score else 0,
            "featured_image": featured_image,
            "seo_keywords": seo_keywords,
            "qa_feedback": bool(qa_feedback),
            "post_id": post_id,
            "checks": checks,
            "passed": passed == total
        }

    def test_content_pipeline(self):
        """Test the complete content generation pipeline"""
        print("\n" + "="*80)
        print("BLOG POST GENERATION PIPELINE TEST")
        print(f"Started: {datetime.now()}")
        print("="*80)

        # Test configurations
        test_configs = [
            {
                "topic": "The Future of Artificial Intelligence in Business",
                "style": "technical",
                "tone": "professional",
                "target_length": 2000
            },
            {
                "topic": "How to Build Your First Successful Side Hustle",
                "style": "narrative",
                "tone": "inspirational",
                "target_length": 1500
            },
            {
                "topic": "Understanding Machine Learning: A Beginner's Guide",
                "style": "educational",
                "tone": "casual",
                "target_length": 1800
            }
        ]

        results = []

        for config in test_configs:
            # Step 1: Create task
            task_id = self.create_task(
                topic=config["topic"],
                style=config["style"],
                tone=config["tone"],
                target_length=config["target_length"]
            )

            if not task_id:
                print(f"  [SKIP] Could not create task for: {config['topic']}")
                continue

            # Step 2: Wait for completion
            task = self.wait_for_completion(task_id, max_wait=120)

            if not task:
                print(f"  [SKIP] Task did not complete")
                continue

            # Step 3: Validate result
            validation = self.validate_task_result(task)

            if validation:
                results.append(validation)
                self.test_results["tasks_tested"].append(validation)
                if validation["passed"]:
                    self.test_results["passed"] += 1
                else:
                    self.test_results["failed"] += 1

            self.test_results["total_tests"] += 1

        # Print summary
        print("\n" + "="*80)
        print("TEST SUMMARY")
        print("="*80)

        if results:
            print(f"\nTasks Tested: {len(results)}")
            print(f"Passed: {self.test_results['passed']}")
            print(f"Failed: {self.test_results['failed']}")

            # Calculate metrics
            total_words = sum(r["word_count"] for r in results)
            avg_words = total_words / len(results) if results else 0
            avg_quality = sum(r["quality_score"] for r in results) / len(results) if results else 0

            print(f"\nContent Metrics:")
            print(f"  Total words generated: {total_words}")
            print(f"  Average words per post: {avg_words:.0f}")
            print(f"  Average quality score: {avg_quality:.1f}")

            print(f"\nFeatured Images:")
            with_images = sum(1 for r in results if r["featured_image"])
            print(f"  Posts with images: {with_images}/{len(results)}")

            print(f"\nSEO Metadata:")
            with_seo = sum(1 for r in results if r["seo_keywords"])
            print(f"  Posts with keywords: {with_seo}/{len(results)}")

            print(f"\nQA Feedback:")
            with_feedback = sum(1 for r in results if r["qa_feedback"])
            print(f"  Posts with feedback: {with_feedback}/{len(results)}")

            print(f"\nDetailed Results:")
            for r in results:
                status = "[PASS]" if r["passed"] else "[FAIL]"
                print(f"  {status} {r['task_id'][:8]}... - {r['word_count']} words, quality={r['quality_score']}")
        else:
            print("\n[ERROR] No tasks completed successfully")

        print("\n" + "="*80)
        print(f"Finished: {datetime.now()}")
        print("="*80)

        return self.test_results

def main():
    tester = BlogPipelineTest()
    results = tester.test_content_pipeline()

    # Print JSON results for parsing
    print("\n[JSON_RESULTS]")
    print(json.dumps(results, indent=2, default=str))

if __name__ == "__main__":
    main()
