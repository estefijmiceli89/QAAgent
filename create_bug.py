#!/usr/bin/env python3
"""
Create Jira BUG issues in the CCAI project from a structured bug template.

This script:
- Prompts for bug fields following a fixed template.
- Attaches a local screenshot/video as evidence.
- Creates a BUG issue in Jira Cloud (CCAI project) assigned to a configured assignee.
- Adds a comment stating the bug was generated automatically and mentions the reporter.
"""

import argparse
import os
import sys
from pathlib import Path
from typing import Optional, Dict, Any, List
import time

import requests
from anthropic import Anthropic
from dotenv import load_dotenv


def load_config() -> Dict[str, str]:
    """Load Jira configuration from environment variables."""
    load_dotenv()

    config = {
        "JIRA_BASE_URL": os.getenv("JIRA_BASE_URL") or os.getenv("JIRA_URL"),
        "JIRA_EMAIL": os.getenv("JIRA_EMAIL"),
        "JIRA_API_TOKEN": os.getenv("JIRA_API_TOKEN"),
        # Force CCAI as project key but still allow override if ever needed.
        "JIRA_PROJECT_KEY": os.getenv("JIRA_PROJECT_KEY") or os.getenv("JIRA_PROJECT") or "CCAI",
        "ASSIGNEE_ACCOUNT_ID": os.getenv("ASSIGNEE_ACCOUNT_ID"),
        "ASSIGNEE_NAME": os.getenv("ASSIGNEE_NAME", "Paolo Junia"),
        "REPORTER_ACCOUNT_ID": os.getenv("REPORTER_ACCOUNT_ID"),
        "REPORTER_DISPLAY_NAME": os.getenv("REPORTER_DISPLAY_NAME", "estefania miceli"),
        "CCAI_PRODUCT_FIELD_ID": os.getenv("CCAI_PRODUCT_FIELD_ID"),
        "CLAUDE_API_KEY": os.getenv("CLAUDE_API_KEY"),
    }

    missing = [
        key
        for key, value in config.items()
        if key in {"JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN"} and not value
    ]
    if missing:
        print(f"❌ Missing required environment variables: {', '.join(missing)}")
        print("   Ensure you have a .env file with these values configured.")
        sys.exit(1)

    return config


def _mime_type_for_path(path: Path) -> Optional[str]:
    suffix = path.suffix.lower()
    if suffix in {".png"}:
        return "image/png"
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix in {".webp"}:
        return "image/webp"
    if suffix in {".gif"}:
        return "image/gif"
    return None


def resolve_assignee_account_id(config: Dict[str, str]) -> Optional[str]:
    """
    Resolve Paolo's accountId via Jira's assignable users search.

    This avoids stale/wrong ASSIGNEE_ACCOUNT_ID values and also ensures
    the user is assignable to the project.
    """
    base = (config.get("JIRA_BASE_URL") or "").rstrip("/")
    project_key = config.get("JIRA_PROJECT_KEY") or "CCAI"
    email = config.get("JIRA_EMAIL")
    token = config.get("JIRA_API_TOKEN")
    if not (base and email and token):
        return config.get("ASSIGNEE_ACCOUNT_ID")

    assignee_name = (config.get("ASSIGNEE_NAME") or "").strip()
    fallback = config.get("ASSIGNEE_ACCOUNT_ID")
    if not assignee_name and not fallback:
        return None

    # If caller already provided an accountId, still verify it via the assignable search.
    auth = (email, token)
    headers = {
        "Accept": "application/json",
        "x-atlassian-force-account-id": "true",
    }

    if not assignee_name:
        return fallback

    # Jira assignable search usually returns users assignable to the project.
    import urllib.parse

    query = urllib.parse.quote_plus(assignee_name)
    url = (
        f"{base}/rest/api/3/user/assignable/search"
        f"?project={project_key}&query={query}"
    )
    resp = requests.get(url, auth=auth, headers=headers, timeout=30)
    if resp.status_code != 200:
        print(
            f"⚠️ Could not resolve assignee via assignable search: {resp.status_code}"
        )
        return fallback

    users = resp.json() or []
    # Prefer exact-ish displayName match.
    name_lower = assignee_name.lower()
    for u in users:
        display = (u.get("displayName") or "").lower()
        if display == name_lower or name_lower in display:
            return u.get("accountId") or u.get("accountID") or fallback

    # Fallback: first returned user.
    if users:
        resolved = users[0].get("accountId") or users[0].get("accountID") or fallback
        print(f"ℹ️ Resolved assignee accountId candidate: {resolved}")
        return resolved

    return fallback


