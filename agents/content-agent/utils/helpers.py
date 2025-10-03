import re
import json
from typing import Optional, Dict, Any
import logging # Add logging import

logger = logging.getLogger(__name__) # Add logger

def load_prompts_from_file(file_path: str) -> Dict[str, str]:
    """Loads and returns the prompts from a JSON file."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        logger.critical(f"FATAL: Prompts file not found at {file_path}. The application cannot start.")
        raise
    except json.JSONDecodeError:
        logger.critical(f"FATAL: Prompts file at {file_path} is corrupted. The application cannot start.")
        raise

def slugify(text: str) -> str:
    """
    Convert a string to a URL-friendly slug.
    - Converts to lowercase
    - Removes non-alphanumeric characters (except hyphens and spaces)
    - Replaces spaces and repeated hyphens with a single hyphen
    - Strips leading/trailing hyphens
    """
    if not text:
        return "untitled"
    # Convert to lowercase
    text = text.lower()
    # Remove characters that are not alphanumeric, spaces, or hyphens
    text = re.sub(r'[^\w\s-]', '', text).strip().lower()
    text = re.sub(r'[-\s]+', '-', text)
    return text

def extract_json_from_string(text: str) -> Optional[str]:
    """
    Finds and extracts the first valid JSON object or array from a string.
    Handles cases where JSON is embedded in text or markdown code blocks.
    """
    # Regex to find content between ```json and ```, matching either an object or an array.
    json_match = re.search(r'```json\s*({.*?}|\[.*?\])\s*```', text, re.DOTALL)
    if json_match:
        return json_match.group(1)

    # Fallback regex to find the first complete JSON object or array in the string.
    json_match = re.search(r'({.*?}|\[.*?\])', text, re.DOTALL)
    if json_match:
        return json_match.group(0)
    
    logger.warning("Could not extract a valid JSON object or array from the provided text.")
    return None