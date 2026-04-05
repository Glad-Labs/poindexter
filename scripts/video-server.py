"""Video Generation Server — creates narrated slideshow videos from images + audio.

Listens on port 9837. Worker calls POST /generate with images and audio to get MP4.
Uses ffmpeg with Ken Burns effect (slow zoom/pan) on SDXL-generated images.

Usage:
    pythonw scripts/video-server.py     # windowless background
    python scripts/video-server.py      # interactive
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

PORT = 9837
OUTPUT_DIR = Path.home() / "Downloads" / "glad-labs-generated-videos"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def _generate_video(
    image_paths: list[str],
    audio_path: str,
    title: str = "",
    output_path: str | None = None,
) -> dict:
    """Generate a Ken Burns slideshow video from images + audio narration.

    Each image gets a slow zoom or pan effect. Crossfade transitions between images.
    Audio (podcast MP3) plays as narration over the slideshow.

    Returns dict with output_path, duration_seconds, file_size_bytes.
    """
    if not image_paths:
        return {"error": "No images provided"}
    if not audio_path or not os.path.exists(audio_path):
        return {"error": f"Audio file not found: {audio_path}"}

    # Get audio duration
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", audio_path],
        capture_output=True, text=True,
    )
    audio_duration = float(probe.stdout.strip()) if probe.stdout.strip() else 300.0

    # Calculate time per image (with 1s crossfade overlap)
    n_images = len(image_paths)
    crossfade_dur = 1.0
    # Each image shows for (total_duration + overlaps) / n_images
    time_per_image = (audio_duration + crossfade_dur * (n_images - 1)) / n_images
    time_per_image = max(5.0, time_per_image)  # At least 5s per image

    if not output_path:
        output_path = str(OUTPUT_DIR / f"{uuid.uuid4().hex[:12]}.mp4")

    # Build complex filter — scale + crossfade (reliable, no zoompan corruption)
    filter_parts = []
    inputs = []

    for i, img_path in enumerate(image_paths):
        inputs.extend(["-loop", "1", "-t", str(time_per_image), "-i", img_path])
        filter_parts.append(
            f"[{i}:v]scale=1920:1080:force_original_aspect_ratio=decrease,"
            f"pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black,"
            f"setsar=1,fps=30[v{i}]"
        )

    # Crossfade transitions between segments
    if n_images == 1:
        filter_parts.append(f"[v0]trim=duration={audio_duration}[outv]")
    else:
        # Chain crossfades: v0 xfade v1 -> xf0, xf0 xfade v2 -> xf1, ...
        prev = "v0"
        for i in range(1, n_images):
            offset = time_per_image * i - crossfade_dur * i
            offset = max(0, offset)
            out_label = f"xf{i-1}" if i < n_images - 1 else "outv"
            filter_parts.append(
                f"[{prev}][v{i}]xfade=transition=fade:duration={crossfade_dur}:offset={offset:.2f}[{out_label}]"
            )
            prev = out_label

    filter_complex = ";".join(filter_parts)

    # Build ffmpeg command
    cmd = ["ffmpeg", "-y"]
    cmd.extend(inputs)
    cmd.extend(["-i", audio_path])
    cmd.extend([
        "-filter_complex", filter_complex,
        "-map", "[outv]",
        "-map", f"{n_images}:a",  # Audio is the last input
        "-c:v", "libx264",
        "-profile:v", "baseline",
        "-level", "3.1",
        "-pix_fmt", "yuv420p",
        "-preset", "medium",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "192k",
        "-ar", "44100",
        "-ac", "2",
        "-shortest",
        "-movflags", "+faststart",
        output_path,
    ])

    # Log the command for debugging
    print(f"[VIDEO] ffmpeg cmd: {len(cmd)} args, {n_images} images, filter: {len(filter_complex)} chars")
    print(f"[VIDEO] Input images: {image_paths}")
    print(f"[VIDEO] Audio: {audio_path}")
    print(f"[VIDEO] Output: {output_path}")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=600,
        )
        if result.returncode == 0 and os.path.exists(output_path):
            stat = os.stat(output_path)
            print(f"[VIDEO] Success: {stat.st_size} bytes, {round(audio_duration)}s")
            return {
                "output_path": output_path,
                "duration_seconds": round(audio_duration),
                "file_size_bytes": stat.st_size,
                "encoder": "libx264",
                "images_used": n_images,
            }
        print(f"[VIDEO] ffmpeg failed (rc={result.returncode}): {result.stderr[-500:]}")
        return {"error": f"ffmpeg failed: {result.stderr[-500:]}"}
    except subprocess.TimeoutExpired:
        return {"error": "ffmpeg timed out after 10 minutes"}


class VideoHandler(BaseHTTPRequestHandler):
    """HTTP handler for video generation requests."""

    def do_POST(self):
        if self.path == "/generate":
            content_length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_length)) if content_length else {}

            image_paths = body.get("image_paths", [])
            audio_path = body.get("audio_path", "")
            title = body.get("title", "")

            # Support base64 encoded data (for cross-container transfer)
            import base64
            import tempfile
            temp_files = []

            if not image_paths and "image_data" in body:
                # Decode base64 images to temp files
                for i, img_b64 in enumerate(body["image_data"]):
                    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False,
                                                      dir=str(OUTPUT_DIR))
                    tmp.write(base64.b64decode(img_b64))
                    tmp.close()
                    image_paths.append(tmp.name)
                    temp_files.append(tmp.name)

            if not audio_path and "audio_data" in body:
                tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False,
                                                  dir=str(OUTPUT_DIR))
                tmp.write(base64.b64decode(body["audio_data"]))
                tmp.close()
                audio_path = tmp.name
                temp_files.append(tmp.name)

            if not image_paths:
                self._respond(400, {"error": "image_paths or image_data is required"})
                return
            if not audio_path:
                self._respond(400, {"error": "audio_path or audio_data is required"})
                return

            try:
                start = time.time()
                result = _generate_video(image_paths, audio_path, title)
                elapsed = time.time() - start
                result["elapsed_seconds"] = round(elapsed, 2)

                if "error" in result:
                    self._respond(500, result)
                else:
                    # Return video bytes
                    with open(result["output_path"], "rb") as f:
                        video_data = f.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "video/mp4")
                    self.send_header("Content-Length", str(len(video_data)))
                    self.send_header("X-Elapsed-Seconds", str(result["elapsed_seconds"]))
                    self.send_header("X-Duration-Seconds", str(result["duration_seconds"]))
                    self.send_header("X-Local-Path", result["output_path"])
                    self.end_headers()
                    self.wfile.write(video_data)
            except Exception as e:
                self._respond(500, {"error": str(e)})
            finally:
                # Clean up temp files from base64 decoding
                for tf in temp_files:
                    try:
                        os.remove(tf)
                    except OSError:
                        pass
        else:
            self._respond(200, {
                "service": "Video Generator",
                "endpoint": "POST /generate",
                "params": {"image_paths": "list[str]", "audio_path": "str", "title": "str (optional)"},
            })

    def do_GET(self):
        if self.path == "/health":
            # Check ffmpeg availability
            try:
                subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
                ffmpeg_ok = True
            except Exception:
                ffmpeg_ok = False
            self._respond(200, {
                "status": "ok" if ffmpeg_ok else "degraded",
                "ffmpeg_available": ffmpeg_ok,
            })
        else:
            self._respond(200, {"service": "Video Generation Server", "port": PORT})

    def _respond(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # Silence request logging


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), VideoHandler)
    print(f"Video generation server on :{PORT}")
    print(f"Output dir: {OUTPUT_DIR}")
    print(f"ffmpeg: {subprocess.run(['ffmpeg', '-version'], capture_output=True, text=True).stdout.split(chr(10))[0]}")
    server.serve_forever()
