# **GLAD LABS: AI FRONTIER FIRM MASTER PLAN V3.0**

**Project Name:** Glad Labs, LLC  
**Owner/Manager:** Matthew M. Gladding

---

## **Part I: Strategic Overview**

### **1. Core Mission & Vision**

To operate the most efficient, automated, solo-founded digital firm, demonstrating a scalable model for the future of specialized business by fusing high-quality content creation with an intelligent, conversational **AI Business Assistant** that manages all operations.

### **2. Strategic Pillars & Brand**

| Pillar             | Focus                             | Goal                                                                                                                             |
| :----------------- | :-------------------------------- | :------------------------------------------------------------------------------------------------------------------------------- |
| **Core Product**   | **Intelligent Automation (SaaS)** | The marketable product is the **AI Agent System** itself. This pivots the business model to scalable B2B services (future SaaS). |
| **Content Engine** | **High-Fidelity Content**         | Consistently generate sophisticated, on-brand content that builds a community and drives traffic for future monetization.        |
| **Technology**     | **Serverless Scalability**        | Utilize a cost-effective, Google-Native stack to eliminate manual maintenance and pay only for usage, ensuring maximum runway.   |

**Brand Tone Mandate:** **Positive, Educational, and Authentically Futuristic.** The tone must be intelligent and empowering, strictly forbidding all cyberpunk slang (e.g., "choom," "jack-in," "preem"). The aesthetic is conveyed only through technical analogy (e.g., "neural network," "asynchronous data stream").

---

## **Part II: Technical Blueprint**

### **3. System Architecture (The Google-Native Stack)**

The entire system is built on a monorepo structure, with a central Orchestrator Agent managing a fleet of specialized, serverless agents.

| Component                | Technology                         | Best Practice & Rationale                                                                                                                                                                    |
| :----------------------- | :--------------------------------- | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Monorepo**             | **GitLab (glad-labs-website)**     | Centralizes all code (Agents, CMS, Web) using isolated dependency files (`requirements.txt`, `package.json`) per project.                                                                    |
| **Orchestrator Agent**   | **Python (LangChain/CrewAI)**      | **The AI Business Assistant.** The central brain of the firm. Manages all other agents, maintains the content calendar, and exposes a conversational API for the Oversight Hub.              |
| **Specialized Agents**   | **Python on Google Cloud Run**     | **Serverless & Scalable.** Provides auto-scaling and minimal cost (pay-per-use) for all agent containers (Content, Financial, etc.), dispatched by the Orchestrator.                         |
| **Content Storage**      | **Strapi v4 (v5 Upgrade Planned)** | **API-First & Structured.** Provides a robust, queryable database for all generated content. Runs locally for dev, containerized for prod.                                                   |
| **Operational Database** | **Google Cloud Firestore**         | **Real-Time Data.** The primary database for the Orchestrator. Stores the content calendar, agent logs, financial records, and serves real-time data to the Oversight Hub's Data Pane.       |
| **Frontend (Oversight)** | **React (CRA) on Vercel/Netlify**  | **Conversational Command Center.** A dual-pane UI. The **Data Pane** provides a real-time view of Firestore/Strapi data. The **Command Pane** is a chat interface to the Orchestrator Agent. |
| **Frontend (Public)**    | **Next.js on Vercel/Netlify**      | **High Performance & SEO.** Enables **Static Site Generation (SSG)** for a fast, SEO-friendly public blog and website.                                                                       |
| **Agent Communication**  | **Google Cloud Pub/Sub**           | **Asynchronous Command Bus.** The nervous system. The Orchestrator uses Pub/Sub to dispatch tasks to specialized agents and receive completion notifications.                                |

### **4. Project Structure & Codebases**

