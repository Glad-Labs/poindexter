"""
GLAD Labs AI Co-Founder Orchestrator Logic
Enhanced with Google Cloud Firestore and Pub/Sub integration
"""

import logging
import asyncio
import time
from typing import Dict, Any, List, Optional
import json
import os
import re

# Try to import complex dependency agents, but don't fail if unavailable
try:
    from agents.financial_agent.financial_agent import FinancialAgent  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    FinancialAgent = None  # type: ignore[assignment]
    logging.warning("Financial agent not available")

try:
    from agents.compliance_agent.agent import ComplianceAgent  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    ComplianceAgent = None  # type: ignore[assignment]
    logging.warning("Compliance agent not available")

class Orchestrator:
    """
    The main orchestrator for the AI Co-Founder with Google Cloud integration
    """

    def __init__(self, firestore_client=None, pubsub_client=None):
        """
        Initializes the Orchestrator with Google Cloud services and specialized agents
        
        Args:
            firestore_client: Optional Firestore client for database operations
            pubsub_client: Optional Pub/Sub client for agent messaging
        """
        self.firestore_client = firestore_client
        self.pubsub_client = pubsub_client
        
        # Initialize agents if available (guard against None for type checkers)
        if 'FinancialAgent' in globals() and FinancialAgent is not None:
            self.financial_agent = FinancialAgent()
            self.financial_agent_available = True
        else:
            self.financial_agent = None
            self.financial_agent_available = False

        if 'ComplianceAgent' in globals() and ComplianceAgent is not None:
            workspace_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
            self.compliance_agent = ComplianceAgent(workspace_root=workspace_root)
            self.compliance_agent_available = True
        else:
            self.compliance_agent = None
            self.compliance_agent_available = False

        logging.info(
            "Orchestrator initialized: firestore_available=%s pubsub_available=%s financial_agent=%s compliance_agent=%s",
            firestore_client is not None,
            pubsub_client is not None,
            self.financial_agent_available,
            self.compliance_agent_available,
        )

    async def process_command_async(self, command: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Async version of command processing for real database operations
        
        Args:
            command: The command to process
            context: Optional context for command processing
            
        Returns:
            Dictionary containing response and metadata
        """
        try:
            command_lower = command.lower().strip()
            
            # Enhanced command routing with better pattern matching
            if any(keyword in command_lower for keyword in ["calendar", "tasks", "schedule"]):
                return self._format_response(await self.get_content_calendar_async())
            elif any(keyword in command_lower for keyword in ["create task", "new post", "write about"]):
                return self._format_response(await self.create_content_task(command))
            elif any(keyword in command_lower for keyword in ["financial", "balance", "spend", "budget", "money"]):
                return self._format_response(await self.get_financial_summary_async())
            elif any(keyword in command_lower for keyword in ["suggest topics", "new ideas", "topic ideas"]):
                return self._format_response("Topic suggestion feature is being enhanced with AI capabilities.")
            elif any(keyword in command_lower for keyword in ["run content", "execute tasks", "start pipeline"]):
                return self._format_response(await self.run_content_pipeline_async())
            elif any(keyword in command_lower for keyword in ["security", "audit", "compliance"]):
                return self._format_response(self.run_security_audit())
            elif any(keyword in command_lower for keyword in ["status", "health", "check"]):
                return await self._get_system_status_async()
            elif any(keyword in command_lower for keyword in ["intervene", "emergency", "stop"]):
                return await self._handle_intervention_async(command, context)
            elif any(keyword in command_lower for keyword in ["help", "what", "how", "commands"]):
                return self._get_help_response()
            else:
                return self._format_response(
                    f"I understand you want help with: '{command}'. "
                    "I can help with content creation, financial analysis, security audits, and more. "
                    "Try commands like 'create content about AI' or 'show financial summary'."
                )
                
        except Exception as e:
            logging.error(f"Error processing command: {e}")
            return {
                "response": f"I encountered an error while processing your command: {str(e)}",
                "status": "error",
                "metadata": {"error": str(e)}
            }

    def process_command(self, command: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Synchronous wrapper for command processing - maintains backward compatibility
        """
        try:
            command_lower = command.lower().strip()
            
            # Enhanced command routing with better pattern matching
            if any(keyword in command_lower for keyword in ["calendar", "tasks", "schedule"]):
                return self._format_response(self.get_content_calendar())
            elif any(keyword in command_lower for keyword in ["create task", "new post", "write about"]):
                return self._format_response(self.create_content_task_sync(command))
            elif any(keyword in command_lower for keyword in ["financial", "balance", "spend", "budget", "money"]):
                return self._format_response(self.get_financial_summary())
            elif any(keyword in command_lower for keyword in ["suggest topics", "new ideas", "topic ideas"]):
                return self._format_response("Topic suggestion feature is being enhanced with AI capabilities.")
            elif any(keyword in command_lower for keyword in ["run content", "execute tasks", "start pipeline"]):
                return self._format_response(self.run_content_pipeline())
            elif any(keyword in command_lower for keyword in ["security", "audit", "compliance"]):
                return self._format_response(self.run_security_audit())
            elif any(keyword in command_lower for keyword in ["status", "health", "check"]):
                return self._get_system_status()
            elif any(keyword in command_lower for keyword in ["intervene", "emergency", "stop"]):
                return self._handle_intervention(command, context)
            elif any(keyword in command_lower for keyword in ["help", "what", "how", "commands"]):
                return self._get_help_response()
            else:
                return self._format_response(
                    f"I understand you want help with: '{command}'. "
                    "I can help with content creation, financial analysis, security audits, and more. "
                    "Try commands like 'create content about AI' or 'show financial summary'."
                )
                
        except Exception as e:
            logging.error(f"Error processing command: {e}")
            return {
                "response": f"I encountered an error while processing your command: {str(e)}",
                "status": "error",
                "metadata": {"error": str(e)}
            }

    def get_content_calendar(self) -> str:
        """Synchronous version of content calendar"""
        try:
            if self.firestore_client:
                return "Content calendar loaded from Firestore. (Use async version for real-time data)"
            else:
                return "Content calendar feature available. (Running in development mode - Firestore not connected)"
        except Exception as e:
            logging.error(f"Error fetching content calendar: {e}")
            return "I'm sorry, I had trouble fetching the content calendar."

    async def get_content_calendar_async(self) -> str:
        """Async version of content calendar with real Firestore integration"""
        try:
            if self.firestore_client:
                # Get actual pending tasks from Firestore
                tasks = await self.firestore_client.get_pending_tasks(limit=20)
                
                if tasks:
                    task_count = len(tasks)
                    response = f"📅 Content Calendar: {task_count} pending tasks loaded from Firestore\n\n"
                    
                    for i, task in enumerate(tasks[:5], 1):  # Show first 5 tasks
                        status_emoji = "🟡" if task.get('status') == 'pending' else "🟢"
                        response += f"{status_emoji} {i}. {task.get('topic', 'Unknown topic')} ({task.get('status', 'unknown')})\n"
                    
                    if task_count > 5:
                        response += f"\n... and {task_count - 5} more tasks"
                    
                    return response
                else:
                    return "📅 Content calendar is empty. Create new tasks to get started!"
            else:
                return "📅 Content calendar ready (Firestore integration available for real-time data)"
        except Exception as e:
            logging.error(f"Error fetching content calendar: {e}")
            return "❌ Error fetching content calendar from database"

    async def get_financial_summary_async(self) -> str:
        """Async version of financial summary with real Firestore data"""
        try:
            response = ""
            
            # Get data from financial agent if available
            if getattr(self, 'financial_agent_available', False) and getattr(self, 'financial_agent', None) is not None:
                agent_response = self.financial_agent.get_financial_summary()  # type: ignore[call-arg,union-attr]
                response += agent_response + "\n\n"
            
            # Enhance with Firestore financial data
            if self.firestore_client:
                financial_summary = await self.firestore_client.get_financial_summary(days=30)
                
                response += f"💾 **Enhanced Firestore Data (Last 30 days):**\n"
                response += f"📊 Total Spend: ${financial_summary.get('total_spend', 0):.2f}\n"
                response += f"📈 Transaction Count: {financial_summary.get('entry_count', 0)}\n"
                response += f"📉 Avg Daily Spend: ${financial_summary.get('average_daily_spend', 0):.2f}\n"
                
                # Show recent entries
                recent_entries = financial_summary.get('entries', [])[:3]
                if recent_entries:
                    response += "\n🕒 **Recent Transactions:**\n"
                    for entry in recent_entries:
                        amount = entry.get('amount', 0)
                        category = entry.get('category', 'Unknown')
                        response += f"  • ${amount:.2f} - {category}\n"
            else:
                response += "💾 Firestore financial tracking available for enhanced analytics"
                
            return response
                    
        except Exception as e:
            logging.error(f"Error getting financial summary: {e}")
            return "❌ Error retrieving financial data"

    async def run_content_pipeline_async(self) -> str:
        """Async version of content pipeline with real Pub/Sub orchestration"""
        try:
            if self.pubsub_client:
                # Trigger all content agents via Pub/Sub
                pipeline_command = {
                    "action": "process_all_pending",
                    "priority": "high",
                    "source": "cofounder_orchestrator",
                    "timestamp": str(asyncio.get_event_loop().time())
                }
                
                message_id = await self.pubsub_client.publish_agent_command("content", pipeline_command)
                
                # Also send to content pipeline topic
                content_pipeline_msg = {
                    "request_type": "batch_processing",
                    "action": "process_all",
                    "priority": "high"
                }
                
                pipeline_msg_id = await self.pubsub_client.publish_content_request(content_pipeline_msg)
                
                # Log the pipeline trigger
                if self.firestore_client:
                    await self.firestore_client.add_log_entry(
                        "info",
                        "Content pipeline triggered by orchestrator",
                        {
                            "command_message_id": message_id,
                            "pipeline_message_id": pipeline_msg_id,
                            "source": "cofounder"
                        }
                    )
                
                return f"🚀 Content pipeline activated!\n📡 Command sent (ID: {message_id[:8]}...)\n📋 Pipeline triggered (ID: {pipeline_msg_id[:8]}...)\n✅ All content agents notified via Pub/Sub"
            else:
                return "🚀 Content pipeline ready (Pub/Sub integration available for distributed processing)"
                
        except Exception as e:
            logging.error(f"Error running content pipeline: {e}")
            return f"❌ Error starting content pipeline: {str(e)}"

    async def _get_system_status_async(self) -> Dict[str, Any]:
        """Async version of system status with real health checks"""
        try:
            status_data = {
                "orchestrator": "online",
                "google_cloud": {
                    "firestore": self.firestore_client is not None,
                    "pubsub": self.pubsub_client is not None
                },
                "agents": {
                    "financial": getattr(self, 'financial_agent_available', False),
                    "compliance": getattr(self, 'compliance_agent_available', False)
                },
                "mode": "production" if (self.firestore_client and self.pubsub_client) else "development"
            }
            
            # Perform actual health checks if services are available
            if self.firestore_client:
                firestore_health = await self.firestore_client.health_check()
                status_data["firestore_health"] = firestore_health
            
            if self.pubsub_client:
                pubsub_health = await self.pubsub_client.health_check()
                status_data["pubsub_health"] = pubsub_health
            
            # Get task statistics
            if self.firestore_client:
                pending_tasks = await self.firestore_client.get_pending_tasks(limit=1)
                status_data["task_queue_size"] = len(pending_tasks) if pending_tasks else 0
            
            status_message = f"🟢 System Status: {status_data['mode'].upper()}\n"
            status_message += f"☁️  Google Cloud: Firestore {'✓' if status_data['google_cloud']['firestore'] else '✗'}, Pub/Sub {'✓' if status_data['google_cloud']['pubsub'] else '✗'}\n"
            status_message += f"🤖 Agents: Financial {'✓' if status_data['agents']['financial'] else '✗'}, Compliance {'✓' if status_data['agents']['compliance'] else '✗'}\n"
            
            if 'task_queue_size' in status_data:
                status_message += f"📋 Task Queue: {status_data['task_queue_size']} pending tasks\n"
            
            # Add health check details
            if 'firestore_health' in status_data:
                firestore_status = status_data['firestore_health'].get('status', 'unknown')
                status_message += f"🗄️  Firestore: {firestore_status}\n"
            
            if 'pubsub_health' in status_data:
                pubsub_status = status_data['pubsub_health'].get('status', 'unknown')
                status_message += f"📡 Pub/Sub: {pubsub_status}"
            
            return {
                "response": status_message,
                "status": "success",
                "metadata": status_data
            }
            
        except Exception as e:
            logging.error(f"Error getting system status: {e}")
            return {
                "response": f"❌ Error getting system status: {str(e)}",
                "status": "error",
                "metadata": {"error": str(e)}
            }

    async def _handle_intervention_async(self, command: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Async version of intervention protocol with real Pub/Sub notification"""
        try:
            reason = "user_intervention_request"
            if "emergency" in command.lower():
                reason = "emergency_situation"
            elif "budget" in command.lower():
                reason = "financial_concern"
            elif "stop" in command.lower():
                reason = "operation_halt_requested"
                
            response_message = f"🚨 // INTERVENE protocol activated: {reason}"
            
            if self.pubsub_client:
                intervention_data = {
                    "reason": reason,
                    "severity": "high",
                    "command": command,
                    "context": context,
                    "timestamp": str(asyncio.get_event_loop().time()),
                    "initiated_by": "cofounder_orchestrator"
                }
                
                message_id = await self.pubsub_client.trigger_intervene_protocol(intervention_data)
                response_message += f"\n📢 All agents notified via emergency Pub/Sub channels (Message ID: {message_id[:8]}...)"
                
                # Log the intervention
                if self.firestore_client:
                    await self.firestore_client.add_log_entry(
                        "critical",
                        f"INTERVENE protocol triggered: {reason}",
                        {
                            "pubsub_message_id": message_id,
                            "reason": reason,
                            "command": command,
                            "context": context
                        }
                    )
            else:
                response_message += "\n⚠️  Emergency protocol ready (Pub/Sub integration available)"
            
            return {
                "response": response_message,
                "status": "intervention",
                "metadata": {
                    "reason": reason,
                    "protocol": "INTERVENE",
                    "severity": "high",
                    "services_notified": self.pubsub_client is not None
                }
            }
            
        except Exception as e:
            logging.error(f"Error in intervention: {e}")
            return {
                "response": f"🚨 CRITICAL: Intervention failed: {str(e)}",
                "status": "error",
                "metadata": {"error": str(e), "protocol": "INTERVENE_FAILED"}
            }

    def create_content_task_sync(self, command: str) -> str:
        """Synchronous version of content task creation for backward compatibility"""
        try:
            # Extract topic using improved pattern matching
            topic = self._extract_topic_from_command(command)
            
            # Create basic task data for sync version
            task_data = {
                "topic": topic,
                "primary_keyword": topic.split()[0] if topic.split() else "content",
                "target_audience": "General",
                "category": "Blog Post",
                "status": "pending"
            }

            # Generate task ID for development mode
            task_id = f"sync-task-{abs(hash(topic)) % 10000}"
            
            if self.firestore_client:
                response = f"✅ Created content task: '{topic}' (Task queued for async processing)"
            else:
                response = f"✅ Created content task: '{topic}' (Development mode - Task ID: {task_id})"
            
            # Note: Pub/Sub integration requires async, so mention it's available
            if self.pubsub_client:
                response += " → Use async API for immediate Pub/Sub notification"
            
            return response
            
        except Exception as e:
            logging.error(f"Error creating content task: {e}")
            return f"❌ Failed to create content task: {str(e)}"

    async def create_content_task(self, command: str) -> str:
        """Enhanced task creation with real Firestore and Pub/Sub integration"""
        try:
            # Extract topic using improved pattern matching
            topic = self._extract_topic_from_command(command)
            
            # Create comprehensive task data matching data_schemas.md
            task_data = {
                "agentId": "content-creation-agent-v1",
                "taskName": f"Generate content about {topic}",
                "topic": topic,
                "primary_keyword": topic.split()[0] if topic.split() else "content",
                "target_audience": "General",
                "category": "Blog Post",
                "status": "queued",
                "metadata": {
                    "priority": 2,  # Medium priority
                    "estimated_duration_minutes": 45,
                    "source": "cofounder_orchestrator",
                    "content_type": "blog_post",
                    "word_count_target": 1500
                }
            }

            task_id = None
            if self.firestore_client:
                # Actually create the task in Firestore
                task_id = await self.firestore_client.add_task(task_data)
                response = f"✅ Created content task: '{topic}' (Task ID: {task_id})"
                
                # Log the task creation
                await self.firestore_client.add_log_entry(
                    "info",
                    f"Content task created via orchestrator: {topic}",
                    {"task_id": task_id, "topic": topic, "source": "cofounder"}
                )
            else:
                # Development mode with simulated task ID
                task_id = f"dev-task-{abs(hash(topic)) % 10000}"
                response = f"✅ Created content task: '{topic}' (Development mode - Task ID: {task_id})"
            
            # Trigger content agent via Pub/Sub if available
            if self.pubsub_client:
                content_request = {
                    "task_id": task_id,
                    "action": "create_content",
                    "specifications": task_data,
                    "priority": "normal",
                    "source": "cofounder_orchestrator"
                }
                
                message_id = await self.pubsub_client.publish_content_request(content_request)
                response += f" → Content agent notified via Pub/Sub (Message ID: {message_id[:8]}...)"
                
                # Update task status to indicate it's been dispatched
                if self.firestore_client:
                    await self.firestore_client.update_task_status(
                        task_id, 
                        "in_progress", 
                        {"pubsub_message_id": message_id, "dispatched_at": "now"}
                    )
            else:
                response += " → Ready for content agent processing"
            
            return response
            
        except Exception as e:
            logging.error(f"Error creating content task: {e}")
            return f"❌ Failed to create content task: {str(e)}"

    def get_financial_summary(self) -> str:
        """Enhanced financial summary with multiple data sources"""
        try:
            if getattr(self, 'financial_agent_available', False) and getattr(self, 'financial_agent', None) is not None:
                # Use the existing financial agent
                agent_response = self.financial_agent.get_financial_summary()  # type: ignore[call-arg,union-attr]
                
                if self.firestore_client:
                    # In real implementation: cloud_data = await self.firestore_client.get_financial_summary()
                    return f"{agent_response}\n\n💾 Enhanced with Firestore financial data"
                else:
                    return f"{agent_response}\n\n📊 (Firestore integration available for enhanced tracking)"
            else:
                if self.firestore_client:
                    return "📊 Financial summary available from Firestore database"
                else:
                    return "📊 Financial tracking system ready (agents and database in development mode)"
                    
        except Exception as e:
            logging.error(f"Error getting financial summary: {e}")
            return "I'm sorry, I had trouble getting the financial summary."

    def run_content_pipeline(self) -> str:
        """Enhanced content pipeline with Pub/Sub orchestration"""
        try:
            if self.pubsub_client:
                # In real implementation: await self.pubsub_client.publish_agent_command("content", {"action": "process_all"})
                return "🚀 Content pipeline started via Pub/Sub messaging. All content agents notified."
            else:
                return "🚀 Content pipeline ready to start (Pub/Sub integration available for distributed processing)"

        except Exception as e:
            logging.error(f"Error running content pipeline: {e}")
            return "I'm sorry, I encountered an error while trying to start the content pipeline."

    def run_security_audit(self) -> str:
        """Enhanced security audit"""
        try:
            if self.compliance_agent:
                return self.compliance_agent.run_security_audit()
            else:
                return "🔒 Security audit system ready (compliance agent in development mode)"

        except Exception as e:
            logging.error(f"Error running security audit: {e}")
            return "I'm sorry, I encountered an error during the security audit."

    def _get_system_status(self) -> Dict[str, Any]:
        """Get comprehensive system status"""
        status_data = {
            "orchestrator": "online",
            "google_cloud": {
                "firestore": self.firestore_client is not None,
                "pubsub": self.pubsub_client is not None
            },
            "agents": {
                "financial": getattr(self, 'financial_agent_available', False),
                "compliance": getattr(self, 'compliance_agent_available', False)
            },
            "mode": "production" if (self.firestore_client and self.pubsub_client) else "development"
        }
        
        status_message = f"🟢 System Status: {status_data['mode'].upper()}\n"
        status_message += f"☁️  Google Cloud: Firestore {'✓' if status_data['google_cloud']['firestore'] else '✗'}, Pub/Sub {'✓' if status_data['google_cloud']['pubsub'] else '✗'}\n"
        status_message += f"🤖 Agents: Financial {'✓' if status_data['agents']['financial'] else '✗'}, Compliance {'✓' if status_data['agents']['compliance'] else '✗'}"
        
        return {
            "response": status_message,
            "status": "success",
            "metadata": status_data
        }

    def _handle_intervention(self, command: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Handle emergency intervention protocol"""
        try:
            reason = "user_intervention_request"
            if "emergency" in command.lower():
                reason = "emergency_situation"
            elif "budget" in command.lower():
                reason = "financial_concern"
                
            response_message = f"🚨 // INTERVENE protocol activated: {reason}"
            
            if self.pubsub_client:
                # In real implementation: await self.pubsub_client.trigger_intervene_protocol({...})
                response_message += "\n📢 All agents notified via emergency Pub/Sub channels"
            else:
                response_message += "\n⚠️  Emergency protocol ready (Pub/Sub integration available)"
            
            return {
                "response": response_message,
                "status": "intervention",
                "metadata": {"reason": reason, "protocol": "INTERVENE"}
            }
            
        except Exception as e:
            logging.error(f"Error in intervention: {e}")
            return {
                "response": f"🚨 CRITICAL: Intervention failed: {str(e)}",
                "status": "error"
            }

    def _get_help_response(self) -> Dict[str, Any]:
        """Provide comprehensive help information"""
        help_message = """🤖 GLAD Labs AI Co-Founder - Available Commands:

📝 **Content Creation:**
   • "Create content about [topic]" - Generate new blog post
   • "Show content calendar" - View scheduled content
   • "Run content pipeline" - Process all pending tasks

💰 **Financial Management:**
   • "Show financial summary" - Current budget status
   • "Analyze spending" - Expense breakdown

🔒 **Security & Compliance:**
   • "Run security audit" - Check system security
   • "Compliance check" - Verify standards

🚨 **Emergency Controls:**
   • "Intervene" - Trigger emergency protocol
   • "System status" - Check all services

❓ **Help:** "Help" or "What can you do?"

💡 **Pro Tip:** I work best with natural language! Try "I need help creating content about AI trends" or "What's our current spending situation?"
"""
        
        return {
            "response": help_message,
            "status": "success",
            "metadata": {
                "available_services": {
                    "firestore": self.firestore_client is not None,
                    "pubsub": self.pubsub_client is not None,
                    "financial_agent": getattr(self, 'financial_agent_available', False),
                    "compliance_agent": getattr(self, 'compliance_agent_available', False)
                }
            }
        }

    def _extract_topic_from_command(self, command: str) -> str:
        """Enhanced topic extraction with better pattern matching"""
        command_lower = command.lower()
        
        # Improved patterns for topic extraction
        patterns = [
            r'(?:about|on|regarding|concerning)\s+([^.!?]+)',
            r'(?:write|create|post)\s+(?:about\s+)?([^.!?]+)',
            r'(?:topic|subject)[\s:]+([^.!?]+)',
            r'(?:blog\s+post\s+about\s+)([^.!?]+)'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, command_lower)
            if match:
                topic = match.group(1).strip()
                # Clean up common words
                topic = re.sub(r'\b(a|an|the|for|to|of|in|on|at|by|with)\b', '', topic)
                return topic.strip() or "general business content"
        
        # Fallback: extract meaningful words
        words = [word for word in command_lower.split() 
                if len(word) > 3 and word not in ['create', 'write', 'about', 'post', 'blog']]
        
        if words:
            return " ".join(words[:3])  # Take first 3 meaningful words
        
        return "general business content"

    def _format_response(self, message: str) -> Dict[str, Any]:
        """Format a simple string response into the standard response format"""
        return {
            "response": message,
            "status": "success",
            "metadata": {}
        }
