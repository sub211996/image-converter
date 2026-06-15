from __future__ import annotations

import io
import tempfile
from pathlib import Path

from flask import Flask, Response, render_template_string, request, send_file
from PIL import Image

try:
    import rawpy
except ImportError:  # pragma: no cover
    rawpy = None


BASE_DIR = Path(__file__).resolve().parent
HTML_PATH = BASE_DIR / "index.html"

app = Flask(__name__)

ALLOWED_RAW_EXTENSIONS = {
    ".cr2",
    ".rw2",
    ".nef",
    ".arw",
    ".sr2",
    ".orf",
    ".pef",
    ".raf",
    ".raw",
    ".dng",
}


def _is_allowed_file(filename: str) -> bool:
    return Path(filename.lower()).suffix in ALLOWED_RAW_EXTENSIONS


@app.get("/")
def home() -> str:
    return render_template_string(HTML_PATH.read_text(encoding="utf-8"))


@app.post("/convert")
def convert_raw_to_jpg() -> Response:
    uploaded = request.files.get("file")
    if uploaded is None or not uploaded.filename:
        return Response("No file uploaded.", status=400)

    if not _is_allowed_file(uploaded.filename):
        return Response("Unsupported file type. Please upload a RAW camera file.", status=400)

    if rawpy is None:
        return Response(
            "rawpy is not installed. Install the requirements first.",
            status=500,
        )

    try:
        # rawpy decodes camera RAW formats into RGB data for Pillow to save as JPG.
        with tempfile.NamedTemporaryFile(suffix=Path(uploaded.filename).suffix, delete=True) as temp_file:
            temp_file.write(uploaded.read())
            temp_file.flush()

            with rawpy.imread(temp_file.name) as raw:
                rgb = raw.postprocess(
                    output_bps=8,
                    no_auto_bright=True,
                    use_camera_wb=True,
                )
    except Exception as exc:  # pragma: no cover
        return Response(f"Could not convert this RAW file: {exc}", status=400)

    image = Image.fromarray(rgb)
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=95, optimize=True)
    output.seek(0)

    return send_file(
        output,
        mimetype="image/jpeg",
        as_attachment=True,
        download_name="converted-image.jpg",
    )


if __name__ == "__main__":
    import os

    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")), debug=False)
