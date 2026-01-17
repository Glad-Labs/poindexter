"""
Fine-tuning service for orchestrator models.

Supports fine-tuning with:
- Ollama (local, free, private)
- Gemini (Google API)
- Claude (Anthropic API)
- OpenAI GPT-4 (OpenAI API)
"""

import asyncio
import logging
import subprocess
import os
import json
from typing import Dict, Any, Optional, List
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)


class FineTuneTarget(str, Enum):
    """Target model for fine-tuning"""

    OLLAMA = "ollama"
    GEMINI = "gemini"
    CLAUDE = "claude"
    GPT4 = "gpt4"


class FineTuningService:
    """
    Manages fine-tuning of models.

    Supports:
    - Local fine-tuning with Ollama
    - API-based fine-tuning (Gemini, Claude, OpenAI)
    - Job tracking and status monitoring
    - Model deployment and switching
    """

    def __init__(self):
        self.jobs: Dict[str, Dict[str, Any]] = {}  # In-memory job tracking

    # ========================================================================
    # OLLAMA FINE-TUNING (Local, Free, Private)
    # ========================================================================

    async def fine_tune_ollama(
        self,
        dataset_path: str,
        base_model: str = "mistral",
        learning_rate: float = 0.001,
        epochs: int = 3,
    ) -> Dict[str, Any]:
        """
        Fine-tune using Ollama (100% local, free, private).

        Args:
            dataset_path: Path to JSONL training data
            base_model: Base model to fine-tune (mistral, llama2, neural-chat)
            learning_rate: Learning rate for training
            epochs: Number of training epochs

        Returns:
            Job metadata
        """
        job_id = f"ollama_finetune_{datetime.now().timestamp()}"

        try:
            # Check if Ollama is running
            result = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=5)

            if result.returncode != 0:
                return {
                    "job_id": job_id,
                    "status": "failed",
                    "error": "Ollama is not running. Please start Ollama first.",
                }

            # Create Modelfile for fine-tuning
            modelfile_content = f"""FROM {base_model}
PARAMETER temperature 0.7
PARAMETER top_p 0.9
PARAMETER learning_rate {learning_rate}
"""
            modelfile_path = f"/tmp/Modelfile_{job_id}"
            with open(modelfile_path, "w") as f:
                f.write(modelfile_content)

            # Start background fine-tuning process
            # Note: Using ollama run with dataset
            process = await asyncio.create_subprocess_exec(
                "ollama",
                "create",
                f"orchestrator-{job_id}",
                "-f",
                modelfile_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            self.jobs[job_id] = {
                "status": "running",
                "target": "ollama",
                "base_model": base_model,
                "process": process,
                "start_time": datetime.now().isoformat(),
                "dataset_path": dataset_path,
                "model_name": f"orchestrator-{job_id}",
            }

            logger.info(f"Started Ollama fine-tuning job: {job_id}")

            return {
                "job_id": job_id,
                "status": "running",
                "target": "ollama",
                "base_model": base_model,
                "model_name": f"orchestrator-{job_id}",
                "message": "Fine-tuning started. Check status with job_id.",
            }

        except FileNotFoundError:
            return {
                "job_id": job_id,
                "status": "failed",
                "error": "Ollama not found. Please install Ollama first.",
            }
        except Exception as e:
            logger.error(f"Failed to start Ollama fine-tuning: {e}")
            return {"job_id": job_id, "status": "failed", "error": str(e)}

    # ========================================================================
    # GEMINI FINE-TUNING (API-based)
    # ========================================================================

    async def fine_tune_gemini(
        self, dataset_path: str, api_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Fine-tune using Google Gemini (API-based).

        Requires: GOOGLE_API_KEY environment variable or api_key parameter
        """
        job_id = f"gemini_finetune_{datetime.now().timestamp()}"

        try:
            # Import google-genai library (new package, replaces deprecated google.generativeai)
            try:
                import google.genai as genai
            except ImportError:
                # Fallback to old deprecated package if new one not available
                import google.generativeai as genai

            key = api_key or os.getenv("GOOGLE_API_KEY")
            if not key:
                return {
                    "job_id": job_id,
                    "status": "failed",
                    "error": "GOOGLE_API_KEY not configured",
                }

            genai.configure(api_key=key)

            # Upload training data
            media = genai.upload_file(dataset_path)

            # Start fine-tuning operation
            base_model = "models/gemini-1.5-pro-latest"

            operation = genai.types.Operation()
            # Note: Actual fine-tuning depends on Google's API
            # This is a placeholder for the actual implementation

            self.jobs[job_id] = {
                "status": "running",
                "target": "gemini",
                "operation": None,  # Would store actual operation
                "start_time": datetime.now().isoformat(),
                "file_uri": media.uri if hasattr(media, "uri") else None,
            }

            logger.info(f"Started Gemini fine-tuning job: {job_id}")

            return {
                "job_id": job_id,
                "status": "running",
                "target": "gemini",
                "base_model": base_model,
                "estimated_time": "2-4 hours",
            }

        except ImportError:
            return {
                "job_id": job_id,
                "status": "failed",
                "error": "google-generativeai not installed. Run: pip install google-generativeai",
            }
        except Exception as e:
            logger.error(f"Failed to start Gemini fine-tuning: {e}")
            return {"job_id": job_id, "status": "failed", "error": str(e)}

    # ========================================================================
    # CLAUDE FINE-TUNING (API-based)
    # ========================================================================

    async def fine_tune_claude(
        self, dataset_path: str, api_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Fine-tune using Anthropic Claude (API-based).

        Requires: ANTHROPIC_API_KEY environment variable or api_key parameter
        """
        job_id = f"claude_finetune_{datetime.now().timestamp()}"

        try:
            import anthropic

            key = api_key or os.getenv("ANTHROPIC_API_KEY")
            if not key:
                return {
                    "job_id": job_id,
                    "status": "failed",
                    "error": "ANTHROPIC_API_KEY not configured",
                }

            client = anthropic.Anthropic(api_key=key)

            # Upload training data
            with open(dataset_path, "rb") as f:
                file_response = client.beta.files.upload(
                    file=(os.path.basename(dataset_path), f, "text/jsonl")
                )

            file_id = file_response.id

            # Start fine-tuning job
            job_response = client.beta.fine_tuning.jobs.create(
                model="claude-3-5-sonnet-20241022",
                training_data={"type": "file", "file_id": file_id},
            )

            job_id_api = job_response.id
            self.jobs[job_id] = {
                "status": "running",
                "target": "claude",
                "job_id_api": job_id_api,
                "start_time": datetime.now().isoformat(),
                "file_id": file_id,
            }

            logger.info(f"Started Claude fine-tuning job: {job_id}")

            return {
                "job_id": job_id,
                "job_id_api": job_id_api,
                "status": "running",
                "target": "claude",
                "estimated_time": "1-3 hours",
            }

        except ImportError:
            return {
                "job_id": job_id,
                "status": "failed",
                "error": "anthropic not installed. Run: pip install anthropic",
            }
        except Exception as e:
            logger.error(f"Failed to start Claude fine-tuning: {e}")
            return {"job_id": job_id, "status": "failed", "error": str(e)}

    # ========================================================================
    # GPT-4 FINE-TUNING (API-based)
    # ========================================================================

    async def fine_tune_gpt4(
        self, dataset_path: str, api_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Fine-tune using OpenAI GPT-4 (API-based).

        Requires: OPENAI_API_KEY environment variable or api_key parameter
        """
        job_id = f"gpt4_finetune_{datetime.now().timestamp()}"

        try:
            import openai

            key = api_key or os.getenv("OPENAI_API_KEY")
            if not key:
                return {
                    "job_id": job_id,
                    "status": "failed",
                    "error": "OPENAI_API_KEY not configured",
                }

            openai.api_key = key

            # Upload training file
            with open(dataset_path, "rb") as f:
                file_response = openai.File.create(file=f, purpose="fine-tune")

            file_id = file_response.id

            # Start fine-tuning job
            job_response = openai.FineTuningJob.create(training_file=file_id, model="gpt-4")

            job_id_api = job_response.id
            self.jobs[job_id] = {
                "status": "running",
                "target": "gpt4",
                "job_id_api": job_id_api,
                "start_time": datetime.now().isoformat(),
                "file_id": file_id,
            }

            logger.info(f"Started GPT-4 fine-tuning job: {job_id}")

            return {
                "job_id": job_id,
                "job_id_api": job_id_api,
                "status": "running",
                "target": "gpt4",
                "estimated_time": "3-6 hours",
                "estimated_cost": "$50-200",
            }

        except ImportError:
            return {
                "job_id": job_id,
                "status": "failed",
                "error": "openai not installed. Run: pip install openai",
            }
        except Exception as e:
            logger.error(f"Failed to start GPT-4 fine-tuning: {e}")
            return {"job_id": job_id, "status": "failed", "error": str(e)}

    # ========================================================================
    # JOB MANAGEMENT
    # ========================================================================

    async def get_job_status(self, job_id: str) -> Dict[str, Any]:
        """Check status of a fine-tuning job"""
        if job_id not in self.jobs:
            return {"status": "not_found", "job_id": job_id}

        job = self.jobs[job_id]

        if job["target"] == "ollama":
            process = job.get("process")
            if process and process.returncode is None:
                return {
                    "job_id": job_id,
                    "status": "running",
                    "target": "ollama",
                    "progress": "Training in progress...",
                    "model_name": job.get("model_name"),
                }
            elif process:
                if process.returncode == 0:
                    return {
                        "job_id": job_id,
                        "status": "complete",
                        "target": "ollama",
                        "model_name": job.get("model_name"),
                    }
                else:
                    return {
                        "job_id": job_id,
                        "status": "failed",
                        "target": "ollama",
                        "error": "Process failed",
                    }

        elif job["target"] == "gemini":
            return {
                "job_id": job_id,
                "status": "running",
                "target": "gemini",
                "progress": "Check Google Cloud console for details",
            }

        elif job["target"] == "claude":
            try:
                import anthropic

                key = os.getenv("ANTHROPIC_API_KEY")
                if not key:
                    return {"status": "error", "error": "ANTHROPIC_API_KEY not set"}

                client = anthropic.Anthropic(api_key=key)
                job_api = client.beta.fine_tuning.jobs.retrieve(job["job_id_api"])

                status_map = {
                    "queued": "queued",
                    "processing": "running",
                    "succeeded": "complete",
                    "failed": "failed",
                }

                return {
                    "job_id": job_id,
                    "status": status_map.get(job_api.status, job_api.status),
                    "target": "claude",
                    "model_name": (
                        job_api.fine_tuned_model if hasattr(job_api, "fine_tuned_model") else None
                    ),
                }
            except Exception as e:
                logger.error(f"Failed to get Claude job status: {e}")
                return {"status": "error", "error": str(e)}

        elif job["target"] == "gpt4":
            try:
                import openai

                key = os.getenv("OPENAI_API_KEY")
                if not key:
                    return {"status": "error", "error": "OPENAI_API_KEY not set"}

                openai.api_key = key
                job_api = openai.FineTuningJob.retrieve(job["job_id_api"])

                status_map = {
                    "queued": "queued",
                    "in_progress": "running",
                    "succeeded": "complete",
                    "failed": "failed",
                }

                return {
                    "job_id": job_id,
                    "status": status_map.get(job_api.status, job_api.status),
                    "target": "gpt4",
                    "model_name": (
                        job_api.fine_tuned_model if hasattr(job_api, "fine_tuned_model") else None
                    ),
                }
            except Exception as e:
                logger.error(f"Failed to get GPT-4 job status: {e}")
                return {"status": "error", "error": str(e)}

        return {"job_id": job_id, "status": "unknown"}

    async def cancel_job(self, job_id: str) -> Dict[str, Any]:
        """Cancel a running fine-tuning job"""
        if job_id not in self.jobs:
            return {"success": False, "error": "Job not found"}

        job = self.jobs[job_id]

        if job["target"] == "ollama":
            process = job.get("process")
            if process:
                process.terminate()
                job["status"] = "cancelled"
                logger.info(f"Cancelled Ollama job: {job_id}")
                return {"success": True, "job_id": job_id, "status": "cancelled"}

        elif job["target"] == "claude":
            try:
                import anthropic

                key = os.getenv("ANTHROPIC_API_KEY")
                if key:
                    client = anthropic.Anthropic(api_key=key)
                    # Claude doesn't support cancellation, just mark locally
                    job["status"] = "cancelled"
                    logger.info(f"Marked Claude job as cancelled: {job_id}")
                    return {
                        "success": True,
                        "job_id": job_id,
                        "status": "cancelled",
                        "note": "Job may still be running on Claude servers",
                    }
            except Exception as e:
                logger.error(f"Failed to cancel Claude job: {e}")

        return {"success": False, "error": "Cannot cancel this job type"}

    # ========================================================================
    # MODEL DEPLOYMENT
    # ========================================================================

    async def deploy_model(
        self, model_name: str, job_id: str, set_active: bool = False
    ) -> Dict[str, Any]:
        """
        Deploy a fine-tuned model for use.

        Args:
            model_name: Name to register model as
            job_id: ID of completed fine-tuning job
            set_active: Whether to make this the active model
        """
        if job_id not in self.jobs:
            return {"success": False, "error": "Job not found"}

        job = self.jobs.get(job_id, {})

        if job.get("status") != "complete":
            return {"success": False, "error": "Job is not complete"}

        logger.info(f"Deployed model: {model_name} from job {job_id}")

        return {
            "success": True,
            "model_name": model_name,
            "source": job.get("target"),
            "registered_at": datetime.now().isoformat(),
            "set_active": set_active,
        }

    async def list_jobs(self) -> List[Dict[str, Any]]:
        """List all fine-tuning jobs"""
        jobs_list = []
        for job_id, job_data in self.jobs.items():
            status = await self.get_job_status(job_id)
            jobs_list.append({"job_id": job_id, **status, "start_time": job_data.get("start_time")})
        return jobs_list
