"""
Google Cloud Pub/Sub client for GLAD Labs AI Co-Founder
Implements asynchronous messaging for agent orchestration and // INTERVENE protocol
"""

import os
import json
import asyncio
import logging
from typing import Dict, Any, Callable, Optional
from concurrent.futures import ThreadPoolExecutor
try:
    from google.cloud import pubsub_v1  # type: ignore
    from google.cloud.pubsub_v1.types import PushConfig  # type: ignore
except Exception:  # pragma: no cover - optional dependency at dev time
    pubsub_v1 = None  # type: ignore
    class PushConfig:  # type: ignore
        pass
import structlog

# Configure structured logging
logger = structlog.get_logger(__name__)

class PubSubClient:
    """
    Pub/Sub client for orchestrating GLAD Labs agent communication
    
    Topics:
    - agent-commands: Commands to individual agents
    - agent-responses: Responses from agents
    - intervene-protocol: Emergency intervention signals
    - content-pipeline: Content creation workflow
    """
    
    def __init__(self, project_id: Optional[str] = None):
        """Initialize Pub/Sub client with project configuration"""
        self.project_id = project_id or os.getenv('GCP_PROJECT_ID')
        self.dev_mode = os.getenv('DEV_MODE', 'false').lower() == 'true' or os.getenv('USE_MOCK_SERVICES', 'false').lower() == 'true'
        
        if not self.project_id:
            logger.warning("No GCP_PROJECT_ID found, using default project")
            self.project_id = "glad-labs-dev-local"
        
        try:
            if pubsub_v1 is None:
                raise RuntimeError("google-cloud-pubsub not installed or import failed")
            # Initialize publisher and subscriber clients
            self.publisher = pubsub_v1.PublisherClient()
            self.subscriber = pubsub_v1.SubscriberClient()
            
            # Create topic paths
            self.topics = {
                'agent_commands': self.publisher.topic_path(self.project_id, 'agent-commands'),
                'agent_responses': self.publisher.topic_path(self.project_id, 'agent-responses'),
                'intervene_protocol': self.publisher.topic_path(self.project_id, 'intervene-protocol'),
                'content_pipeline': self.publisher.topic_path(self.project_id, 'content-pipeline')
            }
            
            if self.dev_mode:
                logger.info("Pub/Sub client initialized in DEV MODE (local/mock services)", 
                           project_id=self.project_id,
                           topics=list(self.topics.keys()))
            else:
                logger.info("Pub/Sub client initialized", 
                           project_id=self.project_id,
                           topics=list(self.topics.keys()))
            
        except Exception as e:
            logger.error("Failed to initialize Pub/Sub client", error=str(e))
            if not self.dev_mode:
                raise
            else:
                logger.warning("Continuing in dev mode without Pub/Sub functionality")
    
    async def ensure_topics_exist(self) -> bool:
        """Ensure all required topics exist, create if missing"""
        # Skip in dev mode to avoid GCP authentication errors
        if self.dev_mode:
            logger.info("Skipping topic creation in dev mode")
            return True
            
        try:
            for topic_name, topic_path in self.topics.items():
                try:
                    self.publisher.get_topic(request={"topic": topic_path})
                    logger.info("Topic exists", topic=topic_name)
                except Exception:
                    # Topic doesn't exist, create it
                    self.publisher.create_topic(request={"name": topic_path})
                    logger.info("Topic created", topic=topic_name)
            
            return True
            
        except Exception as e:
            logger.error("Failed to ensure topics exist", error=str(e))
            return False
    
    async def publish_agent_command(self, agent_name: str, command: Dict[str, Any]) -> str:
        """
        Publish a command to a specific agent
        
        Args:
            agent_name: Target agent identifier
            command: Command data including action and parameters
            
        Returns:
            Message ID of published command
        """
        try:
            message_data = {
                'target_agent': agent_name,
                'command': command,
                'timestamp': str(asyncio.get_event_loop().time()),
                'source': 'cofounder-agent'
            }
            
            # Convert to JSON bytes
            message_bytes = json.dumps(message_data).encode('utf-8')
            
            # Add message attributes
            attributes = {
                'agent': agent_name,
                'command_type': command.get('action', 'unknown'),
                'source': 'cofounder'
            }
            
            # Publish the message
            future = self.publisher.publish(
                self.topics['agent_commands'], 
                message_bytes,
                **attributes
            )
            
            message_id = future.result()
            
            logger.info("Agent command published", 
                       agent_name=agent_name,
                       command_action=command.get('action'),
                       message_id=message_id)
            
            return message_id
            
        except Exception as e:
            logger.error("Failed to publish agent command", 
                        agent_name=agent_name,
                        command=command,
                        error=str(e))
            raise
    
    async def publish_content_request(self, content_request: Dict[str, Any]) -> str:
        """
        Publish a content creation request to the content pipeline
        
        Args:
            content_request: Content specifications including topic, format, metadata
            
        Returns:
            Message ID of published request
        """
        try:
            message_data = {
                'request_type': 'content_creation',
                'specifications': content_request,
                'timestamp': str(asyncio.get_event_loop().time()),
                'source': 'cofounder-agent'
            }
            
            message_bytes = json.dumps(message_data).encode('utf-8')
            
            attributes = {
                'content_type': content_request.get('type', 'unknown'),
                'priority': content_request.get('priority', 'normal'),
                'source': 'cofounder'
            }
            
            future = self.publisher.publish(
                self.topics['content_pipeline'],
                message_bytes,
                **attributes
            )
            
            message_id = future.result()
            
            logger.info("Content request published",
                       content_type=content_request.get('type'),
                       topic=content_request.get('topic'),
                       message_id=message_id)
            
            return message_id
            
        except Exception as e:
            logger.error("Failed to publish content request",
                        content_request=content_request,
                        error=str(e))
            raise
    
    async def trigger_intervene_protocol(self, intervention_data: Dict[str, Any]) -> str:
        """
        Trigger the // INTERVENE protocol for emergency situations
        
        Args:
            intervention_data: Intervention details including reason and required actions
            
        Returns:
            Message ID of intervention signal
        """
        try:
            message_data = {
                'protocol': 'INTERVENE',
                'intervention_data': intervention_data,
                'timestamp': str(asyncio.get_event_loop().time()),
                'severity': intervention_data.get('severity', 'high'),
                'source': 'cofounder-agent'
            }
            
            message_bytes = json.dumps(message_data).encode('utf-8')
            
            attributes = {
                'protocol': 'INTERVENE',
                'severity': intervention_data.get('severity', 'high'),
                'reason': intervention_data.get('reason', 'unknown'),
                'source': 'cofounder'
            }
            
            future = self.publisher.publish(
                self.topics['intervene_protocol'],
                message_bytes,
                **attributes
            )
            
            message_id = future.result()
            
            logger.critical("INTERVENE protocol triggered",
                          reason=intervention_data.get('reason'),
                          severity=intervention_data.get('severity'),
                          message_id=message_id)
            
            return message_id
            
        except Exception as e:
            logger.error("Failed to trigger INTERVENE protocol",
                        intervention_data=intervention_data,
                        error=str(e))
            raise
    
    def create_subscription_handler(self, callback: Callable[[Dict[str, Any]], None]) -> Callable:
        """
        Create a subscription message handler
        
        Args:
            callback: Function to handle received messages
            
        Returns:
            Configured message handler function
        """
        def message_handler(message):
            try:
                # Parse message data
                data = json.loads(message.data.decode('utf-8'))
                
                # Add message metadata
                data['_message_id'] = message.message_id
                data['_attributes'] = dict(message.attributes)
                data['_publish_time'] = message.publish_time
                
                # Call the callback
                callback(data)
                
                # Acknowledge the message
                message.ack()
                
                logger.info("Message processed successfully",
                          message_id=message.message_id)
                
            except Exception as e:
                logger.error("Failed to process message",
                           message_id=message.message_id,
                           error=str(e))
                # Don't acknowledge failed messages for retry
                message.nack()
        
        return message_handler
    
    async def start_agent_response_listener(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        """
        Start listening for agent responses
        
        Args:
            callback: Function to handle agent response messages
        """
        try:
            subscription_path = self.subscriber.subscription_path(
                self.project_id, 
                'agent-responses-subscription'
            )
            
            # Create subscription if it doesn't exist
            try:
                self.subscriber.get_subscription(request={"subscription": subscription_path})
            except Exception:
                self.subscriber.create_subscription(
                    request={
                        "name": subscription_path,
                        "topic": self.topics['agent_responses']
                    }
                )
                logger.info("Created agent responses subscription")
            
            # Create message handler
            handler = self.create_subscription_handler(callback)
            
            # Start listening in a thread pool
            with ThreadPoolExecutor(max_workers=4) as executor:
                streaming_pull_future = self.subscriber.subscribe(
                    subscription_path, 
                    callback=handler
                )
                
                logger.info("Started agent response listener")
                
                try:
                    # Keep the listener alive
                    streaming_pull_future.result()
                except KeyboardInterrupt:
                    streaming_pull_future.cancel()
                    logger.info("Agent response listener stopped")
                    
        except Exception as e:
            logger.error("Failed to start agent response listener", error=str(e))
            raise
    
    async def health_check(self) -> Dict[str, Any]:
        """Perform a basic health check on Pub/Sub connection"""
        try:
            # Test publishing a health check message
            test_message = {
                'type': 'health_check',
                'timestamp': str(asyncio.get_event_loop().time())
            }
            
            message_bytes = json.dumps(test_message).encode('utf-8')
            
            # Try to publish to agent_commands topic
            future = self.publisher.publish(
                self.topics['agent_commands'],
                message_bytes,
                health_check='true'
            )
            
            message_id = future.result()
            
            return {
                'status': 'healthy',
                'project_id': self.project_id,
                'test_message_id': message_id,
                'topics_configured': len(self.topics)
            }
            
        except Exception as e:
            logger.error("Pub/Sub health check failed", error=str(e))
            return {
                'status': 'unhealthy',
                'error': str(e)
            }
    
    async def close(self):
        """Clean up Pub/Sub client resources"""
        try:
            # Close clients
            if hasattr(self, 'publisher'):
                self.publisher.close()
            if hasattr(self, 'subscriber'):
                self.subscriber.close()
                
            logger.info("Pub/Sub client closed")
            
        except Exception as e:
            logger.error("Error closing Pub/Sub client", error=str(e))