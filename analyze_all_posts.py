#!/usr/bin/env python3
"""Analyze all 38 published posts for quality"""
import requests
import json

BASE_URL = "http://localhost:8000"
HEADERS = {
    "Authorization": "Bearer dev-token-123",
    "Content-Type": "application/json"
}

def count_words(text):
    if not text:
        return 0
    return len(text.split())

def analyze_all_posts():
    print("\n[FETCH] Getting all published posts...")
    
    resp = requests.get(
        f"{BASE_URL}/api/posts?skip=0&limit=100",
        headers=HEADERS,
        timeout=10
    )
    
    if resp.status_code != 200:
        print(f"[ERROR] {resp.status_code}")
        return
    
    data = resp.json()
    posts = data.get("data", [])
    
    print(f"\nAnalyzing {len(posts)} posts...")
    print("="*100)
    
    word_counts = []
    has_images = []
    has_seo = []
    
    issues = []
    
    for i, post in enumerate(posts, 1):
        post_id = post.get("id")
        title = post.get("title")
        content = post.get("content", "")
        word_count = count_words(content)
        featured_image = post.get("featured_image_url")
        seo_keywords = post.get("seo_keywords")
        
        word_counts.append(word_count)
        has_images.append(bool(featured_image))
        has_seo.append(bool(seo_keywords))
        
        # Flag issues
        if word_count < 1000:
            issues.append((title, f"Low word count: {word_count}"))
        if not featured_image:
            issues.append((title, "Missing featured image"))
        if not seo_keywords:
            issues.append((title, "Missing SEO keywords"))
    
    # Calculate statistics
    min_words = min(word_counts) if word_counts else 0
    max_words = max(word_counts) if word_counts else 0
    avg_words = sum(word_counts) / len(word_counts) if word_counts else 0
    
    print("\n" + "="*100)
    print("CONTENT QUALITY ANALYSIS")
    print("="*100)
    print(f"\nTotal Posts: {len(posts)}")
    print(f"\nWord Count Statistics:")
    print(f"  Minimum: {min_words} words")
    print(f"  Maximum: {max_words} words")
    print(f"  Average: {avg_words:.0f} words")
    print(f"  Median: {sorted(word_counts)[len(word_counts)//2]} words")
    
    # Quality breakdown
    good_length = sum(1 for w in word_counts if w >= 1500)
    okay_length = sum(1 for w in word_counts if 1000 <= w < 1500)
    poor_length = sum(1 for w in word_counts if w < 1000)
    
    print(f"\nWord Count Distribution:")
    print(f"  >= 1500 words (Good): {good_length} posts ({good_length*100/len(posts):.0f}%)")
    print(f"  1000-1499 words (Okay): {okay_length} posts ({okay_length*100/len(posts):.0f}%)")
    print(f"  < 1000 words (Poor): {poor_length} posts ({poor_length*100/len(posts):.0f}%)")
    
    print(f"\nFeatured Images:")
    with_images = sum(has_images)
    print(f"  Posts with images: {with_images}/{len(posts)} ({with_images*100/len(posts):.0f}%)")
    
    print(f"\nSEO Metadata:")
    with_seo = sum(has_seo)
    print(f"  Posts with keywords: {with_seo}/{len(posts)} ({with_seo*100/len(posts):.0f}%)")
    
    # Show issues
    if issues:
        print(f"\n" + "="*100)
        print(f"QUALITY ISSUES ({len(issues)} found)")
        print("="*100)
        for title, issue in issues[:15]:  # Show first 15
            print(f"  [!] {title[:50]}... - {issue}")
    
    # Get task quality data
    print(f"\n" + "="*100)
    print("FETCHING TASK QUALITY SCORES...")
    print("="*100)
    
    resp = requests.get(
        f"{BASE_URL}/api/tasks?status=published&limit=100",
        headers=HEADERS,
        timeout=10
    )
    
    if resp.status_code == 200:
        tasks_data = resp.json()
        tasks = tasks_data.get("tasks", [])
        
        if tasks:
            quality_scores = []
            for task in tasks[:20]:
                quality = task.get("quality_score")
                qa_feedback = task.get("qa_feedback")
                if quality:
                    quality_scores.append(float(quality))
            
            if quality_scores:
                avg_quality = sum(quality_scores) / len(quality_scores)
                min_quality = min(quality_scores)
                max_quality = max(quality_scores)
                
                print(f"\nQuality Scores (from {len(quality_scores)} tasks):")
                print(f"  Average: {avg_quality:.1f}/100")
                print(f"  Range: {min_quality:.0f} - {max_quality:.0f}")
                
                excellent = sum(1 for q in quality_scores if q >= 80)
                good = sum(1 for q in quality_scores if 60 <= q < 80)
                fair = sum(1 for q in quality_scores if q < 60)
                
                print(f"\nQuality Distribution:")
                print(f"  Excellent (80+): {excellent} ({excellent*100/len(quality_scores):.0f}%)")
                print(f"  Good (60-79): {good} ({good*100/len(quality_scores):.0f}%)")
                print(f"  Fair (<60): {fair} ({fair*100/len(quality_scores):.0f}%)")

if __name__ == "__main__":
    analyze_all_posts()
