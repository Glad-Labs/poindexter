"""
Batch Content Generator for Glad Labs
Generates multiple high-quality blog posts for initial content seeding

Usage:
    python generate-content-batch.py
    
Environment Variables:
    COFOUNDER_API_URL - URL of the AI Co-Founder API (default: http://localhost:8000)
    AUTO_PUBLISH - Set to 'true' to auto-publish to Strapi (default: false)
"""

import asyncio
import requests
import time
import json
from datetime import datetime
from typing import List, Dict, Optional

# Configuration
COFOUNDER_API = "http://localhost:8000"
AUTO_PUBLISH = False  # Set to True after testing

# High-traffic, SEO-optimized topics for AI/tech audience
TOPICS = [
    "How AI is Revolutionizing Game Development in 2025",
    "Top 10 Machine Learning Frameworks Every Startup Should Know",
    "Building a Scalable AI Agent System: Lessons Learned from Production",
    "The Future of Autonomous Content Creation: Trends and Predictions",
    "Why Every Startup Needs an AI Co-Founder in 2025",
    "Getting Started with RAG (Retrieval-Augmented Generation): A Practical Guide",
    "Multi-Agent Systems: Architecture Patterns and Best Practices",
    "Cost-Effective AI Solutions for Solo Entrepreneurs and Small Teams",
    "From Idea to Launch: Building Your First AI Product in 30 Days",
    "AI-Powered SEO: How to Maximize Organic Traffic in 2025",
    "The Rise of AI Agents in Business Automation: What You Need to Know",
    "Local vs Cloud AI: Complete Cost Comparison for Startups",
    "Building Trust in AI Systems: A Guide to Compliance and Ethics",
    "AI Content Generation: Finding the Balance Between Quality and Quantity",
    "The Complete Guide to Strapi v5 CMS for AI-Powered Projects",
]

# Additional topic pool for expansion
ADDITIONAL_TOPICS = [
    "FastAPI vs Flask: Choosing the Right Framework for AI Applications",
    "Next.js 15 for AI Startups: Performance Optimization Guide",
    "Google Cloud Run for AI Workloads: Deployment Best Practices",
    "Building a Voice Interface for Your AI Agent with ElevenLabs",
    "Firestore vs MongoDB for AI Application Data: A Comprehensive Comparison",
    "Implementing Human-in-the-Loop AI Systems: Architecture and UX",
    "Cost Optimization Strategies for OpenAI API in Production",
    "Building Real-Time AI Dashboards with React and WebSockets",
    "Testing AI Systems: Strategies for Quality Assurance",
    "AI-Powered Analytics: Transforming Data into Actionable Insights",
]


