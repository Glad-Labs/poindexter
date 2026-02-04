import logging

logger = logging.getLogger(__name__)


class ContentAgentOrchestrator:
    def __init__(self, api_url: str = "http://localhost:8000"):
        self.api_url = api_url
        self.is_running = False
        self.pubsub_client = None
        logger.info("Initialized ContentAgentOrchestrator (API: %s)", api_url)

    async def start_polling(self, interval: int = 30):
        self.is_running = True
        logger.info("Starting task polling (interval: %ss)", interval)
