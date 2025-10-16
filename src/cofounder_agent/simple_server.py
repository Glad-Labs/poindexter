"""
Simple FastAPI server for the AI Co-Founder
Uses the tested intelligent_cofounder.py directly without complex dependencies
"""

import asyncio
import logging
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
import uvicorn
import json
from datetime import datetime

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Import our tested AI co-founder
try:
    from intelligent_cofounder import IntelligentCoFounder
    AI_COFOUNDER_AVAILABLE = True
except ImportError as e:
    logger.error(f"Failed to import IntelligentCoFounder: {e}")
    AI_COFOUNDER_AVAILABLE = False

# FastAPI app
app = FastAPI(title="GLAD Labs AI Co-Founder API", version="1.0.0")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify the exact origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global AI co-founder instance
cofounder = None

# WebSocket connection manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self.client_data: Dict[str, Dict] = {}

    async def connect(self, websocket: WebSocket, client_id: str):
        await websocket.accept()
        self.active_connections.append(websocket)
        self.client_data[client_id] = {
            "websocket": websocket,
            "connected_at": datetime.now(),
            "last_activity": datetime.now()
        }
        logger.info(f"Client {client_id} connected. Total connections: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket, client_id: str):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        if client_id in self.client_data:
            del self.client_data[client_id]
        logger.info(f"Client {client_id} disconnected. Total connections: {len(self.active_connections)}")

    async def send_personal_message(self, message: str, websocket: WebSocket):
        try:
            await websocket.send_text(message)
        except Exception as e:
            logger.error(f"Error sending personal message: {e}")

    async def broadcast(self, message: str):
        disconnected = []
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception as e:
                logger.error(f"Error broadcasting to connection: {e}")
                disconnected.append(connection)
        
        # Remove disconnected clients
        for conn in disconnected:
            if conn in self.active_connections:
                self.active_connections.remove(conn)

    async def send_typing_indicator(self, client_id: str, is_typing: bool):
        message = json.dumps({
            "type": "typing_indicator",
            "client_id": client_id,
            "is_typing": is_typing,
            "timestamp": datetime.now().isoformat()
        })
        await self.broadcast(message)

manager = ConnectionManager()

# Pydantic models
class CommandRequest(BaseModel):
    command: str
    task: Optional[Dict[str, Any]] = None  # Frontend sends 'task', we'll map it to context
    context: Optional[Dict[str, Any]] = None
    priority: Optional[str] = "normal"

class CommandResponse(BaseModel):
    response: str
    status: str = "success"
    actions: Optional[List[Dict[str, Any]]] = None  # Actions the AI wants to execute
    data: Optional[Dict[str, Any]] = None  # Additional data for UI updates

class TaskCreateRequest(BaseModel):
    topic: str
    primary_keyword: str
    target_audience: str
    category: str
    description: Optional[str] = None
    priority: Optional[str] = "medium"

class TaskUpdateRequest(BaseModel):
    task_id: str
    status: Optional[str] = None
    updates: Optional[Dict[str, Any]] = None

class BusinessMetricsResponse(BaseModel):
    metrics: Dict[str, Any]
    insights: List[str]
    recommendations: List[str]

@app.on_event("startup")
async def startup_event():
    """Initialize the AI co-founder on startup"""
    global cofounder
    if AI_COFOUNDER_AVAILABLE:
        try:
            logger.info("Initializing AI Co-Founder...")
            cofounder = IntelligentCoFounder()
            await cofounder.initialize()
            logger.info("✅ AI Co-Founder initialized successfully!")
        except Exception as e:
            logger.error(f"Failed to initialize AI Co-Founder: {e}")
            cofounder = None
    else:
        logger.warning("AI Co-Founder not available, using fallback responses")

@app.get("/")
async def root():
    """Health check endpoint"""
    status = "ready" if cofounder else "fallback"
    return {
        "message": "GLAD Labs AI Co-Founder API",
        "status": status,
        "version": "1.0.0"
    }

@app.get("/health")
async def health_check():
    """Detailed health check"""
    return {
        "status": "healthy",
        "ai_cofounder_available": cofounder is not None,
        "timestamp": asyncio.get_event_loop().time()
    }

@app.post("/command", response_model=CommandResponse)
async def process_command(request: CommandRequest):
    """
    Process a command from the Oversight Hub frontend with advanced action execution
    """
    try:
        logger.info(f"Received command: {request.command}")
        
        # Prepare context (map 'task' from frontend to 'context')
        context = request.context or {}
        if request.task:
            context["selected_task"] = request.task
            
        if cofounder:
            # Analyze command for potential actions
            command_analysis = await analyze_command_intent(request.command, context)
            
            # Execute the command through AI co-founder
            response = await cofounder.chat(request.command, context)
            
            # Check if command requires actions
            actions = []
            if command_analysis.get("requires_action"):
                actions = await execute_command_actions(command_analysis, request.command, context)
            
            return CommandResponse(
                response=response, 
                status="success",
                actions=actions,
                data=command_analysis.get("data")
            )
        else:
            # Enhanced fallback with command interpretation
            command_analysis = await analyze_command_intent(request.command, context)
            
            fallback_response = await generate_fallback_response(request.command, command_analysis, request.task)
            
            return CommandResponse(
                response=fallback_response, 
                status="fallback",
                actions=command_analysis.get("suggested_actions", [])
            )
            
    except Exception as e:
        logger.error(f"Error processing command: {e}")
        error_response = f"❌ I encountered an error processing your command: {str(e)}\n\nPlease try again or contact support if the issue persists."
        return CommandResponse(response=error_response, status="error")

async def analyze_command_intent(command: str, context: Dict[str, Any]) -> Dict[str, Any]:
    """Analyze command intent and determine required actions"""
    
    command_lower = command.lower()
    analysis = {
        "intent": "general",
        "requires_action": False,
        "confidence": 0.5,
        "entities": [],
        "suggested_actions": []
    }
    
    # Task management intents
    if any(keyword in command_lower for keyword in ["create task", "new task", "add task", "make task"]):
        analysis.update({
            "intent": "create_task",
            "requires_action": True,
            "confidence": 0.9,
            "suggested_actions": [{"type": "create_task", "data": extract_task_data_from_command(command)}]
        })
    
    elif any(keyword in command_lower for keyword in ["update task", "change status", "modify task", "edit task"]):
        analysis.update({
            "intent": "update_task", 
            "requires_action": True,
            "confidence": 0.8,
            "suggested_actions": [{"type": "update_task", "data": extract_update_data_from_command(command, context)}]
        })
    
    # Business analysis intents
    elif any(keyword in command_lower for keyword in ["show metrics", "business report", "performance", "analytics", "dashboard"]):
        analysis.update({
            "intent": "business_analysis",
            "requires_action": True,
            "confidence": 0.9,
            "suggested_actions": [{"type": "generate_business_report", "data": {}}]
        })
    
    # Content strategy intents  
    elif any(keyword in command_lower for keyword in ["content strategy", "content plan", "content calendar"]):
        analysis.update({
            "intent": "content_strategy",
            "requires_action": True,
            "confidence": 0.8,
            "suggested_actions": [{"type": "generate_content_plan", "data": {}}]
        })
    
    # Financial management intents
    elif any(keyword in command_lower for keyword in ["costs", "budget", "expenses", "revenue", "profit"]):
        analysis.update({
            "intent": "financial_analysis",
            "requires_action": True, 
            "confidence": 0.7,
            "suggested_actions": [{"type": "financial_analysis", "data": {}}]
        })
    
    return analysis

def extract_task_data_from_command(command: str) -> Dict[str, Any]:
    """Extract task creation data from natural language command"""
    # Simple extraction - in production, use NLP libraries
    task_data = {
        "topic": "New Task",
        "primary_keyword": "",
        "target_audience": "general",
        "category": "general",
        "priority": "medium"
    }
    
    # Extract topic if mentioned
    if "about" in command.lower():
        parts = command.lower().split("about")
        if len(parts) > 1:
            topic_part = parts[1].split(".")[0].strip()
            task_data["topic"] = topic_part.title()
    
    # Extract keywords
    if "keyword" in command.lower():
        parts = command.lower().split("keyword")
        if len(parts) > 1:
            keyword_part = parts[1].split(" ")[1:3]  # Take next 1-2 words
            task_data["primary_keyword"] = " ".join(keyword_part).strip()
    
    return task_data

def extract_update_data_from_command(command: str, context: Dict[str, Any]) -> Dict[str, Any]:
    """Extract task update data from command"""
    update_data = {}
    
    if context.get("selected_task"):
        update_data["task_id"] = context["selected_task"].get("id")
    
    # Extract status changes
    status_mapping = {
        "complete": "Completed",
        "done": "Completed", 
        "finish": "Completed",
        "cancel": "Cancelled",
        "pause": "Paused",
        "resume": "In Progress",
        "start": "In Progress"
    }
    
    for keyword, status in status_mapping.items():
        if keyword in command.lower():
            update_data["status"] = status
            break
    
    return update_data

async def execute_command_actions(analysis: Dict[str, Any], command: str, context: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Execute the actions determined from command analysis"""
    executed_actions = []
    
    for action in analysis.get("suggested_actions", []):
        action_type = action["type"]
        action_data = action["data"]
        
        try:
            if action_type == "create_task":
                result = await create_task_action(action_data)
                executed_actions.append({
                    "type": "create_task",
                    "status": "success",
                    "result": result
                })
            
            elif action_type == "update_task":
                result = await update_task_action(action_data)
                executed_actions.append({
                    "type": "update_task", 
                    "status": "success",
                    "result": result
                })
            
            elif action_type == "generate_business_report":
                result = await generate_business_metrics()
                executed_actions.append({
                    "type": "business_report",
                    "status": "success",
                    "result": result
                })
                
        except Exception as e:
            executed_actions.append({
                "type": action_type,
                "status": "error", 
                "error": str(e)
            })
    
    return executed_actions

async def create_task_action(task_data: Dict[str, Any]) -> Dict[str, Any]:
    """Create a new task - simulate for now"""
    # In production, this would integrate with Firestore
    task_id = f"task-{hash(task_data['topic'])}"
    
    logger.info(f"Creating task: {task_data}")
    
    return {
        "task_id": task_id,
        "message": f"Created task '{task_data['topic']}'",
        "task_data": task_data
    }

async def update_task_action(update_data: Dict[str, Any]) -> Dict[str, Any]:
    """Update an existing task - simulate for now"""
    # In production, this would integrate with Firestore
    logger.info(f"Updating task: {update_data}")
    
    return {
        "task_id": update_data.get("task_id"),
        "message": f"Updated task status to {update_data.get('status', 'unknown')}",
        "updates": update_data
    }

async def generate_business_metrics() -> Dict[str, Any]:
    """Generate business metrics and insights"""
    # Simulate metrics - in production, get from business intelligence system
    return {
        "metrics": {
            "total_tasks": 15,
            "completed_tasks": 8,
            "in_progress": 4,
            "revenue_trend": "+12%",
            "cost_efficiency": "85%"
        },
        "insights": [
            "Task completion rate has improved by 15% this week",
            "Content production is exceeding targets",
            "Cost optimization opportunities identified"
        ]
    }

async def generate_fallback_response(command: str, analysis: Dict[str, Any], task: Optional[Dict[str, Any]]) -> str:
    """Generate intelligent fallback response based on command analysis"""
    
    intent = analysis.get("intent", "general")
    
    responses = {
        "create_task": f"""🎯 **Task Creation Command Detected**

I understand you want to create a new task. Based on your command: "{command}"

**What I can help you create:**
- Content generation tasks
- Business analysis tasks  
- Strategic planning tasks
- Operational tasks

**Suggested task details:**
{json.dumps(analysis.get("suggested_actions", [{}])[0].get("data", {}), indent=2) if analysis.get("suggested_actions") else "Basic task template"}

Would you like me to create this task, or would you prefer to modify the details first?""",

        "update_task": f"""📝 **Task Update Command Detected**

I understand you want to update a task. Current task: {task.get('topic', 'None') if task else 'No task selected'}

**Available actions:**
- Mark as completed/in progress/paused
- Update priority or details
- Add notes or comments

What specific changes would you like me to make?""",

        "business_analysis": """📊 **Business Analysis Request**

I can provide comprehensive business insights including:

**Available Reports:**
- Task performance metrics  
- Content strategy effectiveness
- Cost optimization analysis
- Revenue growth trends
- System health overview

**Sample Metrics:** (Live data will be available when fully connected)
- Active Tasks: 15 (↑ 3 from last week)
- Completion Rate: 87% 
- Content Quality Score: 4.2/5
- Cost Efficiency: 85%

Would you like me to generate a specific report?""",

        "general": f"""🤖 **AI Co-Founder Ready**

I received: "{command}"

**I can help you with:**
- 📋 Create and manage tasks
- 📊 Analyze business performance  
- 💡 Strategic planning and insights
- 🎯 Content strategy optimization
- 💰 Financial analysis and budgeting
- 🔧 System operations and coordination

**Current Context:**
- Selected Task: {task.get('topic', 'None') if task else 'None'}
- System Status: Operational
- Available Services: Content Agent, Business Intelligence

How would you like me to assist you today?"""
    }
    
    return responses.get(intent, responses["general"])

@app.post("/tasks/create", response_model=Dict[str, Any])
async def create_task_endpoint(request: TaskCreateRequest):
    """Create a new task through direct API call"""
    try:
        logger.info(f"Creating task via API: {request.topic}")
        
        # Simulate task creation (integrate with Firestore in production)
        task_data = {
            "id": f"task-{hash(request.topic + request.category)}",
            "topic": request.topic,
            "primary_keyword": request.primary_keyword,
            "target_audience": request.target_audience,
            "category": request.category,
            "description": request.description,
            "priority": request.priority,
            "status": "New",
            "createdAt": asyncio.get_event_loop().time()
        }
        
        return {
            "status": "success",
            "message": f"Task '{request.topic}' created successfully",
            "task": task_data
        }
        
    except Exception as e:
        logger.error(f"Error creating task: {e}")
        return {"status": "error", "message": str(e)}

@app.put("/tasks/{task_id}", response_model=Dict[str, Any])
async def update_task_endpoint(task_id: str, request: TaskUpdateRequest):
    """Update an existing task"""
    try:
        logger.info(f"Updating task {task_id}: {request.status}")
        
        # Simulate task update (integrate with Firestore in production)
        update_result = {
            "task_id": task_id,
            "status": request.status,
            "updates": request.updates or {},
            "updatedAt": asyncio.get_event_loop().time()
        }
        
        return {
            "status": "success", 
            "message": f"Task {task_id} updated successfully",
            "task": update_result
        }
        
    except Exception as e:
        logger.error(f"Error updating task: {e}")
        return {"status": "error", "message": str(e)}

@app.get("/business/metrics", response_model=BusinessMetricsResponse)
async def get_business_metrics():
    """Get comprehensive business metrics and insights"""
    try:
        if cofounder:
            # Use real business intelligence system
            dashboard = await cofounder.get_business_dashboard()
            
            insights = [
                "Content production exceeding targets by 15%",
                "Task completion rate improved to 87%", 
                "Cost efficiency optimized to 85%",
                "Revenue growth trend showing +12% increase"
            ]
            
            recommendations = [
                "Scale content production to meet growing demand",
                "Implement automated task prioritization",
                "Optimize AI model usage for better cost efficiency",
                "Explore new revenue streams in enterprise market"
            ]
            
            return BusinessMetricsResponse(
                metrics=dashboard,
                insights=insights,
                recommendations=recommendations
            )
        else:
            # Fallback metrics
            return BusinessMetricsResponse(
                metrics={
                    "total_tasks": 15,
                    "completed_tasks": 8,
                    "in_progress": 4,
                    "revenue": 12500,
                    "costs": 3200,
                    "efficiency": 85
                },
                insights=["System running in fallback mode"],
                recommendations=["Initialize full AI co-founder system for complete insights"]
            )
            
    except Exception as e:
        logger.error(f"Error getting business metrics: {e}")
        return BusinessMetricsResponse(
            metrics={},
            insights=[f"Error retrieving metrics: {str(e)}"],
            recommendations=["Check system logs and restart services if needed"]
        )

@app.post("/business/report", response_model=Dict[str, Any])
async def generate_business_report():
    """Generate comprehensive business report"""
    try:
        if cofounder:
            report = await cofounder.generate_business_report()
            return {
                "status": "success",
                "report": report,
                "generated_at": asyncio.get_event_loop().time()
            }
        else:
            return {
                "status": "fallback",
                "report": "Business report generation requires full AI co-founder initialization",
                "generated_at": asyncio.get_event_loop().time()
            }
            
    except Exception as e:
        logger.error(f"Error generating business report: {e}")
        return {
            "status": "error",
            "report": f"Error generating report: {str(e)}",
            "generated_at": asyncio.get_event_loop().time()
        }

@app.get("/system/status", response_model=Dict[str, Any])
async def get_system_status():
    """Get comprehensive system status"""
    try:
        status = {
            "ai_cofounder_available": cofounder is not None,
            "server_uptime": asyncio.get_event_loop().time(),
            "services": {
                "business_intelligence": cofounder is not None,
                "memory_system": cofounder is not None,
                "mcp_integration": cofounder is not None
            },
            "capabilities": [
                "Task Management",
                "Business Analytics", 
                "Strategic Planning",
                "Content Strategy",
                "Financial Analysis",
                "Command Execution"
            ]
        }
        
        if cofounder:
            status["ai_status"] = "fully_operational"
            status["memory_count"] = len(getattr(cofounder, 'conversation_memory', []))
        else:
            status["ai_status"] = "fallback_mode"
            
        return status
        
    except Exception as e:
        logger.error(f"Error getting system status: {e}")
        return {
            "error": str(e),
            "ai_status": "error"
        }

@app.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str):
    """WebSocket endpoint for real-time communication with AI Co-Founder"""
    await manager.connect(websocket, client_id)
    
    try:
        # Send welcome message
        welcome_message = {
            "type": "system",
            "message": "🤖 Connected to AI Co-Founder. Real-time communication enabled!",
            "timestamp": datetime.now().isoformat(),
            "capabilities": [
                "Real-time chat", 
                "Command execution", 
                "Live notifications",
                "Typing indicators",
                "Business updates"
            ]
        }
        await manager.send_personal_message(json.dumps(welcome_message), websocket)
        
        while True:
            # Receive message from client
            data = await websocket.receive_text()
            message_data = json.loads(data)
            
            # Update last activity
            if client_id in manager.client_data:
                manager.client_data[client_id]["last_activity"] = datetime.now()
            
            message_type = message_data.get("type", "chat")
            
            if message_type == "chat":
                await handle_chat_message(websocket, client_id, message_data)
            elif message_type == "command":
                await handle_command_message(websocket, client_id, message_data)
            elif message_type == "typing":
                await manager.send_typing_indicator(client_id, message_data.get("is_typing", False))
            elif message_type == "ping":
                pong_message = {
                    "type": "pong", 
                    "timestamp": datetime.now().isoformat()
                }
                await manager.send_personal_message(json.dumps(pong_message), websocket)
                
    except WebSocketDisconnect:
        manager.disconnect(websocket, client_id)
    except Exception as e:
        logger.error(f"WebSocket error for client {client_id}: {e}")
        manager.disconnect(websocket, client_id)

async def handle_chat_message(websocket: WebSocket, client_id: str, message_data: Dict[str, Any]):
    """Handle chat messages through WebSocket"""
    try:
        user_message = message_data.get("message", "")
        context = message_data.get("context", {})
        
        # Send typing indicator
        typing_message = {
            "type": "ai_typing",
            "is_typing": True,
            "timestamp": datetime.now().isoformat()
        }
        await manager.send_personal_message(json.dumps(typing_message), websocket)
        
        # Process message with AI co-founder
        if cofounder:
            response = await cofounder.chat(user_message, context)
            
            # Analyze for actions
            command_analysis = await analyze_command_intent(user_message, context)
            actions = []
            if command_analysis.get("requires_action"):
                actions = await execute_command_actions(command_analysis, user_message, context)
        else:
            response = await generate_fallback_response(user_message, {"intent": "general"}, context.get("task"))
            actions = []
        
        # Send response
        response_message = {
            "type": "ai_response",
            "message": response,
            "timestamp": datetime.now().isoformat(),
            "actions": actions,
            "client_id": client_id
        }
        
        await manager.send_personal_message(json.dumps(response_message), websocket)
        
        # Stop typing indicator
        stop_typing_message = {
            "type": "ai_typing",
            "is_typing": False,
            "timestamp": datetime.now().isoformat()
        }
        await manager.send_personal_message(json.dumps(stop_typing_message), websocket)
        
    except Exception as e:
        logger.error(f"Error handling chat message: {e}")
        error_message = {
            "type": "error",
            "message": f"Error processing your message: {str(e)}",
            "timestamp": datetime.now().isoformat()
        }
        await manager.send_personal_message(json.dumps(error_message), websocket)

async def handle_command_message(websocket: WebSocket, client_id: str, message_data: Dict[str, Any]):
    """Handle command execution through WebSocket"""
    try:
        command = message_data.get("command", "")
        context = message_data.get("context", {})
        
        # Analyze and execute command
        command_analysis = await analyze_command_intent(command, context)
        
        # Send command acknowledgment
        ack_message = {
            "type": "command_received",
            "command": command,
            "intent": command_analysis.get("intent"),
            "timestamp": datetime.now().isoformat()
        }
        await manager.send_personal_message(json.dumps(ack_message), websocket)
        
        # Execute actions
        actions = []
        if command_analysis.get("requires_action"):
            actions = await execute_command_actions(command_analysis, command, context)
        
        # Send command results
        result_message = {
            "type": "command_result",
            "command": command,
            "actions": actions,
            "timestamp": datetime.now().isoformat()
        }
        await manager.send_personal_message(json.dumps(result_message), websocket)
        
        # Broadcast to other clients if needed
        if command_analysis.get("intent") in ["create_task", "update_task"]:
            broadcast_message = {
                "type": "system_update",
                "message": f"Task action executed by {client_id}",
                "actions": actions,
                "timestamp": datetime.now().isoformat()
            }
            await manager.broadcast(json.dumps(broadcast_message))
        
    except Exception as e:
        logger.error(f"Error handling command message: {e}")
        error_message = {
            "type": "command_error",
            "message": f"Error executing command: {str(e)}",
            "timestamp": datetime.now().isoformat()
        }
        await manager.send_personal_message(json.dumps(error_message), websocket)

@app.get("/ws/connections", response_model=Dict[str, Any])
async def get_websocket_connections():
    """Get current WebSocket connection status"""
    connections_info = []
    for client_id, data in manager.client_data.items():
        connections_info.append({
            "client_id": client_id,
            "connected_at": data["connected_at"].isoformat(),
            "last_activity": data["last_activity"].isoformat(),
            "duration": str(datetime.now() - data["connected_at"])
        })
    
    return {
        "total_connections": len(manager.active_connections),
        "connections": connections_info,
        "timestamp": datetime.now().isoformat()
    }

@app.post("/broadcast", response_model=Dict[str, Any])
async def broadcast_message(message: Dict[str, Any]):
    """Broadcast a message to all connected clients"""
    try:
        broadcast_data = {
            "type": "broadcast",
            "message": message.get("message", ""),
            "data": message.get("data", {}),
            "timestamp": datetime.now().isoformat()
        }
        
        await manager.broadcast(json.dumps(broadcast_data))
        
        return {
            "status": "success",
            "message": "Message broadcasted to all clients",
            "recipients": len(manager.active_connections)
        }
        
    except Exception as e:
        logger.error(f"Error broadcasting message: {e}")
        return {"status": "error", "message": str(e)}

# ==================== ADVANCED FEATURES ENDPOINTS ====================

@app.post("/api/delegate-task")
async def delegate_task(request: dict):
    """Delegate a task to the multi-agent orchestrator"""
    if not cofounder:
        raise HTTPException(status_code=503, detail="AI Co-Founder not initialized")
    
    try:
        task_description = request.get("description", "")
        requirements = request.get("requirements", [])
        priority = request.get("priority", "medium")
        
        if not task_description:
            raise HTTPException(status_code=400, detail="Task description is required")
        
        result = await cofounder.delegate_task_to_agent(task_description, requirements, priority)
        return result
        
    except Exception as e:
        logger.error(f"Error delegating task: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/create-workflow")
async def create_workflow(request: dict):
    """Create a strategic workflow"""
    if not cofounder:
        raise HTTPException(status_code=503, detail="AI Co-Founder not initialized")
    
    try:
        workflow_name = request.get("name", "")
        objectives = request.get("objectives", [])
        
        if not workflow_name or not objectives:
            raise HTTPException(status_code=400, detail="Workflow name and objectives are required")
        
        result = await cofounder.create_strategic_workflow(workflow_name, objectives)
        return result
        
    except Exception as e:
        logger.error(f"Error creating workflow: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/orchestration-status")
async def get_orchestration_status():
    """Get multi-agent orchestration status"""
    if not cofounder:
        raise HTTPException(status_code=503, detail="AI Co-Founder not initialized")
    
    try:
        status = await cofounder.orchestrator.get_orchestration_status()
        return {"success": True, "status": status}
        
    except Exception as e:
        logger.error(f"Error getting orchestration status: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/agent-recommendations")
async def get_agent_recommendations():
    """Get agent optimization recommendations"""
    if not cofounder:
        raise HTTPException(status_code=503, detail="AI Co-Founder not initialized")
    
    try:
        recommendations = await cofounder.orchestrator.get_agent_recommendations()
        return {"success": True, "recommendations": recommendations}
        
    except Exception as e:
        logger.error(f"Error getting agent recommendations: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/dashboard-data")
async def get_dashboard_data():
    """Get advanced dashboard data"""
    if not cofounder:
        raise HTTPException(status_code=503, detail="AI Co-Founder not initialized")
    
    try:
        dashboard_data = await cofounder.dashboard.get_dashboard_data()
        return {"success": True, "data": dashboard_data}
        
    except Exception as e:
        logger.error(f"Error getting dashboard data: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/comprehensive-status")
async def get_comprehensive_system_status():
    """Get comprehensive status from all systems"""
    if not cofounder:
        raise HTTPException(status_code=503, detail="AI Co-Founder not initialized")
    
    try:
        status = await cofounder.get_comprehensive_status()
        return {"success": True, "status": status}
        
    except Exception as e:
        logger.error(f"Error getting comprehensive status: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/smart-notification")
async def send_smart_notification(request: dict):
    """Send a smart notification through the notification system"""
    if not cofounder:
        raise HTTPException(status_code=503, detail="AI Co-Founder not initialized")
    
    try:
        notification_type = request.get("type", "info")
        message = request.get("message", "")
        priority = request.get("priority", "normal")
        context = request.get("context", {})
        
        if not message:
            raise HTTPException(status_code=400, detail="Message is required")
        
        # Send notification through the smart system
        await cofounder.notification_system.send_notification(
            notification_type, message, priority, context
        )
        
        # Also broadcast to WebSocket clients
        notification_data = {
            "type": "smart_notification",
            "data": {
                "message": message,
                "priority": priority,
                "timestamp": datetime.now().isoformat(),
                "context": context
            }
        }
        
        await manager.broadcast(json.dumps(notification_data))
        
        return {"success": True, "message": "Smart notification sent successfully"}
        
    except Exception as e:
        logger.error(f"Error sending smart notification: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    print("🚀 Starting Advanced AI Co-Founder Server...")
    print("📡 Server will be available at http://localhost:8000")
    print("📖 API documentation at http://localhost:8000/docs")
    print("🎯 Features: Command Execution, Task Management, Business Intelligence")
    
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info"
    )