class ContentBatchGenerator:
    """Manages batch content generation with progress tracking"""
    
    def __init__(self, api_url: str = COFOUNDER_API, auto_publish: bool = AUTO_PUBLISH):
        self.api_url = api_url
        self.auto_publish = auto_publish
        self.results: List[Dict] = []
        self.start_time = None
        
    def generate_post(self, topic: str, index: int, total: int) -> Optional[Dict]:
        """Generate a single blog post"""
        print(f"\n{'='*80}")
        print(f"[{index+1}/{total}] Generating: {topic}")
        print(f"{'='*80}")
        
        try:
            response = requests.post(
                f"{self.api_url}/api/content/generate",
                json={
                    "topic": topic,
                    "target_audience": "tech entrepreneurs, AI developers, and startup founders",
                    "category": "AI & Machine Learning",
                    "auto_publish": self.auto_publish
                },
                timeout=300  # 5 minutes per post
            )
            
            if response.ok:
                result = response.json()
                print(f"✅ SUCCESS: {result.get('title', topic)}")
                print(f"   Status: {result.get('status')}")
                print(f"   Content ID: {result.get('content_id')}")
                if result.get('strapi_id'):
                    print(f"   Strapi ID: {result.get('strapi_id')}")
                    print(f"   URL: {result.get('strapi_url')}")
                return result
            else:
                print(f"❌ FAILED: {response.status_code} - {response.text[:200]}")
                return None
                
        except requests.exceptions.Timeout:
            print(f"⏱️ TIMEOUT: Generation took longer than 5 minutes")
            return None
        except Exception as e:
            print(f"❌ ERROR: {str(e)}")
            return None
    
    async def run_batch(self, topics: List[str]):
        """Run batch generation with progress tracking"""
        self.start_time = time.time()
        total = len(topics)
        
        print("\n" + "="*80)
        print("🚀 GLAD LABS CONTENT BATCH GENERATOR")  # Note: Keeping uppercase for visual consistency in terminal
        print("="*80)
        print(f"📝 Topics to generate: {total}")
        print(f"🤖 API endpoint: {self.api_url}")
        print(f"📤 Auto-publish: {'YES ✅' if self.auto_publish else 'NO (save as draft)'}")
        print(f"⏱️  Estimated time: {total * 2} minutes ({total * 2 / 60:.1f} hours)")
        print("="*80)
        
        input("\nPress ENTER to start generation...")
        
        for i, topic in enumerate(topics):
            result = self.generate_post(topic, i, total)
            self.results.append({
                "topic": topic,
                "result": result,
                "success": result is not None
            })
            
            # Rate limiting - don't overwhelm APIs
            if i < total - 1:
                wait_time = 30
                print(f"\n⏳ Waiting {wait_time} seconds before next post...")
                time.sleep(wait_time)
        
        self.print_summary()
    
    def print_summary(self):
        """Print final summary of batch generation"""
        duration = time.time() - self.start_time
        successful = sum(1 for r in self.results if r["success"])
        failed = len(self.results) - successful
        
        print("\n" + "="*80)
        print("📊 BATCH GENERATION COMPLETE")
        print("="*80)
        print(f"✅ Successful: {successful}/{len(self.results)}")
        print(f"❌ Failed: {failed}/{len(self.results)}")
        print(f"⏱️  Total time: {duration / 60:.1f} minutes")
        print(f"📈 Average time per post: {duration / len(self.results):.1f} seconds")
        
        if successful > 0:
            print(f"\n🎉 Generated {successful} blog posts successfully!")
            
            if self.auto_publish:
                print(f"✅ All posts published to Strapi and should appear on your site")
            else:
                print(f"📋 Posts saved as drafts - use Oversight Hub to review and approve")
        
        if failed > 0:
            print(f"\n⚠️  {failed} posts failed to generate. Failed topics:")
            for r in self.results:
                if not r["success"]:
                    print(f"   - {r['topic']}")
        
        # Save results to file
        self.save_results()
        
        print("\n" + "="*80)
    
    def save_results(self):
        """Save results to JSON file for reference"""
        filename = f"content_batch_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
        with open(filename, 'w') as f:
            json.dump({
                "generated_at": datetime.now().isoformat(),
                "total_topics": len(self.results),
                "successful": sum(1 for r in self.results if r["success"]),
                "auto_publish": self.auto_publish,
                "results": self.results
            }, f, indent=2)
        
        print(f"💾 Results saved to: {filename}")


def main():
    """Main entry point"""
    print("\n🎯 GLAD LABS CONTENT BATCH GENERATOR")  # Note: Keeping uppercase for visual consistency
    print("="*80)
    
    # Choose topic set
    print("\nSelect topic set:")
    print("1. Primary topics (15 posts, ~30 minutes)")
    print("2. Additional topics (10 posts, ~20 minutes)")
    print("3. All topics (25 posts, ~50 minutes)")
    print("4. Custom (enter number of posts)")
    
    choice = input("\nEnter choice (1-4) [default: 1]: ").strip() or "1"
    
    if choice == "1":
        topics = TOPICS
    elif choice == "2":
        topics = ADDITIONAL_TOPICS
    elif choice == "3":
        topics = TOPICS + ADDITIONAL_TOPICS
    elif choice == "4":
        num = int(input("How many posts? "))
        topics = (TOPICS + ADDITIONAL_TOPICS)[:num]
    else:
        topics = TOPICS
    
    print(f"\n✅ Selected {len(topics)} topics")
    
    # Confirm auto-publish
    auto_pub = input("\nAuto-publish to Strapi? (yes/no) [default: no]: ").strip().lower()
    auto_publish = auto_pub in ["yes", "y", "true", "1"]
    
    if auto_publish:
        confirm = input("\n⚠️  WARNING: Posts will be published immediately! Type 'YES' to confirm: ")
        if confirm != "YES":
            print("❌ Cancelled")
            return
    
    # Custom API URL
    api_url = input(f"\nAPI URL [default: {COFOUNDER_API}]: ").strip() or COFOUNDER_API
    
    # Create generator and run
    generator = ContentBatchGenerator(api_url=api_url, auto_publish=auto_publish)
    asyncio.run(generator.run_batch(topics))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n❌ Generation cancelled by user")
    except Exception as e:
        print(f"\n\n❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
