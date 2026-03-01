#!/usr/bin/env python3
"""
Blog Post Generation Pipeline Quality Test
Tests content generation for: length, quality, images, and metadata
"""
import requests
import json
import time
from datetime import datetime

BASE_URL = "http://localhost:8000"
HEADERS = {"Authorization": "Bearer dev-token-123"}

TEST_TOPICS = [
    {
        "topic": "Advanced React Patterns and Best Practices in 2026",
        "audience": "Senior Full Stack Developers",
        "style": "technical",
        "tone": "professional"
    },
    {
        "topic": "Machine Learning for Business Decision Making",
        "audience": "Data Scientists and Product Managers",
        "style": "educational",
        "tone": "inspirational"
    },
    {
        "topic": "Microservices Architecture: Scaling Your Application",
        "audience": "DevOps Engineers",
        "style": "technical",
        "tone": "professional"
    }
]

class BlogQualityTest:
    def __init__(self):
        self.results = []
        self.total_posts = 0
        self.passed = 0
        self.failed = 0
        
    def test_content_generation(self, test_config):
        """Generate a blog post and verify quality metrics"""
        print(f"\n{'='*80}")
        print(f"Testing: {test_config['topic']}")
        print(f"{'='*80}")
        
        # Step 1: Create task
        print("\n[STEP 1] Creating blog post task...")
        resp = requests.post(
            f"{BASE_URL}/api/tasks",
            headers=HEADERS,
            json={
                "task_type": "blog_post",
                "topic": test_config["topic"],
                "target_audience": test_config["audience"],
                "style": test_config["style"],
                "tone": test_config["tone"],
                "target_length": 2000,
                "tags": [test_config["style"], "quality-test"]
            }
        )
        
        if resp.status_code not in [200, 202]:
            print(f"[ERROR] Task creation failed: {resp.status_code}")
            self.failed += 1
            return
        
        task_id = resp.json()['task_id']
        print(f"[OK] Task created: {task_id[:8]}")
        
        # Step 2: Wait for content generation
        print("\n[STEP 2] Waiting for content generation (up to 60 seconds)...")
        task = None
        for attempt in range(6):
            time.sleep(10)
            resp = requests.get(f"{BASE_URL}/api/tasks/{task_id}", headers=HEADERS)
            if resp.status_code != 200:
                continue
            
            task = resp.json()
            status = task.get('status')
            print(f"  Attempt {attempt+1}: status={status}")
            
            if status in ['awaiting_approval', 'approved', 'published', 'failed']:
                break
        
        if not task:
            print("[ERROR] Could not fetch task after generation")
            self.failed += 1
            return
        
        # Step 3: Analyze generated content
        print(f"\n[STEP 3] Analyzing generated content...")
        
        result = task.get('result', {})
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except:
                result = {}
        
        # Metrics
        content = result.get('content', '')
        word_count = len(content.split())
        quality_score = task.get('quality_score', 0)
        featured_image = result.get('featured_image_url')
        seo_keywords = result.get('seo_keywords', [])
        seo_title = result.get('seo_title', '')
        seo_description = result.get('seo_description', '')
        qa_feedback = task.get('qa_feedback')
        
        print(f"  Content length: {len(content)} chars, {word_count} words")
        print(f"  Quality score: {quality_score}/100")
        print(f"  Featured image: {bool(featured_image)}")
        print(f"  Featured image URL: {featured_image[:60] if featured_image else 'None'}...")
        print(f"  SEO title: {seo_title}")
        print(f"  SEO keywords: {seo_keywords}")
        print(f"  Has QA feedback: {bool(qa_feedback)}")
        if qa_feedback:
            print(f"  QA feedback: {qa_feedback[:100]}...")
        
        # Validation
        print(f"\n[STEP 4] Quality validation...")
        checks = {
            "Content length >= 1500 words": word_count >= 1500,
            "Content length <= 5000 words": word_count <= 5000,
            "Quality score >= 60": quality_score >= 60,
            "Featured image present": bool(featured_image),
            "Featured image is URL": featured_image.startswith('http') if featured_image else False,
            "SEO title present": bool(seo_title),
            "SEO description present": bool(seo_description),
            "SEO keywords present": len(seo_keywords) > 0 if isinstance(seo_keywords, list) else False,
            "QA feedback available": bool(qa_feedback)
        }
        
        passed_checks = sum(1 for v in checks.values() if v)
        total_checks = len(checks)
        
        for check, result in checks.items():
            status_icon = "[OK]" if result else "[FAIL]"
            print(f"  {status_icon} {check}: {result}")
        
        print(f"\n  Passed: {passed_checks}/{total_checks} checks")
        
        # Determine overall result
        is_quality = (
            word_count >= 1500 and
            quality_score >= 60 and
            bool(featured_image) and
            bool(seo_title) and
            bool(seo_description)
        )
        
        if is_quality and passed_checks >= 7:
            print(f"\n[PASS] Post meets quality standards!")
            self.passed += 1
        else:
            print(f"\n[WARN] Post may need quality review")
            self.failed += 1
        
        self.total_posts += 1
        self.results.append({
            "topic": test_config["topic"],
            "word_count": word_count,
            "quality_score": quality_score,
            "has_image": bool(featured_image),
            "passed": is_quality,
            "checks_passed": passed_checks
        })
        
        return task_id
    
    def print_summary(self):
        """Print test summary"""
        print(f"\n\n{'='*80}")
        print("CONTENT PIPELINE TEST SUMMARY")
        print(f"{'='*80}")
        print(f"\nTotal posts tested: {self.total_posts}")
        print(f"Passed quality checks: {self.passed}")
        print(f"Need review: {self.failed}")
        print(f"Pass rate: {(self.passed/max(self.total_posts, 1))*100:.1f}%")
        
        if self.results:
            print(f"\n{'Topic':<50} {'Words':<8} {'Score':<8} {'Image':<8}")
            print("-" * 75)
            for r in self.results:
                topic = r['topic'][:47] + "..." if len(r['topic']) > 50 else r['topic']
                print(f"{topic:<50} {r['word_count']:<8} {r['quality_score']:<8.1f} {'Yes' if r['has_image'] else 'No':<8}")
        
        print(f"\n{'='*80}\n")

def main():
    print(f"\n{'='*80}")
    print("BLOG POST GENERATION PIPELINE - QUALITY TEST")
    print(f"Start time: {datetime.now()}")
    print(f"{'='*80}")
    
    tester = BlogQualityTest()
    
    for test_config in TEST_TOPICS:
        tester.test_content_generation(test_config)
    
    tester.print_summary()

if __name__ == "__main__":
    main()
