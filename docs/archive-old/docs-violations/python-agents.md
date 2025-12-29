# Python Agents Instructions

- **Primary Language:** Python 3.12
- **Key Libraries:** FastAPI, Uvicorn, Google Cloud client libraries, LangChain/CrewAI.
- **Virtual Environment:** All Python scripts must be run within the virtual environment located at the root of the project (`.venv`).
- **Execution:** Python servers should be run from the **root of the workspace** to ensure correct module resolution (e.g., `uvicorn cofounder_agent.main:app`).
- **Style:** All Python code must adhere to PEP 8 and be formatted with `black`.
- **Logging:** Use the structured logging configuration defined in `utils/logging_config.py`.
