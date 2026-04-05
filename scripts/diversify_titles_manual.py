"""Diversify post titles — manual rewrites without LLM."""
import os
import asyncio
import httpx

API_URL = "https://cofounder-production.up.railway.app"
TOKEN = os.environ.get("API_TOKEN", "")
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

# Manual title rewrites — keeping same meaning, removing "The" prefix
REWRITES = {
    "The Invisible Architecture: How Developer Productivity Tools Evolved in 2026":
        "How Developer Productivity Tools Quietly Evolved in 2026",
    "The Architecture of Trust: Building Production-Ready CI/CD Pipelines from Scratch":
        "Building Production-Ready CI/CD Pipelines from Scratch",
    "The Lone Wolf's Dilemma: Why Solo Founders Are Switching to Next.js":
        "Why Solo Founders Are Switching to Next.js",
    "The New Pulse of Digital Business: How Grafana Is Reshaping SaaS Companies in 2026":
        "How Grafana Is Reshaping SaaS Operations in 2026",
    "The Silent Killer of Startup Growth: How to Build a Lean SaaS Stack Without Breaking the Bank in 2026":
        "Building a Lean SaaS Stack Without Breaking the Bank",
    "The Fast Track to Efficiency: Why FastAPI is the Secret Weapon for Small Businesses":
        "Why FastAPI Is a Secret Weapon for Small Businesses",
    "The Monorepo Decision: When One Repo Rules Them All":
        "Monorepos: When One Repo Rules Them All",
    "The Secret Weapon Every Python Developer Needs: Mastering Railway Deployment":
        "Mastering Railway Deployment for Python Developers",
    "The Zero-Downtime Myth: Why Your Next Migration Doesn't Have to Break Anything":
        "Zero-Downtime Migrations: Why Your Next Deploy Doesn't Have to Break Anything",
    "The Solo Founder's Tech Stack in 2026: Why 'One-Size-Fits-All' Is Dead":
        "Solo Founder Tech Stacks in 2026: Why One-Size-Fits-All Is Dead",
    "The Hidden Cost of Rigid Databases in AI Applications":
        "Rigid Databases Are Holding Back AI Applications — Here's Why",
    "The Case for PostgreSQL as Your Only Database in 2026":
        "PostgreSQL as Your Only Database in 2026: A Strong Case",
    "The Solo Developer's Guide to Staying Out of the Hacker's Hit List":
        "How Solo Developers Can Stay Off the Hacker's Hit List",
    "The Velocity Secret: Why Modern Startups Are Choosing FastAPI Over Traditional Frameworks":
        "Why Modern Startups Are Choosing FastAPI Over Legacy Frameworks",
    "The Hidden Advantage: Why Next.js is the Strategic Choice for Small Business Growth":
        "Next.js as a Strategic Choice for Small Business Growth",
    "The Secret Weapon Quietly Transforming Your Marketing Strategy in 2026":
        "How AI Is Quietly Transforming Marketing Strategy in 2026",
    "The Silent Revolution: How Solo Founders Are Scaling Intelligence Without Scaling Headcount":
        "Scaling Intelligence Without Scaling Headcount: A Solo Founder's Playbook",
    "The Silent Revolution: How Local LLMs Are Rewriting the Startup Rulebook in 2026":
        "Local LLMs Are Rewriting the Startup Rulebook in 2026",
    "The Hidden Costs of Next.js Nobody Talks About":
        "Hidden Costs of Next.js Nobody Talks About",
    "The 90-Day Sprint: What Actually Matters When Launching a Solo SaaS":
        "90-Day SaaS Launch: What Actually Matters",
    "The Weekend Code-Strike: How to Validate a SaaS Idea in 48 Hours Without Writing a Single Line":
        "Validate a SaaS Idea in 48 Hours Without Writing Code",
    "The Database Dilemma: Picking the Right Weapon for Your Data":
        "Picking the Right Database for Your Data",
    "The Silent Revolution: How Small Businesses Are Winning with Automated Workflows":
        "How Small Businesses Are Winning with Automated Workflows",
    "The Invisible Workforce: How Prompt Engineering is Reshaping Small Businesses in 2026":
        "Prompt Engineering Is Reshaping Small Business in 2026",
    "The Silent Killer of Technical Startups: Why You're Building in a Vacuum":
        "Why Technical Startups Fail: Building in a Vacuum",
}


async def main():
    print(f"Applying {len(REWRITES)} title rewrites...")
    print()

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{API_URL}/api/posts?limit=50&published_only=true",
            headers=HEADERS,
        )
        resp.raise_for_status()
        posts = resp.json().get("posts", [])

    updated = 0
    for post in posts:
        old_title = post["title"]
        if old_title in REWRITES:
            new_title = REWRITES[old_title]
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.patch(
                    f"{API_URL}/api/posts/{post['id']}",
                    headers=HEADERS,
                    json={"title": new_title},
                )
                if resp.status_code == 200:
                    print(f"  OLD: {old_title[:60]}")
                    print(f"  NEW: {new_title[:60]}")
                    print()
                    updated += 1
                else:
                    print(f"  ERROR: {old_title[:40]} — {resp.status_code}")

    print(f"Done: {updated}/{len(REWRITES)} titles updated")


if __name__ == "__main__":
    asyncio.run(main())
