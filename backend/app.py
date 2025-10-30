import os
import uuid
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from storage import DB
from sora_client import start_video_job, get_video_job, download_result, download_job_content
from ffmpeg_utils import last_frame, ensure_resolution
from options_llm import propose_options

# Resolve media directory relative to repo root
ROOT = Path(__file__).resolve().parents[1]
MEDIA_DIR = ROOT / "media"
VIDEOS_DIR = MEDIA_DIR / "videos"
FRAMES_DIR = MEDIA_DIR / "frames"
VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
FRAMES_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_SECONDS = int(os.getenv("SORA_SECONDS", "8"))
DEFAULT_SIZE = os.getenv("SORA_SIZE", "1280x720")
DEFAULT_MODEL = os.getenv("SORA_MODEL", "sora-2")

app = FastAPI(title="PlayStory")

app.add_middleware(
    CORSMiddleware,
    # Be explicit to avoid conflicts with allow_credentials
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve local media
app.mount("/media", StaticFiles(directory=str(MEDIA_DIR)), name="media")


class StartReq(BaseModel):
    story_id: str
    base_prompt: str
    seconds: int | None = None
    size: str | None = None
    model: str | None = None


class ContinueReq(BaseModel):
    story_id: str
    parent_node_id: str
    choice_label: str
    sora_prompt: str
    seconds: int | None = None
    size: str | None = None
    model: str | None = None
    include_context: bool = False


@app.get("/")
def root():
    return {"ok": True, "message": "PlayStory backend running"}


@app.post("/start")
def start_story(req: StartReq):
    seconds = req.seconds or DEFAULT_SECONDS
    size = req.size or DEFAULT_SIZE
    model = req.model or DEFAULT_MODEL

    node_id = str(uuid.uuid4())
    try:
        job = start_video_job(model, req.base_prompt, seconds, size, None)
    except Exception as e:
        # Return a 400 with context so the frontend can show a friendly error
        raise HTTPException(status_code=400, detail=str(e))

    DB.add_node(
        id=node_id,
        parent_id=None,
        story_id=req.story_id,
        prompt=req.base_prompt,
        choice_text=None,
        sora_job_id=job.get("id") or job.get("job_id"),
        status=job.get("status", "queued"),
        video_path=None,
        last_frame_path=None,
        seconds=seconds,
        size=size,
        model=model,
    )
    # Initialize story summary with the base prompt for better LLM context
    try:
        DB.set_initial_summary(req.story_id, req.base_prompt)
    except Exception:
        pass
    return {"node_id": node_id, "job_id": job.get("id") or job.get("job_id")}


@app.get("/jobs/{job_id}")
def poll(job_id: str):
    j = get_video_job(job_id)
    status = j.get("status") or j.get("job_status")

    if status == "completed":
        # Try to locate a downloadable URL in various shapes
        download_url = None

        # direct keys
        for k in ("download_url", "asset_url", "video_url", "url"):
            val = j.get(k)
            if isinstance(val, str) and val.startswith("http"):
                download_url = val
                break

        # nested: output list/dict
        if not download_url:
            out = j.get("output")
            if isinstance(out, list) and out:
                cand = out[0]
                if isinstance(cand, dict):
                    download_url = cand.get("url") or cand.get("download_url")
            elif isinstance(out, dict):
                cand = out.get("video") or out.get("asset")
                if isinstance(cand, dict):
                    download_url = cand.get("url")

        # nested: assets dict/list
        if not download_url:
            assets = j.get("assets")
            if isinstance(assets, dict):
                for key in ("video", "original", "mp4"):
                    item = assets.get(key)
                    if isinstance(item, dict) and isinstance(item.get("url"), str):
                        download_url = item["url"]
                        break
                    if isinstance(item, str) and item.startswith("http"):
                        download_url = item
                        break
            elif isinstance(assets, list) and assets:
                for item in assets:
                    if isinstance(item, dict):
                        if item.get("type", "").startswith("video") and isinstance(item.get("url"), str):
                            download_url = item["url"]
                            break
                        if isinstance(item.get("url"), str):
                            download_url = item["url"]
                            break

        # nested: video dict
        if not download_url and isinstance(j.get("video"), dict):
            v = j["video"].get("url")
            if isinstance(v, str):
                download_url = v

        node = DB.get_by_job(job_id)
        if not node:
            return {"status": "error", "error": "Job not found in DB", "raw": j}

        # Per-story folders for faster, simple playback (no stitching)
        story_videos = VIDEOS_DIR / node.story_id
        story_frames = FRAMES_DIR / node.story_id
        story_videos.mkdir(parents=True, exist_ok=True)
        story_frames.mkdir(parents=True, exist_ok=True)

        video_path = story_videos / f"{node.id}.mp4"

        if download_url:
            download_result(download_url, str(video_path))
        else:
            # Fallback: GET /v1/videos/{id}/content streaming bytes
            try:
                download_job_content(job_id, str(video_path))
            except Exception as e:
                return {"status": "error", "error": f"No download URL and /content fetch failed: {e}", "raw": j}

        frame_path = story_frames / f"{node.id}.jpg"
        try:
            last_frame(str(video_path), str(frame_path))
            ensure_resolution(str(frame_path), node.size)
            frame_ok = True
        except Exception as e:
            # Don't crash the request; proceed without a frame and fall back to default options
            frame_ok = False
            frame_path = None

        # Mark done with this clip only (no stitching)
        DB.mark_done(node.id, str(video_path), str(frame_path))
        DB.set_latest(node.story_id, node.id)

        # Check if options already exist (cached from previous poll)
        import json
        cached_options_json = DB.get_options(node.id)
        if cached_options_json:
            # Options already generated, return cached version
            try:
                options = json.loads(cached_options_json)
                used_fallback = False
            except Exception:
                # If cached JSON is malformed, regenerate
                summary = DB.get_summary(node.story_id) or ""
                options, used_fallback = propose_options(summary, str(frame_path) if frame_path else "", n=3)
                DB.set_options(node.id, json.dumps(options))
        else:
            # First time completing, generate options
            summary = DB.get_summary(node.story_id) or ""
            options, used_fallback = propose_options(summary, str(frame_path) if frame_path else "", n=3)
            # Cache the generated options
            DB.set_options(node.id, json.dumps(options))

        return {
            "status": "completed",
            "node_id": node.id,
            "video_url": f"/media/videos/{node.story_id}/{video_path.name}",
            "frame_url": f"/media/frames/{node.story_id}/{node.id}.jpg" if frame_ok else None,
            "options": options,
            "options_source": "fallback" if used_fallback else "llm",
        }

    if status == "failed":
        # Extract a friendly error message if present and surface as-is
        err = j.get("error") if isinstance(j, dict) else None
        return {"status": "failed", "error": err or "Generation failed", "raw": j}

    return {"status": status, "raw": j}


@app.post("/continue")
def continue_story(req: ContinueReq):
    seconds = req.seconds or DEFAULT_SECONDS
    size = req.size or DEFAULT_SIZE
    model = req.model or DEFAULT_MODEL

    parent = DB.get_node(req.parent_node_id)
    if not parent:
        return {"error": "parent_node not found"}

    node_id = str(uuid.uuid4())
    frame_path = parent.last_frame_path
    
    # Build the final prompt with optional context
    final_prompt = req.sora_prompt
    if req.include_context:
        summary = DB.get_summary(req.story_id)
        if summary and summary.strip():
            # Create a compact context prefix from the last 3 story beats
            bullets = [b.strip() for b in summary.split("\n") if b.strip()]
            recent_beats = bullets[-3:] if len(bullets) > 3 else bullets
            context_text = " ".join([b.lstrip("- ") for b in recent_beats])
            # Limit to ~150 chars to avoid overwhelming the main prompt
            if len(context_text) > 150:
                context_text = context_text[:147] + "..."
            final_prompt = f"[Story context: {context_text}]\n\n{req.sora_prompt}"
    
    try:
        job = start_video_job(model, final_prompt, seconds, size, frame_path)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    DB.add_node(
        id=node_id,
        parent_id=parent.id,
        story_id=req.story_id,
        prompt=final_prompt,
        choice_text=req.choice_label,
        sora_job_id=job.get("id") or job.get("job_id"),
        status=job.get("status", "queued"),
        video_path=None,
        last_frame_path=None,
        seconds=seconds,
        size=size,
        model=model,
    )
    DB.update_summary(req.story_id, parent, req.choice_label, req.sora_prompt)

    return {"node_id": node_id, "job_id": job.get("id") or job.get("job_id")}


@app.get("/stories/{story_id}")
def story_graph(story_id: str):
    return DB.list_story(story_id)
