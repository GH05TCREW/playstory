import os
import time
import mimetypes
from pathlib import Path
import requests
from dotenv import load_dotenv

load_dotenv()

API_BASE = os.getenv("OPENAI_API_BASE", "https://api.openai.com")
API = f"{API_BASE}/v1/videos"


def _auth_headers():
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")
    return {"Authorization": f"Bearer {api_key}"}


def start_video_job(model: str, prompt: str, seconds: int, size: str, input_reference_path: str | None):
    headers = _auth_headers()
    if input_reference_path:
        file_path = Path(input_reference_path)
        mime, _ = mimetypes.guess_type(str(file_path))
        if not mime:
            mime = "image/jpeg"
        # Use only the currently supported field name: 'input_reference'
        # Avoid sending unknown parameters like 'input_image' that can cause 400s.
        with open(file_path, "rb") as f:
            files = [("input_reference", (file_path.name, f, mime))]
            data = {"model": model, "prompt": prompt, "seconds": str(seconds), "size": size}
            r = requests.post(API, headers=headers, files=files, data=data, timeout=120)
    else:
        headers = {**headers, "Content-Type": "application/json"}
        # The Videos API expects seconds as a string literal: "4" | "8" | "12"
        payload = {"model": model, "prompt": prompt, "seconds": str(seconds), "size": size}
        r = requests.post(API, headers=headers, json=payload, timeout=120)
    try:
        r.raise_for_status()
    except requests.HTTPError as e:
        # Surface helpful error context for debugging (e.g., model access or bad params)
        detail = None
        try:
            detail = r.json()
        except Exception:
            detail = r.text
        raise RuntimeError(f"Videos API error {r.status_code}: {detail}") from e
    return r.json()


def get_video_job(job_id: str):
    headers = _auth_headers()
    r = requests.get(f"{API}/{job_id}", headers=headers, timeout=60)
    r.raise_for_status()
    return r.json()


def download_result(download_url: str, local_path: str):
    with requests.get(download_url, stream=True, timeout=600) as r:
        r.raise_for_status()
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        with open(local_path, "wb") as f:
            for chunk in r.iter_content(1024 * 512):
                if chunk:
                    f.write(chunk)


def download_job_content(job_id: str, local_path: str):
    """Fallback path: some snapshots expose bytes via /v1/videos/{id}/content instead of an asset URL."""
    headers = _auth_headers()
    with requests.get(f"{API}/{job_id}/content", headers=headers, stream=True, timeout=600) as r:
        r.raise_for_status()
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        with open(local_path, "wb") as f:
            for chunk in r.iter_content(1024 * 512):
                if chunk:
                    f.write(chunk)
