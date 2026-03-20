from services.logger_config import get_logger
from ..services.llm_client import LLMClient
from ..utils.tools import CrewAIToolsFactory

logger = get_logger(__name__)


class SummarizerAgent:
    def __init__(self, llm_client: LLMClient):
        self.llm_client = llm_client
        self._tools = CrewAIToolsFactory.get_content_agent_tools()

    def run(self, text: str, template: str) -> str:
        if not text:
            return ""
        try:
            prompt = template.format(text=text)
        except KeyError:
            return ""
        try:
            return self.llm_client.generate_summary(prompt)
        except Exception:
            logger.error("[summarizer] LLM call failed", exc_info=True)
            return ""
