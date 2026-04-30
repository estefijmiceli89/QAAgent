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
import base64
import mimetypes
import os
import shutil
import subprocess
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


def _video_mime_type(path: Path) -> Optional[str]:
    """MIME type for video evidence (Claude gets frames; Jira still receives the original file)."""
    suffix = path.suffix.lower()
    if suffix == ".mp4":
        return "video/mp4"
    if suffix == ".webm":
        return "video/webm"
    if suffix in {".mov", ".m4v"}:
        return "video/quicktime"
    return None


def _attachment_content_type(path: Path) -> str:
    """Content-Type for Jira multipart upload (helps avoid 415 / broken uploads for video)."""
    return (
        _mime_type_for_path(path)
        or _video_mime_type(path)
        or mimetypes.guess_type(path.name)[0]
        or "application/octet-stream"
    )


def _ffmpeg_available() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _video_duration_seconds(path: Path) -> float:
    r = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "").strip() or "ffprobe failed")
    return max(0.1, float((r.stdout or "0").strip() or 0))


def _extract_video_key_frame_jpegs(path: Path, num_frames: int = 6) -> List[bytes]:
    """
    Sample evenly spaced JPEG frames for vision models (Claude has no raw MP4 input).
    """
    duration = _video_duration_seconds(path)
    out: List[bytes] = []
    for i in range(1, num_frames + 1):
        t = duration * i / (num_frames + 1)
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            str(t),
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-vf",
            "scale=1280:-1",
            "-f",
            "image2pipe",
            "-vcodec",
            "mjpeg",
            "-q:v",
            "3",
            "-",
        ]
        r = subprocess.run(cmd, capture_output=True, timeout=180)
        if r.returncode == 0 and r.stdout:
            out.append(r.stdout)
    if not out:
        raise RuntimeError("No se pudieron extraer fotogramas del vídeo (ffmpeg).")
    return out


def _evidence_blocks_for_claude(evidence_path: Path) -> List[Dict[str, Any]]:
    """Build multimodal user content blocks (images and/or video-as-frames) before the text prompt."""
    blocks: List[Dict[str, Any]] = []
    if not evidence_path.exists():
        return blocks

    image_mime = _mime_type_for_path(evidence_path)
    if image_mime:
        data_b64 = base64.b64encode(evidence_path.read_bytes()).decode("utf-8")
        blocks.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": image_mime,
                    "data": data_b64,
                },
            }
        )
        return blocks

    if _video_mime_type(evidence_path):
        if not _ffmpeg_available():
            print(
                "⚠️ ffmpeg/ffprobe no está en PATH: el MP4 se adjuntará a Jira, "
                "pero Claude no podrá ver el vídeo (instala ffmpeg o usa capturas PNG/JPEG)."
            )
            return blocks
        try:
            frames = _extract_video_key_frame_jpegs(evidence_path)
            blocks.append(
                {
                    "type": "text",
                    "text": (
                        "Las siguientes imágenes son fotogramas tomados a intervalos regulares "
                        "de una grabación de pantalla (archivo de vídeo). "
                        "Úsalos junto con el resumen del bug para describir la UI, el error "
                        "visible y el flujo."
                    ),
                }
            )
            for jpeg_bytes in frames:
                blocks.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": base64.b64encode(jpeg_bytes).decode("utf-8"),
                        },
                    }
                )
        except Exception as e:
            print(f"⚠️ No se pudo leer el vídeo para la IA: {e}")
        return blocks

    return blocks


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


