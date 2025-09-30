import os
import logging
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from config import config
from utils.data_models import BlogPost

class GoogleSheetsClient:
    """Client to interact with the Google Sheets content calendar."""
    SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

    def __init__(self):
        self.creds = self._get_credentials()
        self.service = build('sheets', 'v4', credentials=self.creds)

    def _get_credentials(self):
        creds = None
        token_path = os.path.join(config.BASE_DIR, 'token.json')
        if os.path.exists(token_path):
            creds = Credentials.from_authorized_user_file(token_path, self.SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(config.CREDENTIALS_PATH, self.SCOPES)
                creds = flow.run_local_server(port=0)
            with open(token_path, 'w') as token:
                token.write(creds.to_json())
        return creds

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
                    requests.append(BlogPost(
                        topic=row[headers.index('Topic')],
                        primary_keyword=row[headers.index('Primary Keyword')],
                        target_audience=row[headers.index('Target Audience')],
                        category=row[headers.index('Category')],
                        refinement_loops=int(row[headers.index('Refinement Loops')] or 1),
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