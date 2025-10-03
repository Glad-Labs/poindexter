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
    Finds and extracts the first valid JSON object from a string.
    Handles cases where JSON is embedded in text or markdown code blocks.
    """
    # Regex to find content between ```json and ``` or the first { and last }
    json_match = re.search(r'```json\s*(\{.*?\})\s*```', text, re.DOTALL)
    if json_match:
        return json_match.group(1)

    # Fallback regex to find the first complete JSON object in the string
    json_match = re.search(r'\{.*\}', text, re.DOTALL)
    if json_match:
        # Find the substring that is a valid JSON
        potential_json = json_match.group(0)
        try:
            # Attempt to parse to confirm it's valid JSON
            json.loads(potential_json)
            return potential_json
        except json.JSONDecodeError:
            # This is a more complex case, try to find the boundaries
            open_braces = 0
            start_index = -1
            end_index = -1
            for i, char in enumerate(potential_json):
                if char == '{':
                    if start_index == -1:
                        start_index = i
                    open_braces += 1
                elif char == '}':
                    open_braces -= 1
                    if open_braces == 0 and start_index != -1:
                        end_index = i + 1
                        break
            if start_index != -1 and end_index != -1:
                return potential_json[start_index:end_index]

    return None