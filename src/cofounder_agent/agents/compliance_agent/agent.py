import asyncio
import logging

from ..content_agent.utils.tools import CrewAIToolsFactory


class ComplianceAgent:
    """
    An agent responsible for reviewing the codebase for security best practices
    and flagging potential risks.
    """

    def __init__(self, workspace_root: str):
        self.workspace_root = workspace_root
        self.tools = [
            CrewAIToolsFactory.get_document_tool(),
            CrewAIToolsFactory.get_web_search_tool(),
        ]
        logging.info("Compliance Agent initialized.")

    async def run_security_audit(self) -> str:
        """
        Runs a security audit on the codebase, including dependency scans.
        (This is a placeholder implementation)
        """
        try:
            logging.info("Running security audit...")

            # Run npm audit
            npm_audit_result = await self._run_command(["npm", "audit"])

            # Run pip-audit
            pip_audit_result = await self._run_command(["pip-audit"])

            # Run linters
            flake8_result = await self._run_command(["flake8", "."])
            # Run ESLint against all workspaces that have a 'lint' script
            eslint_result = await self._run_command(["npm", "run", "lint", "--workspaces"])

            response = (
                "Compliance audit complete. Here is the summary:\\n\\n"
                f"**NPM Dependency Audit:**\\n{npm_audit_result}\\n\\n"
                f"**PIP Dependency Audit:**\\n{pip_audit_result}\\n\\n"
                f"**Python Linting (Flake8):**\\n{flake8_result}\\n\\n"
                f"**Frontend Linting (ESLint):**\\n{eslint_result}"
            )
            return response

        except Exception as e:
            logging.error(f"Error during security audit: {e}", exc_info=True)
            return "I'm sorry, I encountered an error while running the security audit."

    async def _run_command(self, args: list[str]) -> str:
        """A helper method to run a subprocess command and return its output."""
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.workspace_root,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                return stderr.decode(errors="replace")
            return stdout.decode(errors="replace")
        except Exception as e:
            logging.error(f"[_run_command] Failed to run {args}: {e}", exc_info=True)
            return f"Error running {args[0]}: {e}"
