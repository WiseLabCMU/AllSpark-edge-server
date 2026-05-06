from nicegui import app, ui
import base64
import os
import shutil
import subprocess
from pathlib import Path

from fastapi.responses import FileResponse, Response

# Import all pages to register their routes
from pages import clients, agent, rerun_view, settings, debug, logs

# Initialize pages
clients.create_page()
agent.create_page()
rerun_view.create_page()
settings.create_page()
debug.create_page()
logs.create_page()

@ui.page('/')
def index():
    ui.navigate.to('/agent')


# ---------------------------------------------------------------------------
# /api/clip-video  — stream an NVR clip to the browser
#
# Query params:
#   path  : base64url-encoded absolute path to the original .mp4 clip
#
# Behaviour:
#   1. Decode + validate the path is inside an allowed directory.
#   2. Prefer the .h264.mp4 sidecar (created by rerun_server or below).
#   3. If the sidecar does not exist yet, create it via ffmpeg (non-blocking
#      for the caller — the sidecar is written before the response is sent).
#   4. Stream the file as video/mp4.
# ---------------------------------------------------------------------------

def _allowed_clip_roots() -> list[Path]:
    """Return the list of root directories from which clips may be served."""
    cfg = settings.load_config()
    mc  = cfg.get("mobile_client", {}) or {}
    roots: list[Path] = []
    # uploads/ root (local clips from mobile clients)
    uploads = Path(os.path.abspath(
        os.path.join(os.path.dirname(__file__), '..', '..', 'uploads')
    ))
    roots.append(uploads)
    # anomalyEventDirs from config (NFS paths)
    for d in mc.get("anomalyEventDirs", []):
        roots.append(Path(d))
    return roots


def _ensure_h264_sidecar(clip: Path) -> Path:
    """Return an H.264-encoded sidecar for *clip*, creating it if needed."""
    sidecar = clip.with_name(clip.stem + ".h264.mp4")
    if sidecar.exists():
        return sidecar
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return clip  # ffmpeg unavailable — serve original and hope for the best
    try:
        subprocess.run(
            [ffmpeg, "-y", "-v", "error", "-i", str(clip),
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
             "-movflags", "+faststart", str(sidecar)],
            check=True, timeout=300,
        )
        return sidecar
    except Exception:
        return clip  # transcode failed — serve original


@app.get("/api/clip-video")
async def clip_video(path: str) -> Response:
    """Stream a video clip (preferring H.264 sidecar) to the browser."""
    # Decode path
    try:
        raw = base64.urlsafe_b64decode(path + "==").decode("utf-8")
    except Exception:
        return Response(status_code=400, content="Invalid path encoding")

    clip = Path(raw).resolve()

    # Security: must be inside an allowed root
    allowed = _allowed_clip_roots()
    if not any(_is_relative_to(clip, r) for r in allowed):
        return Response(status_code=403, content="Path not in allowed directories")

    if not clip.exists():
        return Response(status_code=404, content="Clip not found")

    serve = _ensure_h264_sidecar(clip)
    return FileResponse(
        path=str(serve),
        media_type="video/mp4",
        headers={"Accept-Ranges": "bytes"},
    )


def _is_relative_to(path: Path, parent: Path) -> bool:
    """Path.is_relative_to() backport for Python < 3.9."""
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


if __name__ in {"__main__", "__mp_main__"}:
    # Read port from config.yaml and start NiceGUI on config['port'] + 1 (default 8081).
    full_config = settings.load_config()
    cp_config = full_config.get('control_plane', {})
    mc_config = full_config.get('mobile_client', {})

    edge_port = mc_config.get('port', 8080)
    sidecar_port = cp_config.get('port', edge_port + 1)

    # Mount the dynamic video storage directory for browser playback
    upload_path = mc_config.get('clientUploadsPath', 'uploads/mobile_clients')
    abs_upload_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', upload_path))
    os.makedirs(abs_upload_path, exist_ok=True)
    app.add_media_files('/videos', abs_upload_path)

    # Mount the uploads root so the Anomaly Feed can serve video clips and
    # other artefacts stored under uploads/agent_responses/... and
    # uploads/anomaly_*/agent_responses/... directly to the browser.
    abs_uploads_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), '..', '..', 'uploads')
    )
    os.makedirs(abs_uploads_root, exist_ok=True)
    app.add_media_files('/anomaly-media', abs_uploads_root)

    # Run the control plane
    storage_secret = cp_config.get('storageSecret', 'allspark-secret')
    ui.run(title='AllSpark Control Plane', port=sidecar_port, storage_secret=storage_secret)
