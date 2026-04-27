#!/usr/bin/env python3
"""
Jira Test Case Generator
Automatically generates test cases for Jira tickets using Claude AI
"""

import os
import re
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

# Test case queue: "Ready for QA" + current user in "Assigned QA" (not main assignee).
JIRA_TCS_STATUS = (os.getenv('JIRA_TCS_STATUS') or 'Ready for QA').strip()
JIRA_ASSIGNED_QA_FIELD_ID = (os.getenv('JIRA_ASSIGNED_QA_FIELD_ID') or '').strip()
JIRA_ASSIGNED_QA_CF = (os.getenv('JIRA_ASSIGNED_QA_CF') or '').strip()
JIRA_ASSIGNED_QA_FIELD_NAME = (os.getenv('JIRA_ASSIGNED_QA_FIELD_NAME') or 'Assigned QA').strip()

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

def _assigned_qa_jql_fragment() -> str:
    """
    JQL condition: Assigned QA custom field = logged-in Jira user.

    Prefer JIRA_ASSIGNED_QA_FIELD_ID=customfield_12345 or JIRA_ASSIGNED_QA_CF=12345
    (reliable on every site). Otherwise uses JIRA_ASSIGNED_QA_FIELD_NAME in quotes.
    """
    if JIRA_ASSIGNED_QA_FIELD_ID:
        m = re.match(r"^customfield_(\d+)$", JIRA_ASSIGNED_QA_FIELD_ID, re.IGNORECASE)
        if m:
            return f"cf[{m.group(1)}] = currentUser()"
    if JIRA_ASSIGNED_QA_CF.isdigit():
        return f"cf[{JIRA_ASSIGNED_QA_CF}] = currentUser()"
    name = JIRA_ASSIGNED_QA_FIELD_NAME.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{name}" = currentUser()'


def get_jira_tickets():
    """
    Get Jira tickets in 'Ready for QA' with the current user in the Assigned QA field
    (not the main Jira assignee).
    """
    print('🔍 Searching for tickets in "Ready for QA" with you as Assigned QA...')

    # Jira Cloud removed POST /rest/api/3/search (410); use enhanced JQL search.
    base = (JIRA_URL or "").rstrip("/")
    url = f"{base}/rest/api/3/search/jql"

    auth = (JIRA_EMAIL, JIRA_API_TOKEN)

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    status_escaped = JIRA_TCS_STATUS.replace("\\", "\\\\").replace('"', '\\"')
    jql = (
        f'project = {JIRA_PROJECT} AND status = "{status_escaped}" AND '
        f"{_assigned_qa_jql_fragment()}"
    )

    field_ids = [
        'summary',
        'description',
        'status',
        'assignee',
        'comment',
    ]
    if JIRA_ASSIGNED_QA_FIELD_ID and re.match(
        r"^customfield_\d+$", JIRA_ASSIGNED_QA_FIELD_ID, re.IGNORECASE
    ):
        field_ids.append(JIRA_ASSIGNED_QA_FIELD_ID)

    payload = {
        'jql': jql,
        'fields': field_ids,
        'maxResults': 50
    }

    response = requests.post(
        url,
        auth=auth,
        headers={**headers, "x-atlassian-force-account-id": "true"},
        json=payload,
        timeout=60,
    )

    if response.status_code != 200:
        print(f"❌ Error fetching Jira tickets: {response.status_code}")
        print(f"ℹ️ JQL was: {jql}")
        print(response.text)
        return []
    
    data = response.json()
    raw_issues = data.get("issues")
    if isinstance(raw_issues, list):
        tickets = raw_issues
    elif isinstance(raw_issues, dict) and isinstance(raw_issues.get("nodes"), list):
        tickets = raw_issues["nodes"]
    else:
        tickets = []

    print(f"✅ Found {len(tickets)} ticket(s)")
    return tickets

def _extract_adf_text(node):
    """
    Extract visible text from Atlassian Document Format (ADF).

    Jira comment bodies are often stored as ADF JSON; we flatten it so we can
    reliably detect our own "TCS" marker text near a Google Doc link.
    """
    if node is None:
        return ""

    # Plain strings can exist in some contexts; treat them as text.
    if isinstance(node, str):
        return node

    if isinstance(node, list):
        return "\n".join(_extract_adf_text(item) for item in node if item is not None)

    if not isinstance(node, dict):
        return str(node)

    parts = []
    node_type = node.get("type")
    if node_type == "text":
        parts.append(node.get("text", ""))

    for child_key in ("content",):
        child = node.get(child_key)
        if child:
            parts.append(_extract_adf_text(child))

    # Paragraph-ish blocks should separate with newlines to preserve ordering cues.
    if node_type in {"paragraph", "heading", "blockquote", "listItem"}:
        return "".join(parts).strip()

    return "".join(parts)


def ticket_has_google_doc_link(ticket):
    """Check if ticket already has an auto-generated TCS Google Doc link in comments."""
    comments = ticket["fields"].get("comment", {}).get("comments", [])

    # Only treat as "already done" if the Google Doc link is clearly marked as
    # auto-generated test cases.
    marker = "Test cases generated automatically:"

    for comment in comments:
        body = comment.get("body")
        text = _extract_adf_text(body)

        # Accept either the exact marker or the legacy marker that included a check emoji.
        # The key requirement is that the marker text appears before the doc link.
        legacy_marker = "✅ " + marker
        if (marker in text or legacy_marker in text) and "docs.google.com/document/d/" in text:
            marker_pos = text.find(marker) if marker in text else text.find(legacy_marker)
            link_pos = text.find("docs.google.com/document/d/")
            if marker_pos != -1 and link_pos != -1 and marker_pos < link_pos:
                return True

    return False

def get_ticket_content(ticket):
    """Extract relevant content from Jira ticket"""
    fields = ticket['fields']

    assigned_qa = 'N/A'
    if JIRA_ASSIGNED_QA_FIELD_ID and JIRA_ASSIGNED_QA_FIELD_ID in fields:
        raw = fields.get(JIRA_ASSIGNED_QA_FIELD_ID)
        if isinstance(raw, dict):
            assigned_qa = raw.get('displayName') or raw.get('emailAddress') or str(raw)
        elif raw is not None:
            assigned_qa = str(raw)

    content = f"""
# {ticket['key']}: {fields.get('summary', 'No title')}

## Description
{fields.get('description', 'No description')}

## Status
{fields.get('status', {}).get('name', 'Unknown')}

## Assignee
{fields.get('assignee', {}).get('displayName', 'Unassigned')}

## Assigned QA
{assigned_qa}
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
                            "text": "Test cases generated automatically: "
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
