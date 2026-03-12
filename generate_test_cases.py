#!/usr/bin/env python3
"""
Jira Test Case Generator
Automatically generates test cases for Jira tickets using Claude AI
"""

import os
import sys
import requests
from anthropic import Anthropic
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
import json
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
JIRA_URL = os.getenv('JIRA_URL')  # e.g., https://fpsinc.atlassian.net
JIRA_EMAIL = os.getenv('JIRA_EMAIL')
JIRA_API_TOKEN = os.getenv('JIRA_API_TOKEN')
JIRA_PROJECT = os.getenv('JIRA_PROJECT')  # e.g., CCAI
CLAUDE_API_KEY = os.getenv('CLAUDE_API_KEY')
GOOGLE_DRIVE_FOLDER_ID = os.getenv('GOOGLE_DRIVE_FOLDER_ID')

# Google Drive API scope
SCOPES = ['https://www.googleapis.com/auth/drive.file', 
          'https://www.googleapis.com/auth/documents']

def load_prompt_maestro():
    """Load the prompt maestro template"""
    try:
        with open('prompt_maestro.txt', 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        print("❌ Error: prompt_maestro.txt not found")
        sys.exit(1)

def get_google_credentials():
    """Get or refresh Google credentials"""
    creds = None
    
    # Check if we have saved credentials
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    
    # If no valid credentials, let user log in
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists('credentials.json'):
                print("❌ Error: credentials.json not found")
                print("Please download it from Google Cloud Console")
                sys.exit(1)
            
            flow = InstalledAppFlow.from_client_secrets_file(
                'credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        
        # Save credentials for next run
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    
    return creds

def get_jira_tickets():
    """Get assigned Jira tickets in 'Assigned' status"""
    print("🔍 Searching for assigned tickets...")

    url = f"{JIRA_URL}/rest/api/3/search/jql"

    auth = (JIRA_EMAIL, JIRA_API_TOKEN)

    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json'
    }

    # JQL query to find tickets assigned to current user in "Assigned" status
    jql = f'project = {JIRA_PROJECT} AND assignee = currentUser() AND status = "Assigned"'

    payload = {
        'jql': jql,
        'fields': ['summary', 'description', 'status', 'assignee', 'comment'],
        'maxResults': 50
    }

    response = requests.post(url, auth=auth, headers=headers, json=payload)
    
    if response.status_code != 200:
        print(f"❌ Error fetching Jira tickets: {response.status_code}")
        print(response.text)
        return []
    
    data = response.json()
    tickets = data.get('issues', [])
    
    print(f"✅ Found {len(tickets)} ticket(s)")
    return tickets

def ticket_has_google_doc_link(ticket):
    """Check if ticket already has a Google Doc link in comments"""
    comments = ticket['fields'].get('comment', {}).get('comments', [])
    
    for comment in comments:
        body = comment.get('body', '')
        # Check if comment contains a Google Docs link
        if 'docs.google.com' in str(body):
            return True
    
    return False

def get_ticket_content(ticket):
    """Extract relevant content from Jira ticket"""
    fields = ticket['fields']
    
    content = f"""
# {ticket['key']}: {fields.get('summary', 'No title')}

## Description
{fields.get('description', 'No description')}

## Status
{fields.get('status', {}).get('name', 'Unknown')}

## Assignee
{fields.get('assignee', {}).get('displayName', 'Unassigned')}
"""
    
    return content

def generate_test_cases_with_claude(ticket_key, ticket_content, prompt_maestro):
    """Use Claude API to generate test cases"""
    print(f"🤖 Generating test cases for {ticket_key}...")
    
    client = Anthropic(api_key=CLAUDE_API_KEY)
    
    full_prompt = f"""{prompt_maestro}

## JIRA TICKET TO ANALYZE:

{ticket_content}

Please generate comprehensive test cases following the template and guidelines provided above.
"""
    
    try:
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4000,
            messages=[
                {"role": "user", "content": full_prompt}
            ]
        )
        
        test_cases = message.content[0].text
        print(f"✅ Test cases generated for {ticket_key}")
        return test_cases
        
    except Exception as e:
        print(f"❌ Error generating test cases: {e}")
        return None