def generate_bug_with_claude(
    config: Dict[str, str],
    short_summary: str,
    evidence_path: Optional[Path],
    url_value: str,
) -> Dict[str, str]:
    """
    Use Claude to generate a complete bug report from a short summary.

    The model returns a JSON object with all the template fields, which we then
    map into the structure expected by Jira.
    """
    api_key = config.get("CLAUDE_API_KEY")
    if not api_key:
        print("❌ CLAUDE_API_KEY is not set in your environment. Cannot generate bug with AI.")
        sys.exit(1)

    client = Anthropic(api_key=api_key)

    system_prompt = (
        "You are a senior QA engineer. Given a short bug summary, you must create a "
        "complete, high-quality bug report using the following template:\n\n"
        "Summary\n"
        "URL:\n"
        "Pre-Conditions:\n"
        "Description:\n"
        "Steps to Reproduce:\n"
        "Current Behaviour:\n"
        "Expected Behaviour:\n"
        "Console Logs:\n\n"
        "Respond ONLY with a valid JSON object, no extra text, with exactly these keys:\n"
        "  summary, url, pre_conditions, description,\n"
        "  steps_to_reproduce, current_behaviour, expected_behaviour, console_logs.\n"
        "Values should be strings. Do not include markdown, just plain text."
    )

    user_prompt = (
        f"Short summary of the bug:\n{short_summary}\n\n"
        f"Use this URL for the ticket:\n{url_value}\n"
    )

    try:
        content_blocks: List[Dict[str, Any]] = []
        mime_type = None
        if evidence_path and evidence_path.exists():
            mime_type = _mime_type_for_path(evidence_path)

        # Send image evidence to Claude (skip non-images like video).
        if mime_type:
            import base64

            image_bytes = evidence_path.read_bytes()
            image_b64 = base64.b64encode(image_bytes).decode("utf-8")
            content_blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": mime_type,
                        "data": image_b64,
                    },
                }
            )

        content_blocks.append({"type": "text", "text": user_prompt})

        message = client.messages.create(
            # Use the same model you already use for test cases
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            system=system_prompt,
            messages=[
                {
                    "role": "user",
                    "content": content_blocks,
                }
            ],
        )
        raw_text = message.content[0].text
    except Exception as e:
        print(f"❌ Error calling Claude API: {e}")
        sys.exit(1)

    import json

    # Claude sometimes wraps JSON in ```json ``` fences; strip them if present.
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.lstrip("`")
        # Remove optional language tag like json\n
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
        # Strip trailing fences
        cleaned = cleaned.strip("`").strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        print("❌ Claude response was not valid JSON after cleaning:")
        print(cleaned)
        sys.exit(1)

    required_keys = [
        "summary",
        "url",
        "pre_conditions",
        "description",
        "steps_to_reproduce",
        "current_behaviour",
        "expected_behaviour",
        "console_logs",
    ]
    for key in required_keys:
        if key not in data:
            print(f"❌ Claude JSON is missing required key: {key}")
            sys.exit(1)

    bug = {
        "summary": data["summary"],
        "url": data["url"],
        "pre_conditions": data["pre_conditions"],
        "description": data["description"],
        "steps_to_reproduce": data["steps_to_reproduce"],
        "current_behaviour": data["current_behaviour"],
        "expected_behaviour": data["expected_behaviour"],
        "console_logs": data["console_logs"],
    }

    print("🤖 Bug content generated by AI.")
    return bug


def build_description(bug: Dict[str, str]) -> Dict[str, Any]:
    """
    Build Jira Cloud description in Atlassian Document Format (ADF) using the template.

    We use simple paragraphs headed by field names so the ticket is easy to read.
    """

    def paragraph(text: str) -> Dict[str, Any]:
        return {
            "type": "paragraph",
            "content": [{"type": "text", "text": text}],
        }

    sections = []

    def add_section(label: str, value: Optional[str]) -> None:
        sections.append(paragraph(f"{label}:"))
        if value:
            sections.append(paragraph(value))
        else:
            sections.append(paragraph("-"))

    add_section("URL", bug.get("url"))
    add_section("Pre-Conditions", bug.get("pre_conditions"))
    add_section("Description", bug.get("description"))
    add_section("Steps to Reproduce", bug.get("steps_to_reproduce"))
    add_section("Current Behaviour", bug.get("current_behaviour"))
    add_section("Expected Behaviour", bug.get("expected_behaviour"))
    add_section("Console Logs", bug.get("console_logs"))

    return {
        "type": "doc",
        "version": 1,
        "content": sections,
    }


