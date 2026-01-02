import logging
import subprocess
import os
from src.agents.content_agent.utils.tools import CrewAIToolsFactory

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

    def run_security_audit(self) -> str:
        """
        Runs a security audit on the codebase, including dependency scans.
        (This is a placeholder implementation)
        """
        try:
            logging.info("Running security audit...")
            
            # Run npm audit
            npm_audit_result = self._run_command("npm audit")
            
            # Run pip-audit
            pip_audit_result = self._run_command("pip-audit")

            # Run linters
            flake8_result = self._run_command("flake8 .")
            # Run ESLint against all workspaces that have a 'lint' script
            eslint_result = self._run_command("npm run lint --workspaces")

            response = (
                "Compliance audit complete. Here is the summary:\\n\\n"
                f"**NPM Dependency Audit:**\\n{npm_audit_result}\\n\\n"
                f"**PIP Dependency Audit:**\\n{pip_audit_result}\\n\\n"
                f"**Python Linting (Flake8):**\\n{flake8_result}\\n\\n"
                f"**Frontend Linting (ESLint):**\\n{eslint_result}"
            )
            return response

        except Exception as e:
            logging.error(f"Error during security audit: {e}")
            return "I'm sorry, I encountered an error while running the security audit."

    def _run_command(self, command: str) -> str:
        """A helper method to run a shell command and return its output."""
        try:
            # We run the command from the workspace root
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                cwd=self.workspace_root,
                check=True
            )
            return result.stdout
        except subprocess.CalledProcessError as e:
            # Return the error message if the command fails
            return e.stderr
