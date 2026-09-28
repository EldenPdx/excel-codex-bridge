"""Codex's image tool, answered by the add-in's own image endpoints.

Codex's ``image_gen.imagegen`` tool posts a prompt to the provider's
``images/generations``, or the prompt and the pictures to change (as
``data:`` URLs) to ``images/edits``.  The add-in's generate_image tool does
the same on the signed-in session: a JSON prompt to
``.../basispoints/api/images/generations``, or a form with the picture to
``.../images/edits``, always asking gpt-image-2 for a PNG.  This turns Codex's
requests into the add-in's, within the choices the add-in allows.

Codex always asks for gpt-image-2 and does not tell the model which image
model drew a picture.  ``EXCEL_BRIDGE_IMAGE_MODEL`` (``--image-model``) names
another one for the bridge to ask the backend for instead; the bridge window
shows the model each picture was asked from.
"""

from __future__ import annotations

import os
import re

from . import excel_upstream
from .images import EXTENSIONS, decode_data_url

_BASE = excel_upstream.RESPONSES_URL.rsplit("/", 1)[0]
GENERATIONS_URL = _BASE + "/images/generations"
EDITS_URL = _BASE + "/images/edits"
# The add-in's, and the one Codex asks for.
MODEL = "gpt-image-2"
MODEL_ENV = "EXCEL_BRIDGE_IMAGE_MODEL"
_MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
SIZES = ("auto", "1024x1024", "1536x1024", "1024x1536", "1280x720")
QUALITIES = ("auto", "low", "medium", "high")
BACKGROUNDS = ("auto", "opaque")
MAX_PICTURES = 3


class Refused(ValueError):
    """A request the add-in would not make; the message goes back to Codex."""


def valid_model(name: str) -> bool:
    return bool(_MODEL_NAME.fullmatch(name))


def model() -> str:
    """The image model the bridge asks the backend for."""
    name = os.environ.get(MODEL_ENV, "").strip()
    if not name:
        return MODEL
    if not valid_model(name):
        raise Refused(f"{MODEL_ENV}={name!r} is not an image model name; fix it and restart the bridge.")
    return name


def _choice(request: dict, key: str, allowed: tuple[str, ...]) -> str:
    value = request.get(key)
    if value is None:
        return "auto"
    if value not in allowed:
        raise Refused(f"{key} {value!r} is not available through the Excel add-in; use one of: {', '.join(allowed)}.")
    return value


def _fields(request: dict) -> dict:
    prompt = request.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise Refused("prompt is required.")
    drawing = model()
    asked = request.get("model")
    # Codex always asks for gpt-image-2; the configured model stands in for it.
    if asked not in (None, MODEL, drawing):
        raise Refused(
            f"model {asked!r} is not available through the Excel bridge; it draws with {drawing} "
            f"(the user can choose another with --image-model or {MODEL_ENV})."
        )
    if request.get("background") == "transparent":
        raise Refused(
            "Transparent backgrounds are not available through the Excel add-in. "
            "Ask for the picture again without a transparent background."
        )
    if request.get("output_format") not in (None, "png"):
        raise Refused("The Excel add-in returns PNG pictures only.")
    fields = {
        "background": _choice(request, "background", BACKGROUNDS),
        "model": drawing,
        "output_format": "png",
        "prompt": prompt,
        "quality": _choice(request, "quality", QUALITIES),
        "size": _choice(request, "size", SIZES),
    }
    n = request.get("n")
    if n is not None:
        if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= MAX_PICTURES:
            raise Refused(f"n must be a whole number from 1 to {MAX_PICTURES}.")
        fields["n"] = n
    return fields


def generation_body(request: dict) -> dict:
    """The JSON the add-in posts to ``images/generations``."""
    return _fields(request)


def edit_form(request: dict) -> tuple[dict[str, str], list[tuple[str, tuple[str, bytes, str]]]]:
    """The form fields and files the add-in posts to ``images/edits``."""
    fields = _fields(request)
    pictures = request.get("images")
    if not isinstance(pictures, list) or not pictures:
        raise Refused("images is required: the pictures to change, as data: URLs.")
    # One picture goes as `image`, like the add-in's; several as `image[]`.
    name = "image" if len(pictures) == 1 else "image[]"
    files = []
    for index, picture in enumerate(pictures, 1):
        url = picture.get("image_url") if isinstance(picture, dict) else None
        decoded = decode_data_url(url) if isinstance(url, str) and url.startswith("data:") else None
        if decoded is None:
            raise Refused(f"Picture {index} is not a data: URL; the Excel add-in takes pictures inline only.")
        media_type, data = decoded
        files.append((name, (f"picture-{index}.{EXTENSIONS.get(media_type, 'png')}", data, media_type)))
    return {key: str(value) for key, value in fields.items()}, files
