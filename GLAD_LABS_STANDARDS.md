# **GLAD LABS CODE ASSISTANT STANDARDS (MATT GLADDING PROJECT)**

This guide defines the non-negotiable rules for all development work.

## **1\. Architectural Mandates (The Stack)**

* **Platform:** Always prioritize **Google Cloud Native** solutions (Cloud Run, Pub/Sub, Firestore).  
* **Deployment:** All services must be designed for **Serverless Micro-Containers** (Docker/Cloud Run). Do not suggest monolithic server deployments.  
* **Data Structure:** All real-time data must flow through **Firestore**. All content and static structure must be defined in **Strapi schemas**.  
* **Monorepo:** Maintain dependency isolation. Never mix Python (requirements.txt) and Node.js (package.json) dependencies in the same root file.

## **2\. Code Best Practices (Language & Style)**

* **Frontend (React/Next.js):** Use **Functional Components** and modern **Hooks**. Styling must use **Tailwind CSS**. Code must be **responsive** by design.  
* **Backend (Python):** Use **Python 3.11+**. All agents must be built using either **CrewAI** (for workflows) or **LangChain** (for complex RAG/Data Tools).  
* **Testing:** Every core function or agent task must be accompanied by a suggestion for a basic **Unit Test**.

## **3\. Brand & Tone Guidelines**

* **Goal:** Code comments, documentation, and interface text must be **positive, educational, and professionally futuristic.**  
* **Aesthetic Rule:** The "cyberpunk" theme is **subtle and intelligent**. Use advanced technical concepts (e.g., "neural network," "asynchronous data stream") but **strictly forbid** all slang (e.g., "choom," "jack-in," "preem").  
* **Transparency:** When adding new features, suggest **transparent logging** to Firestore so the Oversight Hub can display the agent's work.

## **4\. Optimization Rule (Cost & Speed)**

* All suggested solutions must adhere to the **Zero-Overhead Rule**. Prioritize solutions that scale down to zero and utilize free-tier cloud resources.
