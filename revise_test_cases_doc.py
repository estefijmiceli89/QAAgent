#!/usr/bin/env python3
"""
Apply human feedback to an existing Google Doc of test cases.

Pass the document URL and natural-language instructions (what to improve, add,
remove, or reorder). Claude revises the full document; the script replaces the
Doc body with the revised text.

Requires the same .env as generate_test_cases.py (CLAUDE_API_KEY) and Google
OAuth token with Docs scope (token.json from running the generator once).
"""

from __future__ import annotations

import argparse
import os
import re
import sys

from anthropic import Anthropic
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from dotenv import load_dotenv

load_dotenv()

SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/documents",
]

CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY")


def get_google_credentials():
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists("credentials.json"):
                print("❌ credentials.json not found")
                sys.exit(1)
            flow = InstalledAppFlow.from_client_secrets_file(
                "credentials.json", SCOPES
            )
            creds = flow.run_local_server(port=0)
        with open("token.json", "w", encoding="utf-8") as token:
            token.write(creds.to_json())
    return creds


def doc_id_from_url(url: str) -> str:
    """Extract Google Doc id from edit or view URL."""
    url = url.strip()
    m = re.search(r"/document/d/([a-zA-Z0-9_-]+)", url)
    if m:
        return m.group(1)
    print("❌ Could not parse document id from URL (expected .../document/d/DOC_ID/...)")
    sys.exit(1)


def _paragraph_text(paragraph: dict) -> str:
    chunks = []
    for el in paragraph.get("elements", []):
        tr = el.get("textRun")
        if tr and "content" in tr:
            chunks.append(tr["content"])
    return "".join(chunks)


def _structural_element_text(element: dict) -> str:
    if "paragraph" in element:
        return _paragraph_text(element["paragraph"])
    if "table" in element:
        lines = []
        for row in element["table"].get("tableRows", []):
            cells = []
            for cell in row.get("tableCells", []):
                parts = []
                for inner in cell.get("content", []):
                    parts.append(_structural_element_text(inner))
                cells.append("".join(parts).replace("\n", " ").strip())
            lines.append("\t".join(cells))
        return "\n".join(lines) + "\n"
    if "sectionBreak" in element or "tableOfContents" in element:
        return ""
    return ""


def read_doc_plain_text(docs_service, document_id: str) -> str:
    doc = docs_service.documents().get(documentId=document_id).execute()
    parts = []
    for el in doc.get("body", {}).get("content", []):
        parts.append(_structural_element_text(el))
    return "".join(parts).rstrip("\n")


def replace_body_text(docs_service, document_id: str, new_text: str) -> None:
    doc = docs_service.documents().get(documentId=document_id).execute()
    content = doc.get("body", {}).get("content", [])
    if not content:
        raise RuntimeError("Document has no body content")
    end_index = content[-1].get("endIndex")
    if end_index is None or end_index < 2:
        raise RuntimeError("Could not determine document end index")
    # Preserve implicit trailing segment: delete [1, end_index - 1)
    requests_body = [
        {
            "deleteContentRange": {
                "range": {"startIndex": 1, "endIndex": end_index - 1}
            }
        },
        {"insertText": {"location": {"index": 1}, "text": new_text}},
    ]
    docs_service.documents().batchUpdate(
        documentId=document_id, body={"requests": requests_body}
    ).execute()


REVISION_SYSTEM = """You are an expert QA engineer. You revise test-case documents written in the project's usual format (Gherkin-style blocks, TC-## headings, sections like Preconditions, Scenarios, Risk Areas, etc.).

Rules:
- Output ONLY the full revised document text. No preamble, no markdown fences, no "Here is the revised version".
- Preserve the same general structure and tone as the input unless the user asks to change format.
- Apply every user instruction: add missing TCs, remove or merge redundant ones, fix wording, adjust priorities, etc.
- If the user asks to delete something, remove it completely. If they ask to add scenarios, integrate them in sensible order with correct numbering (TC-01, TC-02, …) renumbered if needed.
- Keep language consistent with the document (Spanish vs English): match the dominant language of the input unless the user specifies otherwise.
"""


def revise_with_claude(current_text: str, feedback: str) -> str:
    if not CLAUDE_API_KEY:
        print("❌ CLAUDE_API_KEY not set in environment")
        sys.exit(1)
    client = Anthropic(api_key=CLAUDE_API_KEY)
    user_msg = f"""## CURRENT DOCUMENT

{current_text}

---

## REVISION INSTRUCTIONS (from the reviewer)

{feedback}

Return the complete updated document only."""

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=8000,
        system=REVISION_SYSTEM,
        messages=[{"role": "user", "content": user_msg}],
    )
    return message.content[0].text.strip()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Revise a Google Doc of test cases using Claude + your feedback."
    )
    parser.add_argument(
        "--url",
        required=True,
        help="Google Doc URL (e.g. https://docs.google.com/document/d/DOC_ID/edit)",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--feedback",
        help="What to change (improve / add / remove / fix). Plain text.",
    )
    group.add_argument(
        "--feedback-file",
        metavar="PATH",
        help="Read feedback instructions from a UTF-8 file.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print revised text to stdout only; do not update the Doc.",
    )
    args = parser.parse_args()

    feedback = args.feedback
    if args.feedback_file:
        with open(args.feedback_file, encoding="utf-8") as f:
            feedback = f.read()
    if not (feedback or "").strip():
        print("❌ Feedback is empty")
        sys.exit(1)

    doc_id = doc_id_from_url(args.url)
    creds = get_google_credentials()
    docs_service = build("docs", "v1", credentials=creds)

    print("📖 Reading document...")
    current = read_doc_plain_text(docs_service, doc_id)
    if not current.strip():
        print("❌ Document appears empty")
        sys.exit(1)

    print("🤖 Asking Claude to apply your feedback...")
    revised = revise_with_claude(current, feedback.strip())

    if args.dry_run:
        print(revised)
        return

    print("✍️  Updating Google Doc...")
    replace_body_text(docs_service, doc_id, revised)
    print(f"✅ Done: https://docs.google.com/document/d/{doc_id}/edit")


if __name__ == "__main__":
    main()
