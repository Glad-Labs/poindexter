# **GLAD Labs: Autonomous AI Content Agent v1.0**

This application is a fully autonomous AI agent designed to create and publish high-quality blog posts. It runs within a Docker container, managing the entire content lifecycle from idea to publication without human intervention, based on tasks triggered via Google Cloud Pub/Sub.

---

## **1. High-Level Architecture**

The system is orchestrated by `orchestrator.py`, which listens for messages on a Pub/Sub topic. Upon receiving a task, it coordinates a workflow through a series of specialized agents built with CrewAI.

- **`orchestrator.py`**: The main control unit. It initializes the agent crew and kicks off the content generation process upon receiving a trigger.
- **`config.py`**: Contains configuration settings for the agent, such as API keys and other parameters.
- **`create_task.py`**: A script to manually create tasks for the content agent.
- **`prompts.json`**: Stores the prompts used by the AI agents.
- **`Dockerfile`**: Defines the Docker container for the agent.

- **Specialized Agents (`agents/`)**: A crew of AI agents, each with a specific role in the content creation pipeline.

  - **`CreativeAgent`**: Generates the core text content and identifies strategic opportunities for images.
  - **`ImageAgent`**: Creates relevant images based on prompts from the `CreativeAgent`.
  - **`EditingAgent`**: Cleans, formats, and refines the generated content, ensuring it adheres to brand tone and style guidelines.
  - **`QAAgent`**: Performs a final quality assurance check on the content and formatting.
  - **`PublishingAgent`**: Publishes the final, approved content and images to the Strapi CMS.

- **Services (`services/`)**: Client classes that handle all communication with external APIs and platforms.

  - **`firestore_client.py`**: Logs task status, errors, and performance metrics to Google Cloud Firestore.
  - **`llm_client.py`**: Communicates with the chosen Large Language Model API (e.g., Google Gemini).
  - **`image_gen_client.py`**: Communicates with an image generation model (e.g., Stable Diffusion, Pexels API).
  - **`strapi_client.py`**: Communicates with the Strapi API to publish posts and upload media.

- **Utilities (`utils/`)**:
  - **`data_models.py`**: Defines the central Pydantic models (e.g., `BlogPost`) used to pass structured data between agents.
  - **`logging_config.py`**: Configures application-wide structured logging.
  - **`tools.py`**: Defines custom tools available to the agents (e.g., web search, reading files).

---

## **2. Workflow**

1. A message is published to a Google Cloud Pub/Sub topic, triggering the agent. This can be done manually from the Oversight Hub, on a schedule, or via an API call.
2. The `orchestrator.py` script, running in a Cloud Run container, receives the message.
3. It initializes the CrewAI crew, assigning the defined tasks to the specialized agents.
4. The `CreativeAgent` drafts the article.
5. The `ImageAgent` generates or sources images.
6. The `EditingAgent` and `QAAgent` refine the post.
7. The `PublishingAgent` sends the final content to the Strapi CMS via the `strapi_client`.
8. Throughout the process, agents use the `firestore_client` to log progress and outcomes to the `tasks` and `agent_logs` collections in Firestore.
9. If the pipeline completes successfully, the final status is logged as "completed". If any agent fails, the status is updated to "failed" with detailed error information.

---

## **3. Setup and Running**

For detailed instructions on how to set up the environment and run the agent, please refer to the main [project README.md](../../../README.md).

---

## **4. Future Enhancements (TODO)**

- **Content Update/Refresh**: Add a new agent or task to periodically review and update existing articles.
- **Vector DB for Memory**: Integrate a vector database (e.g., Pinecone, or Firestore's vector search) to provide agents with long-term memory of previously generated content, avoiding repetition and enabling context-aware linking.
- **Advanced Image Strategy**: Implement more sophisticated image placement logic, potentially using different image sizes (thumbnail, hero, inline) based on context.
- **Internal Linking**: Develop a tool for agents to search the Strapi CMS for existing articles and intelligently insert internal links.
- **Cost Tracking**: Enhance the `firestore_client` to log the token usage and cost of each API call to the `financials` collection for real-time budget monitoring.
