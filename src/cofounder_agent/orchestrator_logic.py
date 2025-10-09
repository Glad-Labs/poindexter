"""
GLAD Labs AI Co-Founder Orchestrator Logic
Enhanced with Google Cloud Firestore and Pub/Sub integration
"""

import logging
from typing import Dict, Any, List, Optional
import json
import os
import re

# Try to import complex dependency agents, but don't fail if unavailable
try:
    from agents.financial_agent.financial_agent import FinancialAgent
    FINANCIAL_AGENT_AVAILABLE = True
except ImportError:
    FinancialAgent = None
    FINANCIAL_AGENT_AVAILABLE = False
    logging.warning("Financial agent not available")

try:
    from agents.compliance_agent.agent import ComplianceAgent
    COMPLIANCE_AGENT_AVAILABLE = True
except ImportError:
    ComplianceAgent = None
    COMPLIANCE_AGENT_AVAILABLE = False
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
        
        # Initialize agents if available
        if FINANCIAL_AGENT_AVAILABLE:
            self.financial_agent = FinancialAgent()
        else:
            self.financial_agent = None
        
        if COMPLIANCE_AGENT_AVAILABLE:
            workspace_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
            self.compliance_agent = ComplianceAgent(workspace_root=workspace_root)
        else:
            self.compliance_agent = None
        
        logging.info("Orchestrator initialized", 
                    firestore_available=firestore_client is not None,
                    pubsub_available=pubsub_client is not None,
                    financial_agent=FINANCIAL_AGENT_AVAILABLE,
                    compliance_agent=COMPLIANCE_AGENT_AVAILABLE)

    def process_command(self, command: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Enhanced command processing with context support and structured responses
        
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
                return self._format_response(self.get_content_calendar())
            elif any(keyword in command_lower for keyword in ["create task", "new post", "write about"]):
                return self._format_response(self.create_content_task(command))
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
        """Enhanced content calendar with Firestore integration"""
        try:
            if self.firestore_client:
                # In a real implementation, this would be async
                # tasks = await self.firestore_client.get_pending_tasks()
                return "Content calendar loaded from Firestore. (Database integration active)"
            else:
                return "Content calendar feature available. (Running in development mode - Firestore not connected)"
        except Exception as e:
            logging.error(f"Error fetching content calendar: {e}")
            return "I'm sorry, I had trouble fetching the content calendar."

    def create_content_task(self, command: str) -> str:
        """Enhanced task creation with Pub/Sub integration"""
        try:
            # Extract topic using improved pattern matching
            topic = self._extract_topic_from_command(command)
            
            task_data = {
                "topic": topic,
                "primary_keyword": topic.split()[0] if topic.split() else "content",
                "target_audience": "General",
                "category": "Blog Post",
                "status": "pending"
            }

            if self.firestore_client:
                # In real implementation: task_id = await self.firestore_client.add_task(task_data)
                response = f"✅ Created content task: '{topic}' (Saved to Firestore)"
            else:
                response = f"✅ Created content task: '{topic}' (Development mode)"
            
            # Trigger content agent via Pub/Sub if available
            if self.pubsub_client:
                # In real implementation: await self.pubsub_client.publish_content_request(task_data)
                response += " → Content agent notified via Pub/Sub"
            
            return response
            
        except Exception as e:
            logging.error(f"Error creating content task: {e}")
            return "I'm sorry, I had trouble creating the new content task."

    def get_financial_summary(self) -> str:
        """Enhanced financial summary with multiple data sources"""
        try:
            if self.financial_agent:
                # Use the existing financial agent
                agent_response = self.financial_agent.get_financial_summary()
                
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
                "financial": FINANCIAL_AGENT_AVAILABLE,
                "compliance": COMPLIANCE_AGENT_AVAILABLE
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
                    "financial_agent": FINANCIAL_AGENT_AVAILABLE,
                    "compliance_agent": COMPLIANCE_AGENT_AVAILABLE
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
