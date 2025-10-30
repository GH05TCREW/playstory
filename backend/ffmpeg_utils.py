import subprocess
from PIL import Image
import tempfile
import os


def _probe_duration(input_video: str) -> float | None:
    """Return duration in seconds using ffprobe, or None if unavailable."""
    try:
        out = subprocess.check_output([
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            input_video,
        ], stderr=subprocess.STDOUT, text=True).strip()
        return float(out)
    except Exception:
        return None


def last_frame(input_video: str, out_jpg: str):
    """Extract a near-final frame robustly on Windows.
    Strategy:
    1) Try ffprobe to get duration and seek to duration-0.1s, then grab 1 frame
    2) If probe fails, try sseof -0.25
    3) If that fails, try a mid-frame at 50% duration or 1s mark
    """
    dur = _probe_duration(input_video)

    # Attempt A: precise seek near end using -ss
    if dur and dur > 0.2:
        seek = max(0.0, dur - 0.10)
        try:
            subprocess.run([
                "ffmpeg", "-y",
                "-ss", f"{seek:.2f}",
                "-i", input_video,
                "-frames:v", "1",
                "-q:v", "2",
                "-update", "1",
                out_jpg,
            ], check=True)
            return
        except subprocess.CalledProcessError:
            pass

    # Attempt B: sseof fallback slightly earlier to avoid edge
    try:
        subprocess.run([
            "ffmpeg", "-y",
            "-sseof", "-0.25",
            "-i", input_video,
            "-frames:v", "1",
            "-q:v", "2",
            "-update", "1",
            out_jpg,
        ], check=True)
        return
    except subprocess.CalledProcessError:
        pass

    # Attempt C: mid-point or 1s mark as a last resort
    fallback_seek = 1.0
    if dur and dur > 0:
        fallback_seek = max(0.5, dur * 0.5)
    subprocess.run([
        "ffmpeg", "-y",
        "-ss", f"{fallback_seek:.2f}",
        "-i", input_video,
        "-frames:v", "1",
        "-q:v", "2",
        "-update", "1",
        out_jpg,
    ], check=True)


def ensure_resolution(img_path: str, size_str: str):  # size like "1280x720"
    target_w, target_h = map(int, size_str.split("x"))
    im = Image.open(img_path).convert("RGB")
    if im.size != (target_w, target_h):
        im = im.resize((target_w, target_h), Image.LANCZOS)
        im.save(img_path, quality=95)


def concat_videos(input1: str, input2: str, output_path: str, size: str = "1280x720", fps: int = 30):
    """
    Concatenate two mp4 clips robustly on Windows.
    Strategy:
    1) Try concat demuxer directly (fast path) if streams are compatible
    2) Fallback: re-encode with filter_complex concat ensuring same size/fps/audio
    """
    # Fast path using concat demuxer requires identical codecs/params
    list_file = None
    try:
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".txt") as f:
            list_file = f.name
            # Use absolute paths and proper quoting
            f.write(f"file '{os.path.abspath(input1).replace("'", "'\\''")}'\n")
            f.write(f"file '{os.path.abspath(input2).replace("'", "'\\''")}'\n")
        subprocess.run([
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", list_file,
            "-c", "copy",
            output_path,
        ], check=True)
        return
    except subprocess.CalledProcessError:
        pass
    finally:
        if list_file and os.path.exists(list_file):
            try:
                os.remove(list_file)
            except Exception:
                pass

    # Fallback: re-encode with filter_complex concat
    w, h = map(int, size.split("x"))
    try:
        subprocess.run([
            "ffmpeg", "-y",
            "-i", input1,
            "-i", input2,
            "-filter_complex",
            (
                f"[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease,setsar=1:1,fps={fps}[v0];"
                f"[1:v]scale={w}:{h}:force_original_aspect_ratio=decrease,setsar=1:1,fps={fps}[v1];"
                f"[0:a]anull[a0];[1:a]anull[a1];"
                f"[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]"
            ),
            "-map", "[v]",
            "-map", "[a]",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "21",
            "-c:a", "aac", "-b:a", "128k",
            output_path,
        ], check=True)
        return
    except subprocess.CalledProcessError:
        # Try video-only concat (no audio streams)
        subprocess.run([
            "ffmpeg", "-y",
            "-i", input1,
            "-i", input2,
            "-filter_complex",
            (
                f"[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease,setsar=1:1,fps={fps}[v0];"
                f"[1:v]scale={w}:{h}:force_original_aspect_ratio=decrease,setsar=1:1,fps={fps}[v1];"
                f"[v0][v1]concat=n=2:v=1:a=0[v]"
            ),
            "-map", "[v]",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "21",
            output_path,
        ], check=True)
