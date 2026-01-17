"""
MCP Resource Manager

Manages access to MCP resources:
- Tasks (task://task_id)
- Models (model://model_name)
- Memory (memory://key)
"""

import logging
from typing import Any, Dict, Optional, List
from datetime import datetime


class ResourceManager:
    """Manages MCP resources"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.resources = {
            "task": {},
            "model": {},
            "memory": {},
        }
    
    def list_resources(self) -> List[Dict[str, Any]]:
        """List available resource types"""
        return [
            {
                "type": "task",
                "uri_template": "task://{task_id}",
                "description": "Task resources",
            },
            {
                "type": "model",
                "uri_template": "model://{model_name}",
                "description": "Model configuration resources",
            },
            {
                "type": "memory",
                "uri_template": "memory://{key}",
                "description": "Memory resources",
            },
        ]
    
    async def get_resource(
        self,
        resource_type: str,
        resource_id: str,
    ) -> Dict[str, Any]:
        """Get a resource"""
        
        if resource_type not in self.resources:
            raise ValueError(f"Unknown resource type: {resource_type}")
        
        if resource_id not in self.resources[resource_type]:
            raise ValueError(f"{resource_type.title()} '{resource_id}' not found")
        
        return self.resources[resource_type][resource_id]
    
    async def create_resource(
        self,
        resource_type: str,
        data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Create a new resource"""
        
        if resource_type not in self.resources:
            raise ValueError(f"Unknown resource type: {resource_type}")
        
        # Generate ID based on type
        if resource_type == "task":
            resource_id = f"task_{len(self.resources['task']) + 1}"
            resource = {
                "id": resource_id,
                "type": "task",
                **data,
                "created_at": datetime.utcnow().isoformat(),
            }
        elif resource_type == "model":
            resource_id = data.get("name", f"model_{len(self.resources['model']) + 1}")
            resource = {
                "id": resource_id,
                "type": "model",
                **data,
                "created_at": datetime.utcnow().isoformat(),
            }
        elif resource_type == "memory":
            resource_id = data.get("key", f"mem_{len(self.resources['memory']) + 1}")
            resource = {
                "id": resource_id,
                "type": "memory",
                **data,
                "created_at": datetime.utcnow().isoformat(),
            }
        else:
            raise ValueError(f"Unknown resource type: {resource_type}")
        
        self.resources[resource_type][resource_id] = resource
        self.logger.info(f"Created resource: {resource_type}/{resource_id}")
        
        return resource
    
    async def update_resource(
        self,
        resource_type: str,
        resource_id: str,
        data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Update a resource"""
        
        if resource_type not in self.resources:
            raise ValueError(f"Unknown resource type: {resource_type}")
        
        if resource_id not in self.resources[resource_type]:
            raise ValueError(f"{resource_type.title()} '{resource_id}' not found")
        
        resource = self.resources[resource_type][resource_id]
        resource.update(data)
        resource["updated_at"] = datetime.utcnow().isoformat()
        
        self.logger.info(f"Updated resource: {resource_type}/{resource_id}")
        
        return resource
    
    async def delete_resource(
        self,
        resource_type: str,
        resource_id: str,
    ) -> bool:
        """Delete a resource"""
        
        if resource_type not in self.resources:
            raise ValueError(f"Unknown resource type: {resource_type}")
        
        if resource_id not in self.resources[resource_type]:
            raise ValueError(f"{resource_type.title()} '{resource_id}' not found")
        
        del self.resources[resource_type][resource_id]
        self.logger.info(f"Deleted resource: {resource_type}/{resource_id}")
        
        return True
