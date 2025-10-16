
    # Content Pipeline Methods
    async def add_content_task(self, task_data: Dict[str, Any]) -> str:
        """Add a new content creation task to the content_tasks collection"""
        if not self._check_db_available():
            import uuid
            mock_id = str(uuid.uuid4())
            logger.info("Content task created in DEV MODE (not persisted)", task_id=mock_id, topic=task_data.get("topic"))
            return mock_id
            
        try:
            enhanced_task_data = {
                "topic": task_data.get("topic"),
                "primary_keyword": task_data.get("primary_keyword", ""),
                "target_audience": task_data.get("target_audience", "general"),
                "category": task_data.get("category", "uncategorized"),
                "status": task_data.get("status", "New"),
                "auto_publish": task_data.get("auto_publish", False),
                "source": task_data.get("source", "api"),
                "created_at": FIRESTORE_STUBS.SERVER_TIMESTAMP,
                "updated_at": FIRESTORE_STUBS.SERVER_TIMESTAMP,
            }
            
            doc_ref = self.db.collection('content_tasks').document()
            doc_ref.set(enhanced_task_data)
            
            logger.info("Content task created", task_id=doc_ref.id, topic=task_data.get("topic"))
            return doc_ref.id
            
        except Exception as e:
            logger.error("Failed to add content task", error=str(e))
            raise
    
    async def get_content_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get a content task by ID"""
        if not self._check_db_available():
            logger.debug("Content task retrieval skipped - running in dev mode")
            return None
            
        try:
            doc_ref = self.db.collection('content_tasks').document(task_id)
            doc = doc_ref.get()
            
            if doc.exists:
                task_data = doc.to_dict()
                task_data['id'] = doc.id
                return task_data
            else:
                logger.warning("Content task not found", task_id=task_id)
                return None
                
        except Exception as e:
            logger.error("Failed to get content task", task_id=task_id, error=str(e))
            raise
    
    async def get_task_runs(self, task_id: str) -> List[Dict[str, Any]]:
        """Get all run logs for a content task"""
        if not self._check_db_available():
            return []
            
        try:
            runs_ref = self.db.collection('runs').where('task_id', '==', task_id)
            docs = runs_ref.order_by('created_at', direction=FIRESTORE_STUBS.Query.DESCENDING).stream()
            
            runs = []
            for doc in docs:
                run_data = doc.to_dict()
                run_data['id'] = doc.id
                runs.append(run_data)
            
            return runs
            
        except Exception as e:
            logger.error("Failed to get task runs", task_id=task_id, error=str(e))
            return []
    
    async def log_webhook_event(self, webhook_data: Dict[str, Any]) -> str:
        """Log a webhook event from Strapi"""
        if not self._check_db_available():
            import uuid
            mock_id = str(uuid.uuid4())
            logger.info("Webhook event logged in DEV MODE (not persisted)", event_id=mock_id, event=webhook_data.get("event"))
            return mock_id
            
        try:
            enhanced_webhook_data = {
                **webhook_data,
                "logged_at": FIRESTORE_STUBS.SERVER_TIMESTAMP
            }
            
            doc_ref = self.db.collection('webhooks').document()
            doc_ref.set(enhanced_webhook_data)
            
            logger.info("Webhook event logged", event_id=doc_ref.id, event=webhook_data.get("event"))
            return doc_ref.id
            
        except Exception as e:
            logger.error("Failed to log webhook event", error=str(e))
            raise
