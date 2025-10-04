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

    def get_new_content_requests(self) -> list[BlogPost]:
        """Fetches rows from the content plan that are marked 'Ready'."""
        try:
            sheet = self.service.spreadsheets()
            result = sheet.values().get(spreadsheetId=config.SPREADSHEET_ID, range=f"{config.PLAN_SHEET_NAME}!A:F").execute()
            values = result.get('values', [])
            
            requests = []
            if not values or len(values) < 2:
                logging.info("No data found in content plan.")
                return []

            headers = values[0]
            for i, row in enumerate(values[1:], start=2):
                if len(row) > headers.index('Status') and row[headers.index('Status')] == 'Ready':
                    # Safely get the refinement loops value, defaulting to 1 if the column is missing.
                    refinement_loops = 1
                    if 'Refinement Loops' in headers:
                        try:
                            refinement_loops = int(row[headers.index('Refinement Loops')] or 1)
                        except (ValueError, IndexError):
                            logging.warning(f"Could not parse 'Refinement Loops' for row {i}. Defaulting to 1.")
                            refinement_loops = 1
                    
                    requests.append(BlogPost(
                        topic=row[headers.index('Topic')],
                        primary_keyword=row[headers.index('Primary Keyword')],
                        target_audience=row[headers.index('Target Audience')],
                        category=row[headers.index('Category')],
                        refinement_loops=refinement_loops,
                        sheet_row_index=i
                    ))
            return requests
        except Exception as e:
            logging.error(f"Error fetching content requests from Google Sheets: {e}")
            return []

    def update_status_by_row(self, row_index: int, status: str, url: str = ""):
        """Updates the status and URL of a specific row in the content plan."""
        try:
            # Assuming Status is in Col G and URL is in Col H
            body = {'values': [[status, url]]}
            self.service.spreadsheets().values().update(
                spreadsheetId=config.SPREADSHEET_ID,
                range=f"{config.PLAN_SHEET_NAME}!G{row_index}:H{row_index}",
                valueInputOption='USER_ENTERED',
                body=body
            ).execute()
        except Exception as e:
            logging.error(f"Error updating row {row_index} in Google Sheets: {e}")

    def log_completed_post(self, post: BlogPost):
        """Appends a new row to the Generated Content Log sheet."""
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

    def get_published_posts_map(self) -> dict[str, str]:
        """Creates a map of {Title: URL} for all published posts for internal linking."""
        try:
            sheet = self.service.spreadsheets()
            # Assuming Title is in Col F and URL is in Col I of the Log Sheet
            result = sheet.values().get(spreadsheetId=config.SPREADSHEET_ID, range=f"{config.LOG_SHEET_NAME}!F:I").execute()
            values = result.get('values', [])
            
            post_map = {}
            if not values or len(values) < 2:
                return {}
            
            for row in values[1:]:
                if len(row) >= 4 and row[1] == 'Published to Strapi': # Status in Col G
                    title = row[0] # Title in Col F
                    url = row[3]   # URL in Col I
                    post_map[title] = url
            return post_map
        except Exception as e:
            logging.error(f"Error fetching published posts map from Google Sheets: {e}")
            return {}