def refine_jira_summary_with_claude(
    client: Anthropic,
    draft_summary: str,
    short_summary: str,
    description: str,
) -> str:
    """
    Second, focused pass: polish only the Jira issue title for clarity and length.
    """
    context = (description or "").strip()
    if len(context) > 2000:
        context = context[:2000] + "…"

    system = (
        "You only output the final Jira issue title (summary), as a single line of plain text. "
        "No quotes, no markdown, no explanation, no trailing period. "
        "Max 100 characters. Preserve the same language as the draft title "
        "(Spanish or English). If the draft is already excellent, return it unchanged "
        "or minimally adjusted."
    )
    user_text = (
        f"Original user hint:\n{short_summary.strip()}\n\n"
        f"Draft title to improve:\n{draft_summary.strip()}\n\n"
        f"Bug description (for accuracy only):\n{context or '(none)'}\n"
    )

    try:
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=200,
            system=system,
            messages=[{"role": "user", "content": user_text}],
        )
        refined = (message.content[0].text or "").strip()
        # Strip accidental quotes or fences
        refined = refined.strip('"').strip("'").strip()
        if "\n" in refined:
            refined = refined.split("\n", 1)[0].strip()
        if not refined:
            return draft_summary.strip()
        if len(refined) > 255:
            refined = refined[:252] + "…"
        print("🤖 Issue title refined by AI.")
        return refined
    except Exception as e:
        print(f"⚠️ Could not refine summary with AI, using draft title: {e}")
        return draft_summary.strip()


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
        "Expected Behaviour:\n\n"
        "Respond ONLY with a valid JSON object, no extra text, with exactly these keys:\n"
        "  summary, url, pre_conditions, description,\n"
        "  steps_to_reproduce, current_behaviour, expected_behaviour.\n"
        "Values should be strings. Do not include markdown, just plain text.\n\n"
        "For the JSON key \"summary\" (this becomes the Jira issue title):\n"
        "- Write a clear, specific title a developer can scan in a backlog.\n"
        "- Prefer problem + where/when (component, screen, or action), not vague text.\n"
        "- Keep it under 100 characters; Jira allows up to ~255 but short titles work better.\n"
        "- No trailing period; no markdown; no leading \"Bug:\" or ticket prefixes.\n"
        "- Match the language of the user's short summary (Spanish or English).\n\n"
        "If the user attached images or video frames, ground your description, steps, "
        "and current vs expected behaviour in what is visibly shown."
    )

    user_prompt = (
        f"Short summary of the bug:\n{short_summary}\n\n"
        f"Use this URL for the ticket:\n{url_value}\n"
    )

    try:
        content_blocks: List[Dict[str, Any]] = []
        if evidence_path:
            content_blocks.extend(_evidence_blocks_for_claude(evidence_path))

        content_blocks.append({"type": "text", "text": user_prompt})

        message = client.messages.create(
            # Use the same model you already use for test cases
            model="claude-sonnet-4-20250514",
            max_tokens=1200,
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
    ]
    for key in required_keys:
        if key not in data:
            print(f"❌ Claude JSON is missing required key: {key}")
            sys.exit(1)

    bug = {
        "summary": str(data["summary"] or "").strip(),
        "url": data["url"],
        "pre_conditions": data["pre_conditions"],
        "description": data["description"],
        "steps_to_reproduce": data["steps_to_reproduce"],
        "current_behaviour": data["current_behaviour"],
        "expected_behaviour": data["expected_behaviour"],
    }

    print("🤖 Bug content generated by AI.")

    bug["summary"] = refine_jira_summary_with_claude(
        client,
        bug["summary"],
        short_summary,
        bug.get("description", ""),
    )

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
) -> bool:
    """
    POST multipart attachment to Jira Cloud (/rest/api/3/issue/{key}/attachments).

    Uses explicit Content-Type, Accept: application/json, longer timeouts for MP4s,
    and a few retries on transient errors.
    """
    path = file_path.expanduser().resolve()
    if not path.is_file():
        print(f"❌ Archivo de evidencia no encontrado: {path}")
        return False

    url = (
        f"{config['JIRA_BASE_URL'].rstrip('/')}/rest/api/3/issue/"
        f"{issue_key}/attachments"
    )
    auth = (config["JIRA_EMAIL"], config["JIRA_API_TOKEN"])
    content_type = _attachment_content_type(path)
    size = path.stat().st_size
    # Screen recordings need more than 60s; cap at 15 minutes.
    timeout = max(120, min(900, 60 + size // (200 * 1024)))

    headers = {
        "X-Atlassian-Token": "no-check",
        "Accept": "application/json",
        "x-atlassian-force-account-id": "true",
    }

    last_status: Optional[int] = None
    last_body = ""

    for attempt in range(1, 4):
        try:
            with path.open("rb") as fh:
                files = {"file": (path.name, fh, content_type)}
                response = requests.post(
                    url,
                    auth=auth,
                    headers=headers,
                    files=files,
                    timeout=timeout,
                )
        except requests.RequestException as exc:
            last_status = None
            last_body = str(exc)
            print(f"⚠️ Error al subir adjunto (intento {attempt}/3): {exc}")
            time.sleep(2 * attempt)
            continue

        last_status = response.status_code
        last_body = response.text or ""

        if response.status_code in (200, 201):
            try:
                data = response.json()
            except ValueError:
                print(f"❌ Respuesta de adjunto no es JSON: {last_body[:600]}")
                return False
            if not isinstance(data, list) or not data:
                print(f"❌ Jira no devolvió el adjunto en la respuesta: {last_body[:600]}")
                return False
            name = data[0].get("filename") or path.name
            print(f"📎 Adjuntado en Jira: {name} ({size:,} bytes)")
            return True

        if response.status_code in (429, 503) and attempt < 3:
            print(f"⚠️ Adjunto rechazado ({response.status_code}), reintentando…")
            time.sleep(3 * attempt)
            continue

        break

    err = f"❌ No se pudo adjuntar la evidencia"
    if last_status is not None:
        err += f" (HTTP {last_status})"
    err += f": {last_body[:1000]}"
    print(err)
    return False


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
        help=(
            "Short description of the bug; Claude generates the fields and refines "
            "the Jira issue title (summary)."
        ),
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

    optional_evidence: Optional[Path] = None
    if args.evidence:
        optional_evidence = Path(args.evidence).expanduser().resolve()
        if not optional_evidence.is_file():
            print(f"❌ Archivo de evidencia no encontrado: {optional_evidence}")
            sys.exit(1)

    # 1) IA primero: genera el cuerpo del bug a partir del resumen + evidencia (imagen o fotogramas del vídeo).
    bug = generate_bug_with_claude(
        config, args.summary, optional_evidence, url_value
    )

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
    if not issue_key:
        print("❌ Jira no devolvió la clave del ticket; no se puede adjuntar evidencia.")
        sys.exit(1)

    if parent_key:
        print(f"🔗 {issue_key} created as child of {parent_key}")

    ccai_set = set_ccai_product(config, issue_key)
    if ccai_set:
        ensure_assigned(config, issue_key)
    else:
        print(f"⚠️ Skipping Paolo assignment — CCAI Product not set on {issue_key}")

    # 2) Después: adjuntar el archivo original (p. ej. MP4 completo) en el ticket.
    if optional_evidence is not None:
        if not attach_file(config, issue_key, optional_evidence):
            sys.exit(1)

    add_comment(config, issue_key)

    issue_url = f"{config['JIRA_BASE_URL'].rstrip('/')}/browse/{issue_key}"
    print(f"🔗 Issue URL: {issue_url}")


if __name__ == "__main__":
    main()

