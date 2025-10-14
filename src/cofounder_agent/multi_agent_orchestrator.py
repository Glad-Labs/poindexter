"""
Multi-Agent Orchestration System
Coordinate and manage multiple AI agents for complex business tasks
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Callable, Union
from dataclasses import dataclass, field, asdict
from enum import Enum
import json
import uuid

# Set up logging
logger = logging.getLogger(__name__)

class AgentStatus(Enum):
    """Agent status enumeration"""
    IDLE = "idle"
    BUSY = "busy"
    ERROR = "error"
    OFFLINE = "offline"

class TaskPriority(Enum):
    """Task priority levels"""
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4

class TaskStatus(Enum):
    """Task status enumeration"""
    PENDING = "pending"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

@dataclass
class AgentCapability:
    """Agent capability definition"""
    name: str
    description: str
    input_types: List[str]
    output_types: List[str]
    estimated_duration: float  # minutes
    success_rate: float
    cost_estimate: float

@dataclass
class Agent:
    """Individual agent representation"""
    id: str
    name: str
    type: str
    capabilities: List[AgentCapability]
    status: AgentStatus = AgentStatus.IDLE
    current_task_id: Optional[str] = None
    performance_metrics: Dict[str, Any] = field(default_factory=dict)
    last_activity: Optional[datetime] = None
    load_percentage: float = 0.0
    
    def can_handle_task(self, task_requirements: List[str]) -> bool:
        """Check if agent can handle task requirements"""
        agent_capabilities = [cap.name for cap in self.capabilities]
        return any(req in agent_capabilities for req in task_requirements)

@dataclass
class OrchestrationTask:
    """Task for multi-agent orchestration"""
    id: str
    name: str
    description: str
    requirements: List[str]
    priority: TaskPriority
    status: TaskStatus = TaskStatus.PENDING
    assigned_agent_id: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    estimated_duration: Optional[float] = None
    actual_duration: Optional[float] = None
    input_data: Dict[str, Any] = field(default_factory=dict)
    output_data: Dict[str, Any] = field(default_factory=dict)
    dependencies: List[str] = field(default_factory=list)
    progress_percentage: float = 0.0
    error_message: Optional[str] = None
    retries: int = 0
    max_retries: int = 3

class MultiAgentOrchestrator:
    """Orchestrate and coordinate multiple AI agents"""
    
    def __init__(self):
        self.agents: Dict[str, Agent] = {}
        self.tasks: Dict[str, OrchestrationTask] = {}
        self.task_queue: List[str] = []
        self.completed_tasks: List[str] = []
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        self.orchestration_metrics = {
            "total_tasks": 0,
            "completed_tasks": 0,
            "failed_tasks": 0,
            "average_completion_time": 0.0,
            "agent_utilization": 0.0,
            "success_rate": 0.0
        }
        
        # Initialize with default agents
        self._initialize_default_agents()
        
        # Start orchestration loop
        self.orchestration_active = True
        asyncio.create_task(self._orchestration_loop())
    
    def _initialize_default_agents(self):
        """Initialize default AI agents"""
        
        # Content Creation Agent
        content_agent = Agent(
            id="content-001",
            name="Content Creator",
            type="content_creation",
            capabilities=[
                AgentCapability(
                    name="blog_writing",
                    description="Create engaging blog posts and articles",
                    input_types=["topic", "keywords", "target_audience"],
                    output_types=["article", "metadata"],
                    estimated_duration=45.0,
                    success_rate=0.92,
                    cost_estimate=15.0
                ),
                AgentCapability(
                    name="social_media_content",
                    description="Generate social media posts and campaigns",
                    input_types=["topic", "platform", "tone"],
                    output_types=["posts", "hashtags"],
                    estimated_duration=15.0,
                    success_rate=0.95,
                    cost_estimate=5.0
                ),
                AgentCapability(
                    name="content_optimization",
                    description="Optimize content for SEO and engagement",
                    input_types=["content", "keywords", "target_metrics"],
                    output_types=["optimized_content", "seo_analysis"],
                    estimated_duration=20.0,
                    success_rate=0.88,
                    cost_estimate=8.0
                )
            ],
            performance_metrics={
                "tasks_completed": 156,
                "average_rating": 4.3,
                "success_rate": 0.92
            }
        )
        
        # Research Agent
        research_agent = Agent(
            id="research-001",
            name="Market Researcher",
            type="research",
            capabilities=[
                AgentCapability(
                    name="market_analysis",
                    description="Conduct comprehensive market research",
                    input_types=["industry", "competitors", "timeframe"],
                    output_types=["market_report", "trends", "opportunities"],
                    estimated_duration=120.0,
                    success_rate=0.89,
                    cost_estimate=35.0
                ),
                AgentCapability(
                    name="competitor_analysis",
                    description="Analyze competitor strategies and positioning",
                    input_types=["competitors", "metrics", "timeframe"],
                    output_types=["competitor_report", "benchmarks"],
                    estimated_duration=90.0,
                    success_rate=0.91,
                    cost_estimate=25.0
                ),
                AgentCapability(
                    name="trend_identification",
                    description="Identify emerging trends and opportunities",
                    input_types=["industry", "data_sources", "timeframe"],
                    output_types=["trend_report", "predictions"],
                    estimated_duration=60.0,
                    success_rate=0.87,
                    cost_estimate=20.0
                )
            ],
            performance_metrics={
                "tasks_completed": 89,
                "average_rating": 4.1,
                "success_rate": 0.89
            }
        )
        
        # Data Analysis Agent
        analysis_agent = Agent(
            id="analysis-001", 
            name="Data Analyst",
            type="data_analysis",
            capabilities=[
                AgentCapability(
                    name="business_intelligence",
                    description="Generate business intelligence reports",
                    input_types=["data", "metrics", "timeframe"],
                    output_types=["dashboard", "insights", "recommendations"],
                    estimated_duration=75.0,
                    success_rate=0.94,
                    cost_estimate=30.0
                ),
                AgentCapability(
                    name="predictive_modeling",
                    description="Create predictive models and forecasts",
                    input_types=["historical_data", "variables", "horizon"],
                    output_types=["model", "predictions", "confidence_intervals"],
                    estimated_duration=180.0,
                    success_rate=0.85,
                    cost_estimate=50.0
                ),
                AgentCapability(
                    name="data_visualization",
                    description="Create compelling data visualizations",
                    input_types=["data", "chart_types", "audience"],
                    output_types=["charts", "dashboards", "infographics"],
                    estimated_duration=30.0,
                    success_rate=0.96,
                    cost_estimate=12.0
                )
            ],
            performance_metrics={
                "tasks_completed": 203,
                "average_rating": 4.5,
                "success_rate": 0.94
            }
        )
        
        # Strategy Agent
        strategy_agent = Agent(
            id="strategy-001",
            name="Strategic Planner",
            type="strategic_planning",
            capabilities=[
                AgentCapability(
                    name="business_planning",
                    description="Develop comprehensive business plans",
                    input_types=["goals", "resources", "constraints", "timeframe"],
                    output_types=["business_plan", "roadmap", "milestones"],
                    estimated_duration=240.0,
                    success_rate=0.88,
                    cost_estimate=75.0
                ),
                AgentCapability(
                    name="process_optimization",
                    description="Optimize business processes and workflows",
                    input_types=["current_process", "objectives", "constraints"],
                    output_types=["optimized_process", "efficiency_gains", "implementation_plan"],
                    estimated_duration=150.0,
                    success_rate=0.91,
                    cost_estimate=45.0
                ),
                AgentCapability(
                    name="risk_assessment",
                    description="Assess and mitigate business risks",
                    input_types=["business_context", "risk_factors", "impact_levels"],
                    output_types=["risk_matrix", "mitigation_strategies", "monitoring_plan"],
                    estimated_duration=90.0,
                    success_rate=0.93,
                    cost_estimate=35.0
                )
            ],
            performance_metrics={
                "tasks_completed": 67,
                "average_rating": 4.4,
                "success_rate": 0.88
            }
        )
        
        # Register agents
        self.agents[content_agent.id] = content_agent
        self.agents[research_agent.id] = research_agent
        self.agents[analysis_agent.id] = analysis_agent
        self.agents[strategy_agent.id] = strategy_agent
        
        self.logger.info(f"Initialized {len(self.agents)} agents")
    
    async def create_task(self, name: str, description: str, requirements: List[str], 
                         priority: TaskPriority = TaskPriority.MEDIUM, 
                         input_data: Optional[Dict[str, Any]] = None,
                         dependencies: Optional[List[str]] = None) -> str:
        """Create a new orchestration task"""
        
        task_id = str(uuid.uuid4())
        task = OrchestrationTask(
            id=task_id,
            name=name,
            description=description,
            requirements=requirements,
            priority=priority,
            input_data=input_data or {},
            dependencies=dependencies or []
        )
        
        self.tasks[task_id] = task
        self.task_queue.append(task_id)
        self.orchestration_metrics["total_tasks"] += 1
        
        # Sort queue by priority
        self.task_queue.sort(key=lambda tid: self.tasks[tid].priority.value, reverse=True)
        
        self.logger.info(f"Created task {task_id}: {name}")
        return task_id
    
    async def assign_task(self, task_id: str) -> Optional[str]:
        """Assign task to best available agent"""
        
        if task_id not in self.tasks:
            return None
        
        task = self.tasks[task_id]
        
        # Check dependencies
        if not await self._check_task_dependencies(task):
            return None
        
        # Find best agent for task
        best_agent = await self._find_best_agent(task)
        
        if not best_agent:
            self.logger.warning(f"No suitable agent found for task {task_id}")
            return None
        
        # Assign task to agent
        task.assigned_agent_id = best_agent.id
        task.status = TaskStatus.ASSIGNED
        task.estimated_duration = self._estimate_task_duration(task, best_agent)
        
        best_agent.status = AgentStatus.BUSY
        best_agent.current_task_id = task_id
        best_agent.last_activity = datetime.now()
        
        # Remove from queue
        if task_id in self.task_queue:
            self.task_queue.remove(task_id)
        
        self.logger.info(f"Assigned task {task_id} to agent {best_agent.name}")
        return best_agent.id
    
    async def _check_task_dependencies(self, task: OrchestrationTask) -> bool:
        """Check if task dependencies are satisfied"""
        
        for dep_task_id in task.dependencies:
            if dep_task_id in self.tasks:
                dep_task = self.tasks[dep_task_id]
                if dep_task.status != TaskStatus.COMPLETED:
                    return False
            else:
                return False  # Dependency doesn't exist
        
        return True
    
    async def _find_best_agent(self, task: OrchestrationTask) -> Optional[Agent]:
        """Find the best agent for a task"""
        
        suitable_agents = []
        
        for agent in self.agents.values():
            if (agent.status == AgentStatus.IDLE and 
                agent.can_handle_task(task.requirements)):
                
                # Calculate agent score based on multiple factors
                score = await self._calculate_agent_score(agent, task)
                suitable_agents.append((agent, score))
        
        if not suitable_agents:
            return None
        
        # Return agent with highest score
        suitable_agents.sort(key=lambda x: x[1], reverse=True)
        return suitable_agents[0][0]
    
    async def _calculate_agent_score(self, agent: Agent, task: OrchestrationTask) -> float:
        """Calculate agent suitability score for task"""
        
        score = 0.0
        
        # Base capability match
        matching_capabilities = 0
        total_capability_score = 0.0
        
        for capability in agent.capabilities:
            if capability.name in task.requirements:
                matching_capabilities += 1
                total_capability_score += capability.success_rate
        
        if matching_capabilities > 0:
            capability_score = (matching_capabilities / len(task.requirements)) * (total_capability_score / matching_capabilities)
            score += capability_score * 0.4
        
        # Performance metrics
        if "success_rate" in agent.performance_metrics:
            score += agent.performance_metrics["success_rate"] * 0.3
        
        # Load balancing (prefer less busy agents)
        score += (1.0 - agent.load_percentage) * 0.2
        
        # Priority bonus for critical tasks
        if task.priority == TaskPriority.CRITICAL:
            score += 0.1
        
        return score
    
    def _estimate_task_duration(self, task: OrchestrationTask, agent: Agent) -> float:
        """Estimate task duration based on agent capabilities"""
        
        total_duration = 0.0
        requirements_handled = 0
        
        for capability in agent.capabilities:
            if capability.name in task.requirements:
                total_duration += capability.estimated_duration
                requirements_handled += 1
        
        # Apply complexity factor based on number of requirements
        complexity_factor = 1.0 + (len(task.requirements) - 1) * 0.1
        
        return total_duration * complexity_factor if requirements_handled > 0 else 60.0
    
    async def execute_task(self, task_id: str) -> Dict[str, Any]:
        """Execute assigned task"""
        
        if task_id not in self.tasks:
            return {"error": "Task not found"}
        
        task = self.tasks[task_id]
        
        if not task.assigned_agent_id:
            return {"error": "Task not assigned to any agent"}
        
        agent = self.agents[task.assigned_agent_id]
        
        try:
            task.status = TaskStatus.IN_PROGRESS
            task.started_at = datetime.now()
            
            # Simulate task execution
            result = await self._simulate_task_execution(task, agent)
            
            if result.get("success"):
                task.status = TaskStatus.COMPLETED
                task.completed_at = datetime.now()
                task.progress_percentage = 100.0
                task.output_data = result.get("output", {})
                
                # Update metrics
                self.orchestration_metrics["completed_tasks"] += 1
                self._update_completion_time(task)
                
                # Free up agent
                agent.status = AgentStatus.IDLE
                agent.current_task_id = None
                
                self.completed_tasks.append(task_id)
                
                self.logger.info(f"Task {task_id} completed successfully")
                
                return {
                    "success": True,
                    "task_id": task_id,
                    "output": task.output_data,
                    "duration": task.actual_duration
                }
            
            else:
                task.status = TaskStatus.FAILED
                task.error_message = result.get("error", "Unknown error")
                task.retries += 1
                
                # Update metrics
                self.orchestration_metrics["failed_tasks"] += 1
                
                # Free up agent
                agent.status = AgentStatus.IDLE  
                agent.current_task_id = None
                
                # Retry if possible
                if task.retries < task.max_retries:
                    task.status = TaskStatus.PENDING
                    self.task_queue.append(task_id)
                    self.logger.info(f"Retrying task {task_id} (attempt {task.retries + 1})")
                else:
                    self.logger.error(f"Task {task_id} failed after {task.retries} retries")
                
                return {
                    "success": False,
                    "task_id": task_id,
                    "error": task.error_message,
                    "retries": task.retries
                }
        
        except Exception as e:
            self.logger.error(f"Error executing task {task_id}: {e}")
            task.status = TaskStatus.FAILED
            task.error_message = str(e)
            agent.status = AgentStatus.ERROR
            
            return {"success": False, "error": str(e)}
    
    async def _simulate_task_execution(self, task: OrchestrationTask, agent: Agent) -> Dict[str, Any]:
        """Simulate task execution (replace with actual agent calls)"""
        
        # Simulate processing time
        duration = task.estimated_duration or 60.0
        await asyncio.sleep(min(5.0, duration / 10))  # Scaled down for demo
        
        # Simulate success/failure based on agent success rate
        import random
        
        success_rate = 0.9  # Default success rate
        for capability in agent.capabilities:
            if capability.name in task.requirements:
                success_rate = min(success_rate, capability.success_rate)
        
        if random.random() < success_rate:
            # Generate simulated output based on task requirements
            output = {}
            
            if "blog_writing" in task.requirements:
                output["article"] = f"Generated blog article: {task.name}"
                output["word_count"] = random.randint(800, 1200)
                output["seo_score"] = random.randint(75, 95)
            
            if "market_analysis" in task.requirements:
                output["market_report"] = f"Market analysis for {task.name}"
                output["market_size"] = random.randint(100, 1000) * 1000000
                output["growth_rate"] = random.uniform(0.05, 0.25)
            
            if "business_intelligence" in task.requirements:
                output["dashboard"] = f"BI dashboard for {task.name}"
                output["key_metrics"] = ["revenue", "growth", "efficiency"]
                output["recommendations"] = ["Optimize process A", "Expand market B"]
            
            return {"success": True, "output": output}
        else:
            return {"success": False, "error": "Simulated task failure"}
    
    def _update_completion_time(self, task: OrchestrationTask):
        """Update average completion time metric"""
        
        if task.started_at and task.completed_at:
            duration = (task.completed_at - task.started_at).total_seconds() / 60.0
            task.actual_duration = duration
            
            # Update rolling average
            completed_count = self.orchestration_metrics["completed_tasks"]
            current_avg = self.orchestration_metrics["average_completion_time"]
            
            new_avg = ((current_avg * (completed_count - 1)) + duration) / completed_count
            self.orchestration_metrics["average_completion_time"] = new_avg
    
    async def _orchestration_loop(self):
        """Main orchestration loop"""
        
        while self.orchestration_active:
            try:
                # Process pending tasks
                if self.task_queue:
                    task_id = self.task_queue[0]
                    assigned_agent_id = await self.assign_task(task_id)
                    
                    if assigned_agent_id:
                        # Execute task in background
                        asyncio.create_task(self.execute_task(task_id))
                
                # Update agent utilization
                self._update_agent_utilization()
                
                # Update success rate
                self._update_success_rate()
                
                # Wait before next iteration
                await asyncio.sleep(2)
                
            except Exception as e:
                self.logger.error(f"Error in orchestration loop: {e}")
                await asyncio.sleep(5)
    
    def _update_agent_utilization(self):
        """Update overall agent utilization metric"""
        
        if not self.agents:
            return
        
        busy_agents = sum(1 for agent in self.agents.values() if agent.status == AgentStatus.BUSY)
        utilization = busy_agents / len(self.agents)
        
        self.orchestration_metrics["agent_utilization"] = utilization
    
    def _update_success_rate(self):
        """Update overall success rate metric"""
        
        total_tasks = self.orchestration_metrics["total_tasks"]
        if total_tasks > 0:
            completed_tasks = self.orchestration_metrics["completed_tasks"]
            self.orchestration_metrics["success_rate"] = completed_tasks / total_tasks
    
    async def get_orchestration_status(self) -> Dict[str, Any]:
        """Get comprehensive orchestration status"""
        
        return {
            "timestamp": datetime.now().isoformat(),
            "agents": {
                agent_id: {
                    "name": agent.name,
                    "type": agent.type,
                    "status": agent.status.value,
                    "current_task": agent.current_task_id,
                    "capabilities": len(agent.capabilities),
                    "performance": agent.performance_metrics
                }
                for agent_id, agent in self.agents.items()
            },
            "tasks": {
                "total": len(self.tasks),
                "pending": len(self.task_queue),
                "in_progress": len([t for t in self.tasks.values() if t.status == TaskStatus.IN_PROGRESS]),
                "completed": len(self.completed_tasks),
                "failed": len([t for t in self.tasks.values() if t.status == TaskStatus.FAILED])
            },
            "metrics": self.orchestration_metrics,
            "queue": [
                {
                    "id": task_id,
                    "name": self.tasks[task_id].name,
                    "priority": self.tasks[task_id].priority.value,
                    "requirements": self.tasks[task_id].requirements
                }
                for task_id in self.task_queue[:5]  # Show top 5 queued tasks
            ]
        }
    
    async def get_agent_recommendations(self) -> List[Dict[str, Any]]:
        """Get recommendations for agent optimization"""
        
        recommendations = []
        
        # Analyze agent utilization
        utilization = self.orchestration_metrics["agent_utilization"]
        
        if utilization > 0.8:
            recommendations.append({
                "type": "scaling",
                "priority": "high",
                "message": "High agent utilization detected. Consider adding more agents.",
                "details": f"Current utilization: {utilization:.1%}"
            })
        elif utilization < 0.3:
            recommendations.append({
                "type": "optimization",
                "priority": "medium", 
                "message": "Low agent utilization. Consider optimizing task distribution.",
                "details": f"Current utilization: {utilization:.1%}"
            })
        
        # Analyze task success rate
        success_rate = self.orchestration_metrics["success_rate"]
        
        if success_rate < 0.85:
            recommendations.append({
                "type": "quality",
                "priority": "high",
                "message": "Task success rate below target. Review agent capabilities.",
                "details": f"Current success rate: {success_rate:.1%}"
            })
        
        # Analyze task queue length
        queue_length = len(self.task_queue)
        
        if queue_length > 10:
            recommendations.append({
                "type": "capacity",
                "priority": "medium",
                "message": "Large task queue detected. Consider increasing capacity.",
                "details": f"Current queue length: {queue_length}"
            })
        
        return recommendations
    
    async def create_workflow(self, name: str, steps: List[Dict[str, Any]]) -> str:
        """Create a complex workflow with multiple dependent tasks"""
        
        workflow_id = str(uuid.uuid4())
        task_ids = []
        
        for i, step in enumerate(steps):
            dependencies = []
            if i > 0:
                dependencies = [task_ids[i-1]]  # Sequential dependency
            
            task_id = await self.create_task(
                name=f"{name} - Step {i+1}: {step['name']}",
                description=step.get('description', ''),
                requirements=step.get('requirements', []),
                priority=TaskPriority(step.get('priority', 2)),
                input_data=step.get('input_data', {}),
                dependencies=dependencies
            )
            
            task_ids.append(task_id)
        
        self.logger.info(f"Created workflow {workflow_id} with {len(task_ids)} tasks")
        
        return workflow_id
    
    def stop_orchestration(self):
        """Stop the orchestration system"""
        self.orchestration_active = False
        self.logger.info("Orchestration system stopped")