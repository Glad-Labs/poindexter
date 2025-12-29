"""
Settings management methods for DatabaseService.

These methods provide CRUD operations for application settings stored in PostgreSQL.
Settings are stored as key-value pairs with support for validation, encryption, and versioning.
"""

# This module contains the settings methods to be added to DatabaseService
# File: src/cofounder_agent/services/database_service.py
# Insert after task management methods (around line 1000)

SETTINGS_METHODS = """

    # SETTINGS MANAGEMENT
    # ========================================================================
    
    async def get_setting(self, key: str) -> Optional[Dict[str, Any]]:
        \"\"\"
        Get a setting by key.
        
        Args:
            key: Setting key identifier
            
        Returns:
            Setting dict or None if not found
        \"\"\"
        sql = \"SELECT * FROM settings WHERE key = $1 AND is_active = true\"
        
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(sql, key)
                if row:
                    return self._convert_row_to_dict(row)
                return None
        except Exception as e:
            logger.error(f"❌ Failed to get setting {key}: {e}")
            return None
    
    async def get_all_settings(self, category: Optional[str] = None) -> List[Dict[str, Any]]:
        \"\"\"
        Get all active settings, optionally filtered by category.
        
        Args:
            category: Optional category filter
            
        Returns:
            List of setting dicts
        \"\"\"
        if category:
            sql = \"SELECT * FROM settings WHERE category = $1 AND is_active = true ORDER BY key\"
            params = [category]
        else:
            sql = \"SELECT * FROM settings WHERE is_active = true ORDER BY key\"
            params = []
        
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(sql, *params)
                return [self._convert_row_to_dict(row) for row in rows]
        except Exception as e:
            logger.error(f"❌ Failed to get settings: {e}")
            return []
    
    async def set_setting(
        self,
        key: str,
        value: Any,
        category: Optional[str] = None,
        display_name: Optional[str] = None,
        description: Optional[str] = None
    ) -> bool:
        \"\"\"
        Create or update a setting.
        
        Args:
            key: Setting key identifier
            value: Setting value (will be stored as text)
            category: Optional category for grouping
            display_name: Optional display name for UI
            description: Optional description
            
        Returns:
            True if successful
        \"\"\"
        try:
            value_str = json.dumps(value) if isinstance(value, (dict, list)) else str(value)
            
            async with self.pool.acquire() as conn:
                await conn.execute(
                    \"\"\"
                    INSERT INTO settings (key, value, category, display_name, description, is_active, modified_at)
                    VALUES ($1, $2, $3, $4, $5, true, NOW())
                    ON CONFLICT (key) DO UPDATE SET
                        value = $2,
                        category = $3,
                        display_name = $4,
                        description = $5,
                        modified_at = NOW()
                    \"\"\",
                    key, value_str, category, display_name, description
                )
                logger.info(f"✅ Setting saved: {key} = {value_str[:50]}")
                return True
        except Exception as e:
            logger.error(f"❌ Failed to set setting {key}: {e}")
            return False
    
    async def delete_setting(self, key: str) -> bool:
        \"\"\"
        Soft delete a setting (mark as inactive).
        
        Args:
            key: Setting key identifier
            
        Returns:
            True if successful
        \"\"\"
        try:
            async with self.pool.acquire() as conn:
                result = await conn.execute(
                    \"UPDATE settings SET is_active = false, modified_at = NOW() WHERE key = $1\",
                    key
                )
                logger.info(f\"✅ Setting deleted: {key}\")
                return True
        except Exception as e:
            logger.error(f\"❌ Failed to delete setting {key}: {e}\")
            return False
    
    async def get_setting_value(self, key: str, default: Any = None) -> Any:
        \"\"\"
        Get just the value of a setting, with optional default.
        
        Args:
            key: Setting key identifier
            default: Default value if not found
            
        Returns:
            Setting value or default
        \"\"\"
        setting = await self.get_setting(key)
        if not setting or not setting.get('value'):
            return default
        
        # Try to parse as JSON if it looks like JSON
        value_str = setting['value']
        try:
            return json.loads(value_str)
        except:
            return value_str
    
    async def setting_exists(self, key: str) -> bool:
        \"\"\"
        Check if a setting exists and is active.
        
        Args:
            key: Setting key identifier
            
        Returns:
            True if setting exists and is active
        \"\"\"
        sql = \"SELECT EXISTS(SELECT 1 FROM settings WHERE key = $1 AND is_active = true)\"
        try:
            async with self.pool.acquire() as conn:
                result = await conn.fetchval(sql, key)
                return result or False
        except Exception as e:
            logger.error(f\"❌ Failed to check setting {key}: {e}\")
            return False
"""

print(SETTINGS_METHODS)
