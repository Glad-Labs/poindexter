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
    ken_burns: bool = True,
    ken_burns_zoom_range: list[float] | None = None,
    transition_duration: float | None = None,
) -> dict:
    """Generate a Ken Burns slideshow video from images + audio narration.

    Each image gets a slow zoom/pan effect. Crossfade transitions between images.
    Audio (podcast MP3) plays as narration over the slideshow.

    Args:
        ken_burns: Enable zoom/pan effects on images (default True).
        ken_burns_zoom_range: [min_zoom, max_zoom] e.g. [1.0, 1.15].
        transition_duration: Approx seconds per image. None = auto from audio.

    Returns dict with output_path, duration_seconds, file_size_bytes.
    """
    if not image_paths:
        return {"error": "No images provided"}
    if not audio_path or not os.path.exists(audio_path):
        return {"error": f"Audio file not found: {audio_path}"}

    zoom_min, zoom_max = (ken_burns_zoom_range or [1.0, 1.15])

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
    if transition_duration and transition_duration > 0:
        time_per_image = float(transition_duration)
    else:
        time_per_image = (audio_duration + crossfade_dur * (n_images - 1)) / n_images
    time_per_image = max(5.0, time_per_image)  # At least 5s per image

    if not output_path:
        output_path = str(OUTPUT_DIR / f"{uuid.uuid4().hex[:12]}.mp4")

    fps = 30
    filter_parts = []
    inputs = []

    # Ken Burns effect patterns — alternate zoom-in, zoom-out, pan-left, pan-right
    import random
    random.seed(len(image_paths))  # deterministic per image set
    effects = ["zoom_in", "zoom_out", "pan_left", "pan_right"]

    for i, img_path in enumerate(image_paths):
        frames = int(time_per_image * fps)
        inputs.extend(["-loop", "1", "-t", str(time_per_image), "-i", img_path])

        if ken_burns:
            effect = effects[i % len(effects)]
            # zoompan: zoom factor over time, position for panning
            # We render at 2x then scale down for smooth zoom
            zp_w, zp_h = 1920 * 2, 1080 * 2
            if effect == "zoom_in":
                zoom_expr = f"min({zoom_min}+on/{frames}*{zoom_max - zoom_min}\\,{zoom_max})"
                x_expr = f"(iw-iw/zoom)/2"
                y_expr = f"(ih-ih/zoom)/2"
            elif effect == "zoom_out":
                zoom_expr = f"max({zoom_max}-on/{frames}*{zoom_max - zoom_min}\\,{zoom_min})"
                x_expr = f"(iw-iw/zoom)/2"
                y_expr = f"(ih-ih/zoom)/2"
            elif effect == "pan_left":
                zoom_expr = f"{zoom_max}"
                x_expr = f"(iw-iw/zoom)*on/{frames}"
                y_expr = f"(ih-ih/zoom)/2"
            else:  # pan_right
                zoom_expr = f"{zoom_max}"
                x_expr = f"(iw-iw/zoom)*(1-on/{frames})"
                y_expr = f"(ih-ih/zoom)/2"

            filter_parts.append(
                f"[{i}:v]scale={zp_w}:{zp_h}:force_original_aspect_ratio=decrease,"
                f"pad={zp_w}:{zp_h}:(ow-iw)/2:(oh-ih)/2:black,"
                f"zoompan=z='{zoom_expr}':x='{x_expr}':y='{y_expr}'"
                f":d={frames}:s=1920x1080:fps={fps},"
                f"setsar=1[v{i}]"
            )
        else:
            filter_parts.append(
                f"[{i}:v]scale=1920:1080:force_original_aspect_ratio=decrease,"
                f"pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black,"
                f"setsar=1,fps={fps}[v{i}]"
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


def _generate_short_video(
    image_paths: list[str],
    audio_path: str,
    title: str = "",
    output_path: str | None = None,
    max_duration: float = 60.0,
    ken_burns: bool = True,
    ken_burns_zoom_range: list[float] | None = None,
) -> dict:
    """Generate a vertical (9:16) short-form video for TikTok/YouTube Shorts.

    Similar to full-length but:
    - 1080x1920 (vertical, 9:16)
    - Max 60 seconds
    - First N seconds of audio only
    - Faster transitions (5-8s per image)
    """
    if not image_paths:
        return {"error": "No images provided"}
    if not audio_path or not os.path.exists(audio_path):
        return {"error": f"Audio file not found: {audio_path}"}

    zoom_min, zoom_max = (ken_burns_zoom_range or [1.0, 1.2])

    # Get audio duration, cap at max_duration
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", audio_path],
        capture_output=True, text=True,
    )
    audio_duration = float(probe.stdout.strip()) if probe.stdout.strip() else 60.0
    video_duration = min(audio_duration, max_duration)

    # Use fewer images for short video (3-4 max)
    max_images = min(4, len(image_paths))
    image_paths = image_paths[:max_images]
    n_images = len(image_paths)

    time_per_image = video_duration / n_images
    time_per_image = max(5.0, time_per_image)
    crossfade_dur = 0.5  # Faster crossfade for shorts

    if not output_path:
        output_path = str(OUTPUT_DIR / f"short-{uuid.uuid4().hex[:12]}.mp4")

    fps = 30
    filter_parts = []
    inputs = []

    import random
    random.seed(len(image_paths) + 1)
    effects = ["zoom_in", "zoom_out", "pan_left", "pan_right"]

    # Vertical: 1080x1920
    w, h = 1080, 1920
    zp_w, zp_h = w * 2, h * 2

    for i, img_path in enumerate(image_paths):
        frames = int(time_per_image * fps)
        inputs.extend(["-loop", "1", "-t", str(time_per_image), "-i", img_path])

        if ken_burns:
            effect = effects[i % len(effects)]
            if effect == "zoom_in":
                zoom_expr = f"min({zoom_min}+on/{frames}*{zoom_max - zoom_min}\\,{zoom_max})"
                x_expr = f"(iw-iw/zoom)/2"
                y_expr = f"(ih-ih/zoom)/2"
            elif effect == "zoom_out":
                zoom_expr = f"max({zoom_max}-on/{frames}*{zoom_max - zoom_min}\\,{zoom_min})"
                x_expr = f"(iw-iw/zoom)/2"
                y_expr = f"(ih-ih/zoom)/2"
            elif effect == "pan_left":
                zoom_expr = f"{zoom_max}"
                x_expr = f"(iw-iw/zoom)*on/{frames}"
                y_expr = f"(ih-ih/zoom)/2"
            else:
                zoom_expr = f"{zoom_max}"
                x_expr = f"(iw-iw/zoom)*(1-on/{frames})"
                y_expr = f"(ih-ih/zoom)/2"

            filter_parts.append(
                f"[{i}:v]scale={zp_w}:{zp_h}:force_original_aspect_ratio=decrease,"
                f"pad={zp_w}:{zp_h}:(ow-iw)/2:(oh-ih)/2:black,"
                f"zoompan=z='{zoom_expr}':x='{x_expr}':y='{y_expr}'"
                f":d={frames}:s={w}x{h}:fps={fps},"
                f"setsar=1[v{i}]"
            )
        else:
            filter_parts.append(
                f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,"
                f"setsar=1,fps={fps}[v{i}]"
            )

    if n_images == 1:
        filter_parts.append(f"[v0]trim=duration={video_duration}[outv]")
    else:
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

    cmd = ["ffmpeg", "-y"]
    cmd.extend(inputs)
    cmd.extend(["-i", audio_path])
    cmd.extend([
        "-filter_complex", filter_complex,
        "-map", "[outv]",
        "-map", f"{n_images}:a",
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
        "-t", str(video_duration),
        "-movflags", "+faststart",
        output_path,
    ])

    print(f"[SHORT] ffmpeg cmd: {n_images} images, {video_duration:.0f}s, {w}x{h}")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode == 0 and os.path.exists(output_path):
            stat = os.stat(output_path)
            print(f"[SHORT] Success: {stat.st_size} bytes, {round(video_duration)}s")
            return {
                "output_path": output_path,
                "duration_seconds": round(video_duration),
                "file_size_bytes": stat.st_size,
                "format": "vertical_9_16",
                "resolution": f"{w}x{h}",
                "images_used": n_images,
            }
        print(f"[SHORT] ffmpeg failed (rc={result.returncode}): {result.stderr[-500:]}")
        return {"error": f"ffmpeg failed: {result.stderr[-500:]}"}
    except subprocess.TimeoutExpired:
        return {"error": "ffmpeg timed out"}


