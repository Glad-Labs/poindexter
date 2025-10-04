# **GLAD LABS: AI FRONTIER FIRM MASTER PLAN V2.1**

**Project Name:** Glad Labs, LLC  
**Owner/Manager:** Matthew M. Gladding  
**Core Mission:** To operate the most efficient, automated, solo-founded digital firm, demonstrating a scalable model for the future of specialized business by fusing high-quality content creation with intelligent, serverless automation.

---

## **1. Strategic Pillars & Value Proposition**

| Pillar | Focus | Goal |
| :--- | :---- | :---- |
| **Core Product** | **Intelligent Automation (SaaS)** | The marketable product is the **AI Agent System** itself. This pivots the business model to scalable B2B services (future SaaS). |
| **Content Engine** | **High-Fidelity Content** | Consistently generate sophisticated, on-brand content that builds a community and drives traffic for future monetization. |
| **Technology** | **Serverless Scalability** | Utilize a cost-effective, Google-Native stack to eliminate manual maintenance and pay only for usage, ensuring maximum runway. |

**Brand Tone Mandate:** **Positive, Educational, and Authentically Futuristic.** The tone must be intelligent and empowering, strictly forbidding all cyberpunk slang (e.g., "choom," "jack-in," "preem"). The aesthetic is conveyed only through technical analogy (e.g., "neural network," "asynchronous data stream").

---

## **2. Technical Architecture (The Google-Native Stack)**

The entire system is built on a monorepo structure, leveraging micro-containers and serverless resources for maximum efficiency and scalability.

| Component | Technology | Best Practice & Rationale |
| :--- | :--- | :--- |
| **Monorepo** | **GitHub (glad-labs-website)** | Centralizes all code (Agents, CMS, Web) using isolated dependency files (`requirements.txt`, `package.json`) per project. |
| **Agent Execution** | **Python on Google Cloud Run** | **Serverless & Scalable.** Provides auto-scaling and minimal cost (pay-per-use) for all agent containers. |
| **Agent Intelligence** | **CrewAI / LangChain** | **Iterative Self-Critique.** Frameworks for building sophisticated agent workflows with external API calls. |
| **Content Storage** | **Strapi v5 (Headless CMS)** | **API-First & Structured.** Provides a robust, queryable database for all generated content. Runs locally for dev, containerized for prod. |
| **Oversight Hub Backend** | **Google Cloud Firestore** | **Real-Time Data.** Serves immediate metrics (tasks, financials, agent status) to the React dashboard. |
| **Frontend (Oversight)**| **React (CRA) on Vercel/Netlify** | **Component-Based UI.** Standard React setup for a dynamic, interactive user interface for the control panel. |
| **Frontend (Public)** | **Next.js on Vercel/Netlify** | **High Performance & SEO.** Enables **Static Site Generation (SSG)** for a fast, SEO-friendly public blog and website. |
| **Orchestration/Control**| **Google Cloud Pub/Sub** | **Asynchronous Command Queue.** Central hub for triggering agents from the Oversight Hub without direct coupling. |

---

## **3. Project Structure & Codebases**

- **/agents/content-agent/**: The primary Python-based CrewAI agent responsible for content generation.
- **/cms/strapi-backend/**: The Strapi v5 application, serving as the headless CMS.
- **/web/oversight-hub/**: The React (Create React App) dashboard for monitoring and controlling the agents.
- **/web/public-site/**: The public-facing Next.js website and blog.
- **/cloud-functions/**: (Future) Location for lightweight, serverless functions, e.g., for handling specific triggers.

---

## **4. Execution Timeline (Path to Full Automation)**

| Phase | Duration | Primary Focus | Technical Milestones |
| :--- | :--- | :--- | :--- |
| **Phase I: Foundation & Infra** | Months 1–2 | **Build the Data Backbone** | **Strapi:** Define `Post` and `Author` content types. **Firestore:** Finalize schema for tasks, financials, and logs. **CI/CD:** Set up GitHub Actions and Google Artifact Registry for Docker builds. |
| **Phase II: Agent Intelligence** | Months 3–4 | **Automate Content Pipeline** | **CrewAI:** Implement iterative critique loop, tone enforcement, and metadata generation. **Publishing:** Program agent to push content to **Strapi API** and log results to **Firestore**. |
| **Phase III: Oversight & Control** | Months 5–6 | **Launch the Full Platform** | **Front-End:** Launch **Next.js Public Site** and **React Oversight Hub**. **Control:** Implement the `// INTERVENE` protocol via Pub/Sub. **Metrics:** Hub displays live burn rate and content ROI. |
| **Phase IV: Monetization & Pivot**| Months 7+ | **Scale and Decide** | **Funding:** Utilize the working platform and established metrics to secure external funding. **Next Product:** Use data from the Hub to decide between building a **Unity Game MVP** or a **Game Engine.** |
