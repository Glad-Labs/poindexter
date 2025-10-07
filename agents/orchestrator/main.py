import sys
import os
from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn

# Add the project root to the Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

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