class VideoHandler(BaseHTTPRequestHandler):
    """HTTP handler for video generation requests."""

    def do_POST(self):
        if self.path == "/generate-short":
            content_length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_length)) if content_length else {}

            image_paths = body.get("image_paths", [])
            audio_path = body.get("audio_path", "")
            title = body.get("title", "")

            if not image_paths or not audio_path:
                self._respond(400, {"error": "image_paths and audio_path required"})
                return

            try:
                start = time.time()
                result = _generate_short_video(
                    image_paths, audio_path, title,
                    max_duration=body.get("max_duration", 60.0),
                    ken_burns=body.get("ken_burns", True),
                    ken_burns_zoom_range=body.get("ken_burns_zoom_range"),
                )
                elapsed = time.time() - start
                result["elapsed_seconds"] = round(elapsed, 2)

                if "error" in result:
                    self._respond(500, result)
                else:
                    with open(result["output_path"], "rb") as f:
                        video_data = f.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "video/mp4")
                    self.send_header("Content-Length", str(len(video_data)))
                    self.send_header("X-Elapsed-Seconds", str(result["elapsed_seconds"]))
                    self.send_header("X-Duration-Seconds", str(result["duration_seconds"]))
                    self.send_header("X-Format", "vertical_9_16")
                    self.send_header("X-Local-Path", result["output_path"])
                    self.end_headers()
                    self.wfile.write(video_data)
            except Exception as e:
                self._respond(500, {"error": str(e)})

        elif self.path == "/generate":
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
                result = _generate_video(
                    image_paths, audio_path, title,
                    ken_burns=body.get("ken_burns", True),
                    ken_burns_zoom_range=body.get("ken_burns_zoom_range"),
                    transition_duration=body.get("transition_duration"),
                )
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
