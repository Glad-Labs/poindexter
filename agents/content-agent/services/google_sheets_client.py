import os
import logging
from datetime import datetime  # Add this import
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import httplib2  # Import httplib2
from config import config
from utils.data_models import BlogPost
from typing import Generator

SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

class GoogleSheetsClient:
    """Client to interact with the Google Sheets content calendar."""

    def __init__(self, credentials_file='credentials.json', token_file='token.json'):
        creds = None
        if os.path.exists(token_file):
            creds = Credentials.from_authorized_user_file(token_file, SCOPES)
        
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(credentials_file, SCOPES)
                creds = flow.run_local_server(port=0)
            with open(token_file, 'w') as token:
                token.write(creds.to_json())
        
        try:
            # --- Proxy/SSL Configuration ---
            # Check for a custom CA bundle environment variable, common in corporate environments
            # with SSL inspection proxies.
            ca_certs_path = os.environ.get('REQUESTS_CA_BUNDLE')
            
            if ca_certs_path:
                logging.info(f"Found custom CA bundle at {ca_certs_path}. Configuring HTTP client.")
                http_client = httplib2.Http(ca_certs=ca_certs_path)
                self.service = build('sheets', 'v4', credentials=creds, http=http_client)
            else:
                # Use the default HTTP client if no custom CA bundle is specified
                self.service = build('sheets', 'v4', credentials=creds)
            # --- End Configuration ---

            logging.info("Google Sheets client initialized successfully.")
        except HttpError as err:
            logging.error(f"An error occurred during Google Sheets client initialization: {err}")
            self.service = None
            raise  # Re-raise the exception to halt initialization
        except Exception as e:
            logging.error(f"An unexpected error occurred during Google Sheets client initialization: {e}")
            self.service = None
            raise  # Re-raise the exception to halt initialization

    def get_content_queue(self) -> Generator[BlogPost, None, None]:
        """
        Fetches rows from the content plan marked 'Ready' and yields them one by one.
        This generator approach is more memory-efficient for large content plans.

        Yields:
            Generator[BlogPost, None, None]: A generator of BlogPost objects.
        """
        if not self.service:
            logging.error("Google Sheets service not initialized. Cannot get content queue.")
            return
        try:
            sheet = self.service.spreadsheets()
            result = sheet.values().get(spreadsheetId=config.SPREADSHEET_ID, range=f"{config.PLAN_SHEET_NAME}!A:F").execute()
            values = result.get('values', [])
            
            if not values or len(values) < 2:
                logging.info("No data found in content plan or only headers present.")
                return

            headers = values[0]
            # Create a map of header names to their column index for robustness
            header_map = {header: i for i, header in enumerate(headers)}

            # Required headers
            required_headers = ['Topic', 'Primary Keyword', 'Target Audience', 'Category', 'Status']
            if not all(h in header_map for h in required_headers):
                logging.error(f"Missing one or more required headers in the sheet: {required_headers}")
                return

            for i, row in enumerate(values[1:], start=2):
                # Check if the row has enough columns and the status is 'Ready'
                status_col = header_map.get('Status')
                if status_col is not None and len(row) > status_col and row[status_col] == 'Ready':
                    
                    # Safely get the refinement loops value, defaulting to 1.
                    refinement_loops = 1
                    loops_col = header_map.get('Refinement Loops')
                    if loops_col is not None and len(row) > loops_col:
                        try:
                            refinement_loops = int(row[loops_col] or 1)
                        except (ValueError, IndexError):
                            logging.warning(f"Could not parse 'Refinement Loops' for row {i}. Defaulting to 1.")
                            refinement_loops = 1
                    
                    # Construct the BlogPost object and yield it
                    yield BlogPost(
                        topic=row[header_map['Topic']],
                        primary_keyword=row[header_map['Primary Keyword']],
                        target_audience=row[header_map['Target Audience']],
                        category=row[header_map['Category']],
                        refinement_loops=refinement_loops,
                        sheet_row_index=i
                    )
        except HttpError as e:
            logging.error(f"API error fetching content requests from Google Sheets: {e}")
        except Exception as e:
            logging.error(f"Unexpected error fetching content requests from Google Sheets: {e}")

    def update_sheet_status(self, row_index: int, status: str, url: str = ""):
        """
        Updates the status, timestamp, and optionally the URL for a specific row.
        This provides a clear audit trail directly in the content calendar.

        Args:
            row_index (int): The 1-based index of the row to update.
            status (str): The new status to set (e.g., 'In Progress', 'Published').
            url (str, optional): The URL of the published post. Defaults to "".
        """
        if not self.service:
            logging.error("Google Sheets service not initialized. Cannot update sheet status.")
            return
        try:
            # Recommendation: Make column indices configurable in config.py instead of hardcoding.
            # This would make the system more robust to changes in the sheet layout.
            # Example: STATUS_COLUMN = 'G', URL_COLUMN = 'H', LAST_UPDATED_COLUMN = 'I'
            
            now = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
            # Assuming Status is Col G, URL is Col H, Last Updated is Col I
            body = {
                'values': [[status, url, now]]
            }
            
            # The range starts from column G for the given row.
            range_to_update = f"{config.PLAN_SHEET_NAME}!G{row_index}"

            self.service.spreadsheets().values().update(
                spreadsheetId=config.SPREADSHEET_ID,
                range=range_to_update,
                valueInputOption='USER_ENTERED',
                body=body
            ).execute()
            logging.info(f"Updated row {row_index} status to '{status}'.")
        except HttpError as e:
            logging.error(f"API error updating row {row_index} in Google Sheets: {e}")
        except Exception as e:
            logging.error(f"Unexpected error updating row {row_index} in Google Sheets: {e}")

    def log_completed_post(self, post: BlogPost):
        """Appends a new row to the Generated Content Log sheet."""
        if not self.service:
            logging.error("Google Sheets service not initialized. Cannot log completed post.")
            return
        try:
            # This order must match the columns in your 'Generated Content Log' sheet
            timestamp = datetime.now().isoformat()
            row_data = [
                post.topic,
                post.generated_title or "",
                post.status or "Unknown",
                post.strapi_url or "",
                post.category or "",
                timestamp,
                post.rejection_reason or ""
            ]
            
            body = {'values': [row_data]}
            self.service.spreadsheets().values().append(
                spreadsheetId=config.SPREADSHEET_ID,
                range=f"{config.LOG_SHEET_NAME}!A1",
                valueInputOption='USER_ENTERED',
                insertDataOption='INSERT_ROWS',
                body=body
            ).execute()
            logging.info(f"Successfully logged '{post.generated_title}' to the Generated Content Log.")
        except Exception as e:
            logging.error(f"Error logging post to Google Sheets: {e}")

    def get_all_published_posts(self) -> dict[str, str]:
        """
        Fetches all posts marked as 'Published' to build a map of titles to URLs.
        This is used by the Creative Agent to suggest internal links.

        Returns:
            dict[str, str]: A dictionary mapping post titles to their published URLs.
        """
        if not self.service:
            logging.error("Google Sheets service not initialized. Cannot get published posts.")
            return {}
            
        published_posts = {}
        try:
            sheet = self.service.spreadsheets()
            # Fetches columns for Title (assuming in 'J') and URL (assuming in 'H')
            # Recommendation: Make these columns configurable.
            result = sheet.values().get(
                spreadsheetId=config.SPREADSHEET_ID,
                range=config.PUBLISHED_SHEET_NAME  # Corrected range
            ).execute()
            values = result.get('values', [])

            if not values or len(values) < 2:
                logging.info("No published posts found in the 'Published' sheet.")
                return {}

            headers = values[0]
            header_map = {header: i for i, header in enumerate(headers)}

            # Check for required headers
            if not all(h in header_map for h in ['Final Title', 'URL', 'Status']):
                logging.error("Missing 'Final Title', 'URL', or 'Status' header in 'Published' sheet.")
                return {}

            for row in values[1:]:
                status_col = header_map['Status']
                title_col = header_map['Final Title']
                url_col = header_map['URL']

                if len(row) > status_col and row[status_col] == 'Published':
                    if len(row) > title_col and len(row) > url_col:
                        title = row[title_col]
                        url = row[url_col]
                        if title and url:
                            published_posts[title] = url
            
            logging.info(f"Fetched {len(published_posts)} published posts for internal linking.")
            return published_posts
        except HttpError as e:
            logging.error(f"API error fetching published posts: {e}")
            return {}
        except Exception as e:
            logging.error(f"Unexpected error fetching published posts: {e}")
            return {}