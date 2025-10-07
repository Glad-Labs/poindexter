import sys
import os

# Add the project root to the Python path to resolve import issues
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from fastapi import FastAPI
from pydantic import BaseModel
import logging
import uvicorn

from agents.orchestrator.orchestrator_logic import Orchestrator

app = FastAPI()
orchestrator = Orchestrator()

class ChatMessage(BaseModel):
    message: str

@app.post("/chat")
async def chat(message: ChatMessage):
    """
    This endpoint will receive messages from the Oversight Hub,
    process them with the Orchestrator Agent, and return the AI's response.
    """
    user_message = message.message
    ai_response = orchestrator.process_command(user_message)
    
    return {"response": ai_response}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