def create_google_doc(ticket_key, content, folder_id):
    """Create a Google Doc with the test cases"""
    print(f"📄 Creating Google Doc for {ticket_key}...")
    
    try:
        creds = get_google_credentials()
        
        # Create document
        docs_service = build('docs', 'v1', credentials=creds)
        drive_service = build('drive', 'v3', credentials=creds)
        
        # Create the document
        doc = docs_service.documents().create(
            body={'title': ticket_key}
        ).execute()
        
        doc_id = doc.get('documentId')
        
        # Add content to document
        requests_body = [
            {
                'insertText': {
                    'location': {'index': 1},
                    'text': content
                }
            }
        ]
        
        docs_service.documents().batchUpdate(
            documentId=doc_id,
            body={'requests': requests_body}
        ).execute()
        
        # Move to specific folder
        if folder_id:
            file = drive_service.files().get(
                fileId=doc_id,
                fields='parents'
            ).execute()
            
            previous_parents = ",".join(file.get('parents'))
            
            drive_service.files().update(
                fileId=doc_id,
                addParents=folder_id,
                removeParents=previous_parents,
                fields='id, parents'
            ).execute()
        
        # Make it accessible to anyone with the link
        drive_service.permissions().create(
            fileId=doc_id,
            body={
                'type': 'anyone',
                'role': 'writer'
            }
        ).execute()
        
        doc_url = f"https://docs.google.com/document/d/{doc_id}/edit"
        print(f"✅ Google Doc created: {doc_url}")
        
        return doc_url
        
    except Exception as e:
        print(f"❌ Error creating Google Doc: {e}")
        return None

def add_comment_to_jira(ticket_key, doc_url):
    """Add a comment with the Google Doc link to the Jira ticket"""
    print(f"💬 Adding comment to {ticket_key}...")
    
    url = f"{JIRA_URL}/rest/api/3/issue/{ticket_key}/comment"
    
    auth = (JIRA_EMAIL, JIRA_API_TOKEN)
    
    headers = {
        'Content-Type': 'application/json'
    }
    
    comment_body = {
        "body": {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "type": "text",
                            "text": "✅ Test cases generated automatically: "
                        },
                        {
                            "type": "text",
                            "text": doc_url,
                            "marks": [
                                {
                                    "type": "link",
                                    "attrs": {
                                        "href": doc_url
                                    }
                                }
                            ]
                        }
                    ]
                }
            ]
        }
    }
    
    response = requests.post(url, auth=auth, headers=headers, json=comment_body)
    
    if response.status_code == 201:
        print(f"✅ Comment added to {ticket_key}")
        return True
    else:
        print(f"❌ Error adding comment: {response.status_code}")
        print(response.text)
        return False

def main():
    """Main execution function"""
    print("=" * 60)
    print("🚀 JIRA TEST CASE GENERATOR")
    print("=" * 60)
    print()
    
    # Validate environment variables
    required_vars = [
        'JIRA_URL', 'JIRA_EMAIL', 'JIRA_API_TOKEN', 
        'JIRA_PROJECT', 'CLAUDE_API_KEY', 'GOOGLE_DRIVE_FOLDER_ID'
    ]
    
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        print("❌ Missing required environment variables:")
        for var in missing_vars:
            print(f"   - {var}")
        print("\nPlease configure your .env file")
        sys.exit(1)
    
    # Load prompt maestro
    prompt_maestro = load_prompt_maestro()
    
    # Get tickets
    tickets = get_jira_tickets()
    
    if not tickets:
        print("✅ No tickets to process")
        return
    
    # Process each ticket
    processed = 0
    skipped = 0
    
    for ticket in tickets:
        ticket_key = ticket['key']
        print()
        print(f"📋 Processing {ticket_key}...")
        
        # Check if already has Google Doc link
        if ticket_has_google_doc_link(ticket):
            print(f"⏭️  Skipping {ticket_key} - already has Google Doc link")
            skipped += 1
            continue
        
        # Get ticket content
        ticket_content = get_ticket_content(ticket)
        
        # Generate test cases
        test_cases = generate_test_cases_with_claude(
            ticket_key, 
            ticket_content, 
            prompt_maestro
        )
        
        if not test_cases:
            print(f"⚠️  Failed to generate test cases for {ticket_key}")
            continue
        
        # Create Google Doc
        doc_url = create_google_doc(
            ticket_key, 
            test_cases, 
            GOOGLE_DRIVE_FOLDER_ID
        )
        
        if not doc_url:
            print(f"⚠️  Failed to create Google Doc for {ticket_key}")
            continue
        
        # Add comment to Jira
        if add_comment_to_jira(ticket_key, doc_url):
            processed += 1
        
        print(f"✅ {ticket_key} completed!")
    
    print()
    print("=" * 60)
    print(f"✅ Processed: {processed}")
    print(f"⏭️  Skipped: {skipped}")
    print(f"📊 Total: {len(tickets)}")
    print("=" * 60)

if __name__ == "__main__":
    main()
