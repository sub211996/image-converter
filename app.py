from __future__ import annotations

import io
import os
import tempfile
import zipfile
from pathlib import Path

from flask import Flask, Response, render_template_string, request, send_file
from PIL import Image, ImageOps
from werkzeug.utils import secure_filename

try:
    from pillow_heif import register_heif_opener
except ImportError:  # pragma: no cover
    register_heif_opener = None
else:  # pragma: no cover
    register_heif_opener()

try:
    import rawpy
except ImportError:  # pragma: no cover
    rawpy = None


BASE_DIR = Path(__file__).resolve().parent
HTML_PATH = BASE_DIR / "index.html"

app = Flask(__name__)

RAW_EXTENSIONS = {
    ".cr2",
    ".cr3",
    ".rw2",
    ".nef",
    ".arw",
    ".sr2",
    ".orf",
    ".pef",
    ".raf",
    ".raw",
    ".dng",
    ".x3f",
    ".kdc",
    ".mos",
    ".mrw",
    ".srw",
}

IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".bmp",
    ".webp",
    ".tif",
    ".tiff",
    ".heic",
    ".heif",
    ".avif",
    ".ico",
    ".jp2",
    ".j2k",
    ".jpf",
    ".jpx",
    ".ppm",
    ".pgm",
    ".pbm",
    ".pnm",
}

ALLOWED_EXTENSIONS = RAW_EXTENSIONS | IMAGE_EXTENSIONS

OUTPUT_FORMATS = {
    "jpg": {"extension": "jpg", "mime": "image/jpeg", "pil_format": "JPEG"},
    "png": {"extension": "png", "mime": "image/png", "pil_format": "PNG"},
    "webp": {"extension": "webp", "mime": "image/webp", "pil_format": "WEBP"},
}


def _extension(filename: str) -> str:
    return Path(filename.lower()).suffix


def _is_allowed_file(filename: str) -> bool:
    return _extension(filename) in ALLOWED_EXTENSIONS


def _is_raw_file(filename: str) -> bool:
    return _extension(filename) in RAW_EXTENSIONS


def _load_rgb_from_standard_image(file_bytes: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(file_bytes))
    return ImageOps.exif_transpose(image)


def _load_rgb_from_raw(file_bytes: bytes, suffix: str) -> Image.Image:
    if rawpy is None:
        raise RuntimeError("RAW conversion support is not installed.")

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as temp_file:
        temp_file.write(file_bytes)
        temp_file.flush()

        with rawpy.imread(temp_file.name) as raw:
            rgb = raw.postprocess(
                output_bps=8,
                no_auto_bright=True,
                use_camera_wb=True,
            )

    return Image.fromarray(rgb)


def _prepare_image_for_output(image: Image.Image, output_format: str) -> Image.Image:
    if output_format == "jpg":
        if image.mode in {"RGBA", "LA", "P"}:
            base = Image.new("RGB", image.size, "white")
            if image.mode == "P":
                image = image.convert("RGBA")
            alpha = image.getchannel("A") if "A" in image.getbands() else None
            if alpha is not None:
                base.paste(image.convert("RGBA"), mask=alpha)
                return base
        return image.convert("RGB")

    if image.mode == "P":
        return image.convert("RGBA")

    if image.mode in {"LA", "RGBA"}:
        return image.convert("RGBA")

    if image.mode not in {"RGB", "RGBA"}:
        return image.convert("RGB")

    return image


def _convert_upload(uploaded_file, output_format: str) -> tuple[str, bytes]:
    filename = secure_filename(uploaded_file.filename or "image")
    suffix = _extension(filename)
    file_bytes = uploaded_file.read()

    if _is_raw_file(filename):
        image = _load_rgb_from_raw(file_bytes, suffix)
    else:
        image = _load_rgb_from_standard_image(file_bytes)

    output_format_info = OUTPUT_FORMATS[output_format]
    image = _prepare_image_for_output(image, output_format)
    output = io.BytesIO()
    save_kwargs = {}

    if output_format == "jpg":
        save_kwargs = {"quality": 95, "optimize": True, "progressive": True}
    elif output_format == "webp":
        save_kwargs = {"quality": 95, "method": 6}

    image.save(output, format=output_format_info["pil_format"], **save_kwargs)
    return filename, output.getvalue()


@app.get("/")
def home() -> str:
    return render_template_string(HTML_PATH.read_text(encoding="utf-8"))


@app.get("/health")
def health() -> tuple[str, int]:
    return "ok", 200


@app.post("/convert")
def convert_files() -> Response:
    uploaded_files = request.files.getlist("files")
    if not uploaded_files:
        single = request.files.get("file")
        uploaded_files = [single] if single and single.filename else []

    if not uploaded_files:
        return Response("No files uploaded.", status=400)

    valid_files = [item for item in uploaded_files if item and item.filename]
    if not valid_files:
        return Response("No valid files uploaded.", status=400)

    output_format = request.form.get("format", "jpg").lower().strip()
    if output_format not in OUTPUT_FORMATS:
        return Response("Unsupported output format.", status=400)

    converted: list[tuple[str, bytes]] = []
    for uploaded in valid_files:
        if not _is_allowed_file(uploaded.filename):
            return Response(
                "Unsupported file type. Please upload a supported image or RAW file.",
                status=400,
            )

        try:
            converted.append(_convert_upload(uploaded, output_format))
        except Exception as exc:  # pragma: no cover
            return Response(f"Could not convert {uploaded.filename}: {exc}", status=400)

    if len(converted) == 1:
        original_name, converted_bytes = converted[0]
        output = io.BytesIO(converted_bytes)
        jpg_name = Path(original_name).stem or "converted-image"
        extension = OUTPUT_FORMATS[output_format]["extension"]
        return send_file(
            output,
            mimetype=OUTPUT_FORMATS[output_format]["mime"],
            as_attachment=True,
            download_name=f"{jpg_name}.{extension}",
        )

    archive = io.BytesIO()
    with zipfile.ZipFile(archive, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        extension = OUTPUT_FORMATS[output_format]["extension"]
        for original_name, converted_bytes in converted:
            file_name = f"{Path(original_name).stem or 'image'}.{extension}"
            zf.writestr(file_name, converted_bytes)

    archive.seek(0)
    return send_file(
        archive,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"converted-images-{output_format}.zip",
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")), debug=False)
