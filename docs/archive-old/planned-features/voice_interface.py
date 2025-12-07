"""
Voice Interface System for AI Co-Founder
Provides speech-to-text, text-to-speech, and voice command processing
"""

import asyncio
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional, Callable
import json
from dataclasses import dataclass, asdict

# Set up logging
logger = logging.getLogger(__name__)

@dataclass
class VoiceCommand:
    """Voice command data structure"""
    id: str
    text: str
    confidence: float
    timestamp: datetime
    language: str = "en-US"
    intent: Optional[str] = None
    entities: Optional[Dict[str, Any]] = None
    processed: bool = False
    response_text: Optional[str] = None

class VoiceInterfaceSystem:
    """Advanced voice interface for AI co-founder interaction"""
    
    def __init__(self):
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        self.voice_commands: List[VoiceCommand] = []
        self.active_sessions: Dict[str, Dict[str, Any]] = {}
        self.wake_words = ["hey ai", "ai cofounder", "glad labs", "computer"]
        self.command_patterns = self._initialize_command_patterns()
        self.voice_settings = {
            "speech_rate": 1.0,
            "pitch": 0.0,
            "volume": 0.8,
            "voice_id": "neural_voice_1",
            "language": "en-US"
        }
        
    def _initialize_command_patterns(self) -> Dict[str, Dict[str, Any]]:
        """Initialize voice command recognition patterns"""
        return {
            "business_metrics": {
                "patterns": [
                    "show me business metrics",
                    "what are our numbers",
                    "business performance",
                    "how are we doing",
                    "revenue update",
                    "financial status"
                ],
                "intent": "get_business_metrics",
                "requires_auth": False
            },
            "task_creation": {
                "patterns": [
                    "create a task",
                    "add new task", 
                    "I need to",
                    "remind me to",
                    "schedule task",
                    "new assignment"
                ],
                "intent": "create_task",
                "requires_auth": True
            },
            "content_creation": {
                "patterns": [
                    "write a blog post",
                    "create content",
                    "generate article",
                    "content strategy",
                    "social media post",
                    "marketing content"
                ],
                "intent": "create_content",
                "requires_auth": True
            },
            "strategic_planning": {
                "patterns": [
                    "strategic plan",
                    "business strategy",
                    "growth planning",
                    "market analysis",
                    "competitive analysis",
                    "business roadmap"
                ],
                "intent": "strategic_planning",
                "requires_auth": True
            },
            "system_control": {
                "patterns": [
                    "system status",
                    "health check",
                    "restart system",
                    "check agents",
                    "orchestration status",
                    "agent performance"
                ],
                "intent": "system_control",
                "requires_auth": True
            },
            "help_support": {
                "patterns": [
                    "help me",
                    "what can you do",
                    "capabilities",
                    "commands list",
                    "voice commands",
                    "how to use"
                ],
                "intent": "help_support",
                "requires_auth": False
            }
        }
    
    async def process_voice_input(self, audio_data: bytes, session_id: str = "default") -> Dict[str, Any]:
        """Process voice input and convert to text"""
        try:
            # Simulate speech-to-text processing
            # In production, integrate with Azure Cognitive Services, Google Speech-to-Text, etc.
            
            transcription_result = await self._simulate_speech_to_text(audio_data)
            
            if not transcription_result.get("success"):
                return {"success": False, "error": "Speech recognition failed"}
            
            text = transcription_result["text"]
            confidence = transcription_result["confidence"]
            
            # Create voice command record
            command = VoiceCommand(
                id=f"voice_{session_id}_{datetime.now().timestamp()}",
                text=text,
                confidence=confidence,
                timestamp=datetime.now(),
                language=self.voice_settings["language"]
            )
            
            # Process the command
            processed_result = await self._process_voice_command(command, session_id)
            
            # Store command
            self.voice_commands.append(command)
            
            # Keep only recent commands (last 100)
            if len(self.voice_commands) > 100:
                self.voice_commands = self.voice_commands[-100:]
            
            return {
                "success": True,
                "command_id": command.id,
                "transcription": text,
                "confidence": confidence,
                "intent": command.intent,
                "response": processed_result
            }
            
        except Exception as e:
            self.logger.error(f"Error processing voice input: {e}")
            return {"success": False, "error": str(e)}
    
    async def _simulate_speech_to_text(self, audio_data: bytes) -> Dict[str, Any]:
        """Simulate speech-to-text conversion"""
        # In production, replace with actual speech recognition service
        
        # Simulate different voice commands for demo
        import random
        
        sample_commands = [
            "Show me business metrics",
            "Create a new blog post about AI automation",
            "What is our revenue this month",
            "Schedule a market analysis task",
            "Generate a strategic plan for Q2",
            "Check system status",
            "Help me with content creation",
            "What can you do for me"
        ]
        
        # Simulate processing delay
        await asyncio.sleep(0.5)
        
        # Return random sample for demo
        selected_command = random.choice(sample_commands)
        confidence = random.uniform(0.85, 0.98)
        
        return {
            "success": True,
            "text": selected_command,
            "confidence": confidence,
            "language": "en-US",
            "processing_time": 0.5
        }
    
    async def _process_voice_command(self, command: VoiceCommand, session_id: str) -> Dict[str, Any]:
        """Process voice command and determine intent"""
        try:
            # Extract intent from text
            intent_result = await self._extract_intent(command.text)
            
            command.intent = intent_result.get("intent")
            command.entities = intent_result.get("entities", {})
            
            # Generate appropriate response
            response = await self._generate_voice_response(command, session_id)
            
            command.response_text = response.get("text")
            command.processed = True
            
            return response
            
        except Exception as e:
            self.logger.error(f"Error processing voice command: {e}")
            return {"success": False, "error": str(e)}
    
    async def _extract_intent(self, text: str) -> Dict[str, Any]:
        """Extract intent and entities from voice command text"""
        
        text_lower = text.lower()
        
        # Simple pattern matching (in production, use NLU service)
        for category, config in self.command_patterns.items():
            for pattern in config["patterns"]:
                if pattern.lower() in text_lower:
                    return {
                        "intent": config["intent"],
                        "category": category,
                        "entities": self._extract_entities(text, config["intent"]),
                        "confidence": 0.9
                    }
        
        # Default intent for unrecognized commands
        return {
            "intent": "unknown",
            "category": "general",
            "entities": {},
            "confidence": 0.1
        }
    
    def _extract_entities(self, text: str, intent: str) -> Dict[str, Any]:
        """Extract entities based on intent"""
        
        entities = {}
        text_lower = text.lower()
        
        # Task creation entities
        if intent == "create_task":
            # Extract task type
            if "blog" in text_lower or "article" in text_lower:
                entities["task_type"] = "content_creation"
            elif "analysis" in text_lower or "research" in text_lower:
                entities["task_type"] = "research"
            elif "plan" in text_lower or "strategy" in text_lower:
                entities["task_type"] = "strategic_planning"
            
            # Extract priority
            if "urgent" in text_lower or "asap" in text_lower or "critical" in text_lower:
                entities["priority"] = "high"
            elif "low priority" in text_lower or "when you can" in text_lower:
                entities["priority"] = "low"
            else:
                entities["priority"] = "medium"
        
        # Content creation entities
        elif intent == "create_content":
            if "blog" in text_lower:
                entities["content_type"] = "blog_post"
            elif "social" in text_lower:
                entities["content_type"] = "social_media"
            elif "email" in text_lower:
                entities["content_type"] = "email"
            
            # Extract topic if mentioned
            about_index = text_lower.find("about")
            if about_index != -1:
                topic = text[about_index + 5:].strip()
                entities["topic"] = topic
        
        return entities
    
    async def _generate_voice_response(self, command: VoiceCommand, session_id: str) -> Dict[str, Any]:
        """Generate appropriate voice response based on intent"""
        
        intent = command.intent
        entities = command.entities or {}
        
        try:
            if intent == "get_business_metrics":
                return await self._handle_metrics_request(command, session_id)
            
            elif intent == "create_task":
                return await self._handle_task_creation(command, session_id)
            
            elif intent == "create_content":
                return await self._handle_content_creation(command, session_id)
            
            elif intent == "strategic_planning":
                return await self._handle_strategic_planning(command, session_id)
            
            elif intent == "system_control":
                return await self._handle_system_control(command, session_id)
            
            elif intent == "help_support":
                return await self._handle_help_request(command, session_id)
            
            else:
                return {
                    "success": True,
                    "text": "I didn't understand that command. Try saying 'help me' to see what I can do.",
                    "audio_url": None,
                    "should_speak": True
                }
                
        except Exception as e:
            self.logger.error(f"Error generating voice response: {e}")
            return {
                "success": False,
                "text": "Sorry, I encountered an error processing your request.",
                "error": str(e)
            }
    
    async def _handle_metrics_request(self, command: VoiceCommand, session_id: str) -> Dict[str, Any]:
        """Handle business metrics voice request"""
        
        # Simulate getting business metrics
        response_text = """Here's your business overview: Revenue is up 12.5% this month at $10,750. 
        Task completion rate is at 78%, and our content engagement is performing well at 4.5%. 
        The AI automation system is running efficiently with 96% uptime."""
        
        return {
            "success": True,
            "text": response_text,
            "should_speak": True,
            "data": {
                "metrics_requested": True,
                "session_id": session_id
            }
        }
    
    async def _handle_task_creation(self, command: VoiceCommand, session_id: str) -> Dict[str, Any]:
        """Handle task creation voice request"""
        
        entities = command.entities or {}
        task_type = entities.get("task_type", "general")
        priority = entities.get("priority", "medium")
        
        response_text = f"I've created a {task_type} task with {priority} priority based on your request. " \
                       f"The task will be assigned to the appropriate agent for processing."
        
        return {
            "success": True,
            "text": response_text,
            "should_speak": True,
            "data": {
                "task_created": True,
                "task_type": task_type,
                "priority": priority
            }
        }
    
    async def _handle_content_creation(self, command: VoiceCommand, session_id: str) -> Dict[str, Any]:
        """Handle content creation voice request"""
        
        entities = command.entities or {}
        content_type = entities.get("content_type", "general content")
        topic = entities.get("topic", "your specified topic")
        
        response_text = f"I'll create {content_type} about {topic}. " \
                       f"The content creation agent will start working on this and you'll be notified when it's ready."
        
        return {
            "success": True,
            "text": response_text,
            "should_speak": True,
            "data": {
                "content_creation_started": True,
                "content_type": content_type,
                "topic": topic
            }
        }
    
    async def _handle_strategic_planning(self, command: VoiceCommand, session_id: str) -> Dict[str, Any]:
        """Handle strategic planning voice request"""
        
        response_text = "I'll initiate a strategic planning analysis. " \
                       "This includes market research, competitive analysis, and growth opportunities. " \
                       "The strategic planning agent will compile a comprehensive report for you."
        
        return {
            "success": True,
            "text": response_text,
            "should_speak": True,
            "data": {
                "strategic_planning_initiated": True
            }
        }
    
    async def _handle_system_control(self, command: VoiceCommand, session_id: str) -> Dict[str, Any]:
        """Handle system control voice request"""
        
        response_text = "System status: All agents are operational. " \
                       "Orchestration system is running with 4 active agents. " \
                       "Task completion rate is optimal and all services are healthy."
        
        return {
            "success": True,
            "text": response_text,
            "should_speak": True,
            "data": {
                "system_status_checked": True
            }
        }
    
    async def _handle_help_request(self, command: VoiceCommand, session_id: str) -> Dict[str, Any]:
        """Handle help and support voice request"""
        
        response_text = """I'm your AI co-founder assistant. I can help you with:
        Business metrics and performance tracking,
        Task creation and management,
        Content generation and strategy,
        Strategic planning and analysis,
        System monitoring and control.
        Just speak naturally and I'll understand what you need."""
        
        return {
            "success": True,
            "text": response_text,
            "should_speak": True,
            "data": {
                "help_provided": True,
                "capabilities_listed": True
            }
        }
    
    async def generate_speech_audio(self, text: str, voice_settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Generate speech audio from text"""
        try:
            settings = voice_settings or self.voice_settings
            
            # Simulate text-to-speech generation
            # In production, integrate with Azure Cognitive Services, Amazon Polly, etc.
            
            audio_result = await self._simulate_text_to_speech(text, settings)
            
            return {
                "success": True,
                "audio_url": audio_result.get("audio_url"),
                "duration": audio_result.get("duration", 0),
                "format": "mp3",
                "text": text,
                "voice_settings": settings
            }
            
        except Exception as e:
            self.logger.error(f"Error generating speech audio: {e}")
            return {"success": False, "error": str(e)}
    
    async def _simulate_text_to_speech(self, text: str, settings: Dict[str, Any]) -> Dict[str, Any]:
        """Simulate text-to-speech conversion"""
        
        # Calculate estimated duration (average speaking rate)
        words = len(text.split())
        words_per_minute = 150 * settings.get("speech_rate", 1.0)
        duration = (words / words_per_minute) * 60
        
        # Simulate processing delay
        await asyncio.sleep(min(2.0, duration * 0.1))
        
        # Return simulated audio URL
        audio_filename = f"tts_audio_{datetime.now().timestamp()}.mp3"
        
        return {
            "audio_url": f"/api/audio/{audio_filename}",
            "duration": duration,
            "processing_time": min(2.0, duration * 0.1)
        }
    
    async def get_voice_session_data(self, session_id: str) -> Dict[str, Any]:
        """Get voice session data and statistics"""
        
        session_commands = [cmd for cmd in self.voice_commands if session_id in cmd.id]
        
        return {
            "session_id": session_id,
            "total_commands": len(session_commands),
            "successful_commands": len([cmd for cmd in session_commands if cmd.processed]),
            "average_confidence": sum(cmd.confidence for cmd in session_commands) / len(session_commands) if session_commands else 0,
            "recent_commands": [
                {
                    "text": cmd.text,
                    "intent": cmd.intent,
                    "confidence": cmd.confidence,
                    "timestamp": cmd.timestamp.isoformat()
                }
                for cmd in session_commands[-5:]  # Last 5 commands
            ],
            "voice_settings": self.voice_settings
        }
    
    async def update_voice_settings(self, new_settings: Dict[str, Any]) -> Dict[str, Any]:
        """Update voice interface settings"""
        
        try:
            # Validate settings
            valid_keys = ["speech_rate", "pitch", "volume", "voice_id", "language"]
            
            for key, value in new_settings.items():
                if key in valid_keys:
                    self.voice_settings[key] = value
            
            return {
                "success": True,
                "updated_settings": self.voice_settings,
                "message": "Voice settings updated successfully"
            }
            
        except Exception as e:
            self.logger.error(f"Error updating voice settings: {e}")
            return {"success": False, "error": str(e)}
    
    async def get_voice_analytics(self) -> Dict[str, Any]:
        """Get voice interface usage analytics"""
        
        total_commands = len(self.voice_commands)
        
        if total_commands == 0:
            return {"message": "No voice commands processed yet"}
        
        # Calculate analytics
        successful_commands = len([cmd for cmd in self.voice_commands if cmd.processed])
        success_rate = successful_commands / total_commands
        
        # Intent distribution
        intent_counts = {}
        for cmd in self.voice_commands:
            intent = cmd.intent or "unknown"
            intent_counts[intent] = intent_counts.get(intent, 0) + 1
        
        # Average confidence
        avg_confidence = sum(cmd.confidence for cmd in self.voice_commands) / total_commands
        
        # Recent activity (last 24 hours)
        recent_cutoff = datetime.now().timestamp() - (24 * 60 * 60)
        recent_commands = [cmd for cmd in self.voice_commands if cmd.timestamp.timestamp() > recent_cutoff]
        
        return {
            "total_commands": total_commands,
            "success_rate": success_rate,
            "average_confidence": avg_confidence,
            "intent_distribution": intent_counts,
            "recent_activity": len(recent_commands),
            "most_common_intent": max(intent_counts.items(), key=lambda x: x[1])[0] if intent_counts else None,
            "voice_settings": self.voice_settings
        }