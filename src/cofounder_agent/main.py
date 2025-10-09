import sys
import os

# Add the parent directory (src) to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import logging
from cofounder_agent.orchestrator_logic import Orchestrator

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = FastAPI(
    title="Co-Founder Agent API",
    description="API for the AI Co-Founder to manage business operations.",
    version="1.0.0",
)

orchestrator = Orchestrator()

class CommandRequest(BaseModel):
    """Request model for processing a command."""
    command: str

class CommandResponse(BaseModel):
    """Response model for the result of a command."""
    response: str

@app.post("/command", response_model=CommandResponse)
async def process_command(request: CommandRequest):
    """
    Processes a command sent to the Co-Founder agent.

    This endpoint receives a command, delegates it to the orchestrator logic,
    and returns the result.
    """
    try:
        logging.info(f"Received command: {request.command}")
        response = orchestrator.process_command(request.command)
        return CommandResponse(response=response)
    except Exception as e:
        logging.error(f"An error occurred while processing the command: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="An internal error occurred.")

@app.get("/")
async def root():
    """
    Root endpoint to confirm the server is running.
    """
    return {"message": "Co-Founder Agent is running."}