def create_issue(
    config: Dict[str, str],
    bug: Dict[str, str],
    parent_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a Jira BUG issue and return the response JSON."""
    url = f"{config['JIRA_BASE_URL'].rstrip('/')}/rest/api/3/issue"

    description_adf = build_description(bug)

    fields: Dict[str, Any] = {
        "project": {"key": config["JIRA_PROJECT_KEY"]},
        "summary": bug["summary"],
        "issuetype": {"name": "Bug"},
        "description": description_adf,
    }

    if parent_key:
        fields["parent"] = {"key": parent_key}

    # Assignee is set separately after CCAI Product is confirmed (see set_ccai_product).

    payload = {"fields": fields}

    auth = (config["JIRA_EMAIL"], config["JIRA_API_TOKEN"])
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    response = requests.post(
        url,
        auth=auth,
        headers={**headers, "x-atlassian-force-account-id": "true"},
        json=payload,
        timeout=30,
    )

    if response.status_code not in (200, 201):
        print(f"❌ Failed to create issue: {response.status_code}")
        print(response.text)
        sys.exit(1)

    data = response.json()
    issue_key = data.get("key")
    print(f"✅ Created issue {issue_key}")
    return data


CCAI_PRODUCT_FIELD_ID = "customfield_11050"
CCAI_PRODUCT_OPTION_ID = "10538"  # QA-I


def set_ccai_product(config: Dict[str, str], issue_key: str) -> bool:
    """Set CCAI Product = QA-I on the issue via a separate PUT call."""
    print(f"🔄 Setting CCAI Product = QA-I on {issue_key}...")

    url = f"{config['JIRA_BASE_URL'].rstrip('/')}/rest/api/3/issue/{issue_key}"
    auth = (config["JIRA_EMAIL"], config["JIRA_API_TOKEN"])
    headers = {"Accept": "application/json", "Content-Type": "application/json"}

    payload = {
        "fields": {
            CCAI_PRODUCT_FIELD_ID: [{"id": CCAI_PRODUCT_OPTION_ID}]
        }
    }

    resp = requests.put(url, auth=auth, headers=headers, json=payload, timeout=30)
    if resp.status_code == 204:
        print(f"✅ CCAI Product set to QA-I on {issue_key}")
        return True
    else:
        print(f"❌ Failed to set CCAI Product on {issue_key}: {resp.status_code} {resp.text}")
        return False


def ensure_assigned(config: Dict[str, str], issue_key: str) -> None:
    """Ensure the issue is assigned to Paolo (accountId may not work on create)."""
    assignee_account_id = resolve_assignee_account_id(config)
    assignee_name = config.get("ASSIGNEE_NAME", "Paolo Junia")
    if not assignee_account_id and not assignee_name:
        return

    def _get_assignee() -> Optional[str]:
        url = (
            f"{config['JIRA_BASE_URL'].rstrip('/')}/rest/api/3/issue/"
            f"{issue_key}?fields=assignee"
        )
        auth = (config["JIRA_EMAIL"], config["JIRA_API_TOKEN"])
        resp = requests.get(
            url,
            auth=auth,
            headers={
                "Accept": "application/json",
                "x-atlassian-force-account-id": "true",
            },
            timeout=30,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        assignee = (data.get("fields") or {}).get("assignee")
        if not assignee:
            return None
        return assignee.get("accountId") or assignee.get("displayName") or None

    def _wait_for_assignee(max_wait_s: int = 20) -> Optional[str]:
        deadline = time.time() + max_wait_s
        last = None
        while time.time() < deadline:
            last = _get_assignee()
            if last:
                return last
            time.sleep(2)
        return last

    # Quick pre-check.
    current = _get_assignee()
    if current:
        print(f"ℹ️ Issue already has assignee: {current}")
        return

    # Verify the target accountId exists in Jira (helps diagnose mismatches).
    if assignee_account_id:
        verify_url = (
            f"{config['JIRA_BASE_URL'].rstrip('/')}/rest/api/3/user"
            f"?accountId={assignee_account_id}"
        )
        resp_v = requests.get(
            verify_url,
            auth=(config["JIRA_EMAIL"], config["JIRA_API_TOKEN"]),
            headers={"Accept": "application/json", "x-atlassian-force-account-id": "true"},
            timeout=30,
        )
        if resp_v.status_code == 200:
            vdata = resp_v.json() or {}
            print(
                f"ℹ️ Assignee accountId verified. displayName={vdata.get('displayName')}"
            )
        else:
            print(
                f"⚠️ Assignee accountId verification failed: {resp_v.status_code} {resp_v.text}"
            )

    auth = (config["JIRA_EMAIL"], config["JIRA_API_TOKEN"])
    assign_url = (
        f"{config['JIRA_BASE_URL'].rstrip('/')}/rest/api/3/issue/{issue_key}/assignee"
    )
    # Required on some Jira sites when using accountId.
    # See: x-atlassian-force-account-id
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "x-atlassian-force-account-id": "true",
    }

    # 1) Try accountId assignment via the dedicated endpoint.
    if assignee_account_id:
        print(f"ℹ️ Attempting assignee with accountId={assignee_account_id}")
        resp = requests.put(
            assign_url,
            auth=auth,
            headers=headers,
            json={"accountId": assignee_account_id},
            timeout=30,
        )
        if resp.status_code not in (200, 204):
            print(
                f"⚠️ Could not assign via accountId: {resp.status_code} - {resp.text}"
            )
        else:
            # Even with 204, some Jira sites do not persist the assignee.
            print(f"ℹ️ Assignee PUT(/assignee) returned {resp.status_code}.")

    # 2) Re-check with polling (Jira may be eventually consistent)
    current = _wait_for_assignee(max_wait_s=20)
    if current:
        return

    # 3) Fallback: update the issue fields with assignee (some Jira sites behave better here).
    update_url = (
        f"{config['JIRA_BASE_URL'].rstrip('/')}/rest/api/3/issue/{issue_key}"
    )
    if assignee_account_id:
        resp2 = requests.put(
            update_url,
            auth=auth,
            headers=headers,
            json={"fields": {"assignee": {"accountId": assignee_account_id}}},
            timeout=30,
        )
        if resp2.status_code not in (200, 201):
            print(
                f"⚠️ Could not assign via issue update: {resp2.status_code} - {resp2.text}"
            )
        else:
            print(f"ℹ️ Assignee PUT(/issue) returned {resp2.status_code}.")

    # Final re-check with polling
    current = _wait_for_assignee(max_wait_s=20)
    if current:
        print("✅ Assignee set to Paolo.")
    else:
        print("ℹ️ Assignee after attempts is still empty/unset.")


def attach_file(
    config: Dict[str, str],
    issue_key: str,
    file_path: Path,
) -> None:
    """Attach a local file to the given Jira issue."""
    if not file_path.exists():
        print(f"⚠️ Evidence file not found, skipping attachment: {file_path}")
        return

    url = (
        f"{config['JIRA_BASE_URL'].rstrip('/')}/rest/api/3/issue/"
        f"{issue_key}/attachments"
    )
    auth = (config["JIRA_EMAIL"], config["JIRA_API_TOKEN"])

    headers = {
        "X-Atlassian-Token": "no-check",
    }

    with file_path.open("rb") as f:
        files = {"file": (file_path.name, f)}
        response = requests.post(
            url, auth=auth, headers=headers, files=files, timeout=60
        )

    if response.status_code not in (200, 201):
        print(f"⚠️ Failed to upload attachment: {response.status_code}")
        print(response.text)
    else:
        print(f"📎 Attached evidence file: {file_path.name}")


def build_comment_body(config: Dict[str, str]) -> Dict[str, Any]:
    """
    Build a Jira Cloud comment body that states the bug was generated automatically
    and mentions the reporter.
    """
    reporter_account_id = config.get("REPORTER_ACCOUNT_ID")
    reporter_name = config.get("REPORTER_DISPLAY_NAME", "QA Engineer")

    content: Dict[str, Any] = {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {
                        "type": "text",
                        "text": "BUG was generated automatically. ",
                    },
                ],
            }
        ],
    }

    mention_text = f"@{reporter_name}"
    if reporter_account_id:
        # Jira Cloud supports user mentions in ADF with `mention` node.
        mention_node = {
            "type": "mention",
            "attrs": {
                "id": reporter_account_id,
                "text": mention_text,
                "userType": "DEFAULT",
            },
        }
        content["content"][0]["content"].append(mention_node)
    else:
        # Fallback: non-clickable mention text.
        content["content"][0]["content"].append(
            {"type": "text", "text": mention_text}
        )

    return content


def extract_issue_key(parent_arg: str) -> str:
    """Extract a Jira issue key from a URL or return as-is if already a key."""
    import re
    # Match keys like CCAI-494, ABC-123
    match = re.search(r"([A-Z][A-Z0-9]+-\d+)", parent_arg)
    if match:
        return match.group(1)
    raise ValueError(f"Could not extract a Jira issue key from: {parent_arg}")




def add_comment(
    config: Dict[str, str],
    issue_key: str,
) -> None:
    """Add an automated comment mentioning the reporter."""
    url = (
        f"{config['JIRA_BASE_URL'].rstrip('/')}/rest/api/3/issue/"
        f"{issue_key}/comment"
    )
    auth = (config["JIRA_EMAIL"], config["JIRA_API_TOKEN"])
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    body = build_comment_body(config)

    response = requests.post(
        url,
        auth=auth,
        headers={**headers, "x-atlassian-force-account-id": "true"},
        json={"body": body},
        timeout=30,
    )

    if response.status_code not in (200, 201):
        print(f"⚠️ Failed to add comment: {response.status_code}")
        print(response.text)
    else:
        print("💬 Added automated comment mentioning reporter.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create a Jira BUG in CCAI with evidence and an automated comment, "
            "using Claude AI to generate the full bug details from a short summary."
        )
    )
    parser.add_argument(
        "--summary",
        "-s",
        help="Short summary of the bug (used as input for AI and as ticket title).",
        required=True,
    )
    parser.add_argument(
        "--url",
        "-u",
        help=(
            "Bug URL to set in the Jira template. If omitted, defaults to "
            "https://qai-ui.qa.ai.fpscloud.com"
        ),
        required=False,
    )
    parser.add_argument(
        "--evidence",
        "-e",
        help="Path to evidence file (image/video) to attach to the bug.",
        required=False,
    )
    parser.add_argument(
        "--parent",
        "-p",
        help=(
            "URL or key of the parent Jira ticket. "
            "The bug will be linked as child of this ticket. "
            "Example: https://fpsinc.atlassian.net/browse/CCAI-494 or CCAI-494"
        ),
        required=False,
    )
    return parser.parse_args()


def main() -> None:
    config = load_config()
    args = parse_args()

    default_url = "https://qai-ui.qa.ai.fpscloud.com"
    url_value = args.url.strip() if args.url else default_url

    # 1) Use AI to generate the full bug content from the short summary.
    evidence_path = Path(args.evidence) if args.evidence else None
    bug = generate_bug_with_claude(config, args.summary, evidence_path, url_value)

    # Enforce URL exactly as requested (prevents Claude from inventing a URL).
    bug["url"] = url_value

    # Resolve parent key before creating the issue.
    parent_key: Optional[str] = None
    if args.parent:
        try:
            parent_key = extract_issue_key(args.parent)
            print(f"ℹ️ Parent ticket: {parent_key}")
        except ValueError as e:
            print(f"⚠️ {e}")

    issue = create_issue(config, bug, parent_key=parent_key)
    issue_key = issue.get("key")

    if parent_key:
        print(f"🔗 {issue_key} created as child of {parent_key}")

    ccai_set = set_ccai_product(config, issue_key)
    if ccai_set:
        ensure_assigned(config, issue_key)
    else:
        print(f"⚠️ Skipping Paolo assignment — CCAI Product not set on {issue_key}")

    if args.evidence:
        attach_file(config, issue_key, evidence_path)

    add_comment(config, issue_key)

    issue_url = f"{config['JIRA_BASE_URL'].rstrip('/')}/browse/{issue_key}"
    print(f"🔗 Issue URL: {issue_url}")


if __name__ == "__main__":
    main()

