import logging
from ..services.llm_client import LLMClient

logger = logging.getLogger(__name__)

class SummarizerAgent:
    """An agent dedicated to summarizing text."""

    def __init__(self, llm_client: LLMClient):
        """Initializes the SummarizerAgent with an LLM client."""
        self.llm_client = llm_client
        self.tools = CrewAIToolsFactory.get_content_agent_tools()

    def run(self, text_to_summarize: str, prompt_template: str) -> str:
        """
        Summarizes the given text using the provided prompt template.

        Args:
            text_to_summarize (str): The text to be summarized.
            prompt_template (str): The prompt template for the summarization task.

        Returns:
            str: The summarized text.
        """
        if not text_to_summarize:
            logger.warning("SummarizerAgent: No text provided to summarize.")
            return ""

        try:
            prompt = prompt_template.format(text=text_to_summarize)
            logger.info("SummarizerAgent: Summarizing text.")
            summary = self.llm_client.generate_summary(prompt)
            return summary
        except Exception as e:
            logger.error(f"SummarizerAgent: An error occurred during summarization: {e}")
            return ""  # Return empty string on failure
