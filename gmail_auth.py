
# gmail_auth.py
# Author: Fatemeh Delavari (Atena)
# Version: 2 (2026-02-20)

import os
import os.path
import pickle
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

def authenticate_gmail():
    SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']
    creds = None

    # Load saved credentials if they exist
    if os.path.exists('token.pickle'):
        with open('token.pickle', 'rb') as token:
            creds = pickle.load(token)

    # Otherwise, log in and save new credentials
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:

            # creds_path = os.path.join(os.path.dirname(__file__), 'credentials.json')
            creds_path = os.path.join(os.path.dirname(__file__), 'client_secret.json')
            flow = InstalledAppFlow.from_client_secrets_file(creds_path, SCOPES)

            creds = flow.run_local_server(port=0)

        # Save credentials
        with open('token.pickle', 'wb') as token:
            pickle.dump(creds, token)

    service = build('gmail', 'v1', credentials=creds)
    return service