- **/agents/orchestrator/**: (New) The central AI Business Assistant.
- **/agents/content-agent/**: The specialized agent responsible for content generation.
- **/cms/strapi-backend/**: The Strapi v4 application, serving as the headless CMS.
- **/web/oversight-hub/**: The React dashboard for monitoring and controlling the Orchestrator.
- **/web/public-site/**: The public-facing Next.js website and blog.
- **/cloud-functions/**: Lightweight, serverless functions for specific triggers.

---

## **Part III: Execution & Operations**

### **5. Development Roadmap (Path to Full Automation)**

| Phase                                     | Timeline     | Primary Focus                        | Key Milestones                                                                                                                                                                                                       |
| :---------------------------------------- | :----------- | :----------------------------------- | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Phase I: Foundation & Core Automation** | **Complete** | **Establish Core Pipeline**          | **✓** Strapi CMS & Public Site established. **✓** Firestore schemas defined. **✓** Content Agent MVP developed. **✓** Oversight Hub v1 for monitoring and control implemented.                                       |
| **Phase II: The Command Center**          | **Complete** | **Build the Conversational UI**      | **✓ 2.1:** Develop the Orchestrator Agent's conversational API (e.g., using FastAPI). **✓ 2.2:** Implement the dual-pane UI in the Oversight Hub. **✓ 2.3:** Integrate the Hub's Command Pane with the Orchestrator. |
| **Phase III: Financial Integration**      | **Complete** | **Automate Financial Oversight**     | **✓ 3.1:** Develop the Financial Agent. **✓ 3.2:** Integrate with **Mercury Bank API** and GCP Billing for data ingestion. **✓ 3.3:** Enhance Orchestrator to process financial queries.                             |
| **Phase IV: Market Intelligence**         | **Complete** | **Enable Proactive Strategy**        | **✓ 4.1:** Develop the Market Insight Agent. **✓ 4.2:** Integrate with external SEO and trend APIs. **✓ 4.3:** Enhance Orchestrator to provide proactive content suggestions.                                        |
| **Phase V: Full Autonomy & Monetization** | **Complete** | **Achieve Full Business Automation** | **✓ 5.1:** Develop the Compliance Agent. **✓ 5.2:** Achieve full conversational control over the entire business workflow. **✓ 5.3:** Package the agent system for a potential SaaS offering.                        |

### **6. The Orchestrator Protocol & Agent Roles**

The Orchestrator Agent is the single point of contact for all operations. The user interacts with it via the chat interface in the Oversight Hub. The Orchestrator then delegates tasks to the appropriate specialized agent.

**Example Conversational Workflow:**

1.  **User:** `"Show me the content calendar for next month."`
    - **Orchestrator Action:** Queries Firestore and displays the relevant tasks in the Data Pane.
2.  **User:** `"Suggest three new topics about AI in game design."`
    - **Orchestrator Action:** Dispatches a job to the **Market Insight Agent**. The agent performs its analysis and returns the results. The Orchestrator presents the suggestions in the chat.
3.  **User:** `"I like option 2. Flesh it out for a technical audience, target keyword 'procedural generation', and schedule it for next Friday."`
    - **Orchestrator Action:** Creates a new task document in the Firestore content calendar with all the specified details and sets its status to "Ready".
4.  **User:** `"What was our cloud spend last week? And what's our current balance in Mercury?"`
    - **Orchestrator Action:** Queries the **Financial Agent**, which in turn queries its data from Firestore (GCP Billing) and the **Mercury Bank API**, and returns the answer directly in the chat.
5.  **User:** `"Execute all 'Ready' tasks in the content calendar."`
    - **Orchestrator Action:** Finds all relevant tasks and dispatches them to the **Content Agent** via Pub/Sub, updating the UI in real-time as they progress.

| Agent Name               | Core Responsibility                                         | Primary Data Source                              | Triggered By                               |
| :----------------------- | :---------------------------------------------------------- | :----------------------------------------------- | :----------------------------------------- |
| **Orchestrator Agent**   | Manages all agents, data, and user interaction.             | Firestore, All Agent Data                        | User Chat Command                          |
| **Content Agent**        | Content generation, refinement, and publishing.             | Strapi, Firestore                                | Orchestrator Command (via Pub/Sub)         |
| **Financial Agent**      | Accounting, expense logging, and burn rate calculation.     | Firestore, **Mercury Bank API**, GCP Billing API | Orchestrator Command / System Event        |
| **Market Insight Agent** | Monitors trends; generates relevant, popular topic options. | External APIs                                    | Orchestrator Command                       |
| **Compliance Agent**     | Reviews code for security best practices; flags risks.      | Codebase, Deployment Logs                        | Orchestrator Command (Pre-deployment Hook) |

### **7. Engineering Standards & Best Practices**

This section codifies the technical standards and operational protocols for the GLAD Labs codebase. Adherence to these standards is mandatory to ensure maintainability, scalability, and security.

#### **A. Code Quality & Style Guides**

| Language / Framework | Linter / Formatter        | Configuration Standard                                                           | Rationale                                                                           |
| :------------------- | :------------------------ | :------------------------------------------------------------------------------- | :---------------------------------------------------------------------------------- |
| **Python**           | **Black** & **Flake8**    | PEP 8 compliant, with strict line length (88 chars).                             | Enforces consistency and readability across all Python services.                    |
| **JavaScript/React** | **ESLint** & **Prettier** | Standard rulesets (e.g., `eslint-config-react-app`, `prettier-config-standard`). | Prevents common errors and maintains a uniform code style in frontend applications. |
| **Markdown**         | **markdownlint**          | Standard configuration.                                                          | Ensures all documentation is clean, readable, and consistent.                       |

- **Self-Documenting Code:** Variables, functions, and classes must have clear, descriptive names. Complex logic must be accompanied by concise, explanatory comments.
- **Modularity:** Code must be organized into small, single-responsibility modules or functions to maximize reusability and ease of testing.

#### **B. Testing Strategy**

A multi-layered testing approach is required to ensure system reliability.

| Test Type                  | Scope                           | Implementation                                         | Goal                                                                                                                              |
| :------------------------- | :------------------------------ | :----------------------------------------------------- | :-------------------------------------------------------------------------------------------------------------------------------- |
| **Unit Tests**             | Individual functions/components | **Pytest** (Python), **React Testing Library** (React) | Verify that the smallest units of code work as expected in isolation.                                                             |
| **Integration Tests**      | Service-to-service interaction  | Mocked API calls, database interactions                | Ensure that different parts of the system (e.g., Agent to Strapi) communicate correctly.                                          |
| **End-to-End (E2E) Tests** | Full user/system workflow       | **Cypress** or **Playwright** (Future)                 | Simulate a full process (e.g., creating a task in the Hub and verifying a post in the Public Site) to validate the entire system. |

#### **C. API & Data Integrity**

- **Schema-First Approach:** All data models (Strapi Content Types, Firestore collections, Pydantic models) must be explicitly defined before implementation. This prevents data inconsistencies.
- **Idempotent APIs:** All `POST` or `PUT` operations should be idempotent where possible, meaning multiple identical requests have the same effect as a single one.
- **Error Handling:** APIs must return clear, standardized error messages and appropriate HTTP status codes (e.g., `400` for bad requests, `401` for unauthorized, `500` for server errors).

#### **D. Security Mandates**

- **No Secrets in Code:** All API keys, credentials, and sensitive configuration must be loaded from environment variables (`.env` files) or a dedicated secret manager (e.g., Google Secret Manager). **Secrets must never be committed to Git.**
- **Principle of Least Privilege:** API tokens and service account permissions must be scoped to the minimum required access level. For example, a token for the Public Site should be read-only.
- **Dependency Audits:** Regularly run `npm audit` and `pip-audit` to identify and patch vulnerabilities in third-party packages.

#### **E. Git Workflow & CI/CD**

- **Branching Model:** A simplified **GitFlow** model will be used:
  - `main`: Production-ready, deployable code.
  - `develop`: The primary integration branch for new features.
  - `feature/<feature-name>`: Branches for all new development, created from `develop`.
- **Pull Requests (PRs):** All code must be merged into `develop` via a Pull Request. PRs require at least one review (even if self-reviewed for solo work) and must pass all automated checks (linting, testing).
- **CI/CD:** Automated pipelines will be configured in **GitLab CI/CD** to run tests, security scans, and deployments.

#### **F. Logging & Monitoring**

- **Structured Logging:** All services must produce structured logs (JSON format) containing a timestamp, severity level (INFO, WARN, ERROR), and a clear message. This is critical for effective analysis in Google Cloud Logging.
- **Centralized Monitoring:** The **Oversight Hub** is the primary tool for real-time monitoring of agent status. For deeper infrastructure monitoring, **Google Cloud Monitoring** will be used to track resource usage and set up alerts.
