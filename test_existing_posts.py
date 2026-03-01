#!/usr/bin/env python3
"""Test existing blog posts for quality metrics"""
import requests
import json
import re

BASE_URL = "http://localhost:8000"
HEADERS = {
    "Authorization": "Bearer dev-token-123",
    "Content-Type": "application/json"
}

def count_words(text):
    if not text:
        return 0
    return len(text.split())

def test_posts():
    print("\n[FETCH] Getting published posts...")
    
    resp = requests.get(
        f"{BASE_URL}/api/posts?skip=0&limit=50",
        headers=HEADERS,
        timeout=10
    )
    
    if resp.status_code != 200:
        print(f"[ERROR] {resp.status_code}: {resp.text}")
        return
    
    data = resp.json()
    posts = data.get("data", [])
    
    print(f"\nFound: {len(posts)} posts")
    print("="*80)
    
    metrics = {
        "total_posts": len(posts),
        "total_words": 0,
        "avg_words": 0,
        "posts_with_images": 0,
        "posts_with_seo": 0,
        "posts": []
    }
    
    for post in posts[:10]:  # Test first 10
        post_id = post.get("id")
        title = post.get("title")
        content = post.get("content", "")
        word_count = count_words(content)
        featured_image = post.get("featured_image_url")
        seo_keywords = post.get("seo_keywords")
        
        print(f"\nPost: {title[:50]}...")
        print(f"  Words: {word_count}")
        print(f"  Image: {'YES' if featured_image else 'NO'}")
        print(f"  SEO Keywords: {'YES' if seo_keywords else 'NO'}")
        
        metrics["total_words"] += word_count
        if featured_image:
            metrics["posts_with_images"] += 1
        if seo_keywords:
            metrics["posts_with_seo"] += 1
            
        metrics["posts"].append({
            "title": title,
            "id": post_id,
            "word_count": word_count,
            "has_image": bool(featured_image),
            "has_seo": bool(seo_keywords)
        })
    
    # Calculate averages
    if metrics["posts"]:
        metrics["avg_words"] = metrics["total_words"] / len(metrics["posts"])
    
    print("\n" + "="*80)
    print("SUMMARY METRICS")
    print("="*80)
    print(f"Posts tested: {len(metrics['posts'])}")
    print(f"Total words: {metrics['total_words']}")
    print(f"Average words per post: {metrics['avg_words']:.0f}")
    print(f"Posts with featured images: {metrics['posts_with_images']}/{len(metrics['posts'])}")
    print(f"Posts with SEO keywords: {metrics['posts_with_seo']}/{len(metrics['posts'])}")
    
    print("\nDETAIL:")
    for p in metrics["posts"]:
        status = "[OK]" if p["word_count"] >= 1500 else "[LOW]"
        print(f"  {status} {p['title'][:40]}... - {p['word_count']} words")

if __name__ == "__main__":
    test_posts()
