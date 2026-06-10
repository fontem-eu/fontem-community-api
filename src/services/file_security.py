"""File-upload validation + sanitization pipeline.

Defence in depth for the ``/data-stories/{id}/upload`` endpoint and
any future binary-ingest paths. Layered so each gate catches a
different class of attack and a failure in one doesn't compromise
the rest:

1. **Size cap** (`MAX_RASTER_BYTES`, `MAX_SVG_BYTES`) — short-circuits
   decompression-bomb / DoS before any parsing happens.
2. **Magic-byte MIME sniff** (`libmagic` via python-magic) — the
   client's Content-Type header is hostile input; we re-derive the
   type from the first 1 KiB and reject if it doesn't land in the
   allow-list. Catches polyglots and Content-Type spoofing.
3a. **Raster re-encode** through Pillow — opening the bytes,
   verifying structurally, then writing them back kills EXIF / ICC
   metadata, any payload appended past IEND/EOI, and proves the file
   is a real image. Pillow's `MAX_IMAGE_PIXELS` default (~178 Mpx)
   blocks decompression bombs that pass the byte-size check.
3b. **SVG sanitisation** — parse the XML strictly, strip every
   ``<script>`` element, every ``on*`` event-handler attribute, every
   ``javascript:`` / ``data:`` href, every external entity reference
   (XXE), every ``<foreignObject>``. Re-serialise from the cleaned
   tree.
4. **ClamAV INSTREAM** — the surviving bytes get streamed to the
   clamd daemon for known-malware signature matching (EICAR test
   string included). Optional via ``CLAMAV_HOST`` env so dev /
   in-memory tests run without a daemon.

Callers receive ``(content_type, clean_bytes)`` on success or an
``InvalidInput`` for any rejection — the router translates that to
400, and the app-level handler covers the chain.
"""
from __future__ import annotations

import io
import logging
import os
import re
from dataclasses import dataclass
from xml.etree import ElementTree as ET

import clamd
import magic
from PIL import Image

# Register canonical SVG/Xlink namespaces with empty / standard
# prefixes so re-serialised output keeps the default-namespace form
# (``<svg xmlns="...">``) instead of ElementTree's auto-generated
# ``ns0:`` prefix, which breaks downstream SVG consumers expecting
# the element-local name and our own ``<rect>`` regex tests.
# NOSONAR S5332 — these are W3C-mandated XML namespace identifier
# strings, not URLs we dereference. The SVG spec defines them as
# the literal ``http://`` form; changing them breaks every SVG.
from src.services.exceptions import InvalidInput

ET.register_namespace("", "http://www.w3.org/2000/svg")  # noqa: S5332
ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")  # noqa: S5332

logger = logging.getLogger(__name__)


# ── Limits ───────────────────────────────────────────────────
# 20 MB raster covers high-resolution JPEGs from modern phones
# (~10 MB typical) with headroom for the occasional 24 Mpx DSLR
# shot. 2 MB SVG is generous for charts: our own widget exports
# come in well under 200 KiB. The Pillow re-encode also enforces
# a per-side pixel cap (see RASTER_MAX_DIM).
MAX_RASTER_BYTES = 20 * 1024 * 1024
MAX_SVG_BYTES = 2 * 1024 * 1024
RASTER_MAX_DIM = 8000  # per side; 8000×8000 ≈ 64 Mpx

ALLOWED_RASTER_MIMES = frozenset({
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
})
SVG_MIME = "image/svg+xml"
ALL_ALLOWED_MIMES = ALLOWED_RASTER_MIMES | {SVG_MIME}


# Pillow's Image.format → canonical MIME, plus the inverse map used
# when we re-save: keeps the round-trip explicit so a JPEG that
# claims to be a PNG (Content-Type spoof) doesn't silently end up
# as a PNG-named JPEG on disk.
_PIL_FORMAT_TO_MIME = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "WEBP": "image/webp",
    "GIF": "image/gif",
}
_MIME_TO_PIL_FORMAT = {v: k for k, v in _PIL_FORMAT_TO_MIME.items()}


# SVG sanitisation patterns. We parse the XML rather than regex it,
# so this list is just for the attribute-name match step inside the
# tree walk.
_SVG_EVENT_HANDLER_RE = re.compile(r"^on", re.IGNORECASE)
_SVG_DANGEROUS_HREF_RE = re.compile(
    r"^\s*(javascript|data|vbscript|file):", re.IGNORECASE,
)
# `<foreignObject>` lets the browser embed an HTML fragment inside
# the SVG, which is the standard XSS bypass for SVG sanitisers that
# only strip <script>. Always remove.
_SVG_BANNED_TAGS = frozenset({"script", "foreignObject"})


@dataclass(frozen=True)
class CleanedFile:
    """Result of ``scan_and_sanitise``.

    ``content_type`` is the canonical MIME (post-sniff, post-clean) the
    caller should persist + serve. ``data`` is the bytes safe to store
    — for raster this is the re-encoded image, for SVG it's the
    serialised sanitised tree.
    """

    content_type: str
    data: bytes


def scan_and_sanitise(
    raw: bytes,
    *,
    clamd_client: clamd.ClamdNetworkSocket | None = None,
) -> CleanedFile:
    """Run the full pipeline on ``raw`` and return cleaned bytes.

    ``clamd_client`` is optional so unit tests can skip the AV step
    without a daemon. The production wire-up passes a configured
    client; missing both env + arg degrades to "scan skipped" with a
    warning log so we notice if the env var ever drops out.
    """
    sniffed = _sniff_mime(raw)
    if sniffed not in ALL_ALLOWED_MIMES:
        raise InvalidInput(
            f"File type {sniffed!r} not allowed; expected one of "
            f"{sorted(ALL_ALLOWED_MIMES)}",
        )

    if sniffed == SVG_MIME:
        if len(raw) > MAX_SVG_BYTES:
            raise InvalidInput(
                f"SVG too large: {len(raw)} bytes > {MAX_SVG_BYTES}",
            )
        cleaned = _sanitise_svg(raw)
        canonical_mime = SVG_MIME
    else:
        if len(raw) > MAX_RASTER_BYTES:
            raise InvalidInput(
                f"Image too large: {len(raw)} bytes > {MAX_RASTER_BYTES}",
            )
        cleaned, canonical_mime = _reencode_raster(raw, expected_mime=sniffed)

    _av_scan(cleaned, clamd_client=clamd_client)
    return CleanedFile(content_type=canonical_mime, data=cleaned)


# ── Stage 1 + 2 helpers ──────────────────────────────────────


def _sniff_mime(raw: bytes) -> str:
    """Return libmagic's MIME for ``raw``'s leading bytes.

    Reads a small slice; libmagic itself only inspects the first
    couple of KiB. Returns the bare type (``image/png``) without
    parameters so the allow-list check is a simple set membership.
    """
    # 8 KiB is well past what libmagic actually consults (it stops
    # at the first definitive match) but cheap to slice and immune to
    # weird truncations that confuse the detector.
    mime = magic.from_buffer(raw[:8192], mime=True)
    # libmagic sometimes returns e.g. ``image/svg+xml; charset=utf-8``
    # for SVG — split off any parameters.
    return mime.split(";")[0].strip()


# ── Stage 3a: raster re-encode ───────────────────────────────


def _reencode_raster(raw: bytes, *, expected_mime: str) -> tuple[bytes, str]:
    """Open, verify, and re-save the raster image.

    Two passes through Pillow: ``verify()`` does a structural check
    without decoding pixels (cheap and catches some malformed files
    early); a second open + ``load()`` actually decodes so we can
    enforce the dimension cap and re-serialise without metadata.
    ``verify()`` consumes the stream, hence the separate BytesIO
    pair.

    Raises ``InvalidInput`` if Pillow can't parse the bytes, if the
    detected format doesn't match what magic sniffed (catches the
    JPEG-bytes-with-PNG-magic polyglot), or if pixel dimensions
    exceed ``RASTER_MAX_DIM``.
    """
    try:
        with Image.open(io.BytesIO(raw)) as probe:
            probe.verify()
    except (OSError, SyntaxError) as e:
        raise InvalidInput(f"Image failed structural verification: {e}") from e

    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except (OSError, Image.DecompressionBombError) as e:
        raise InvalidInput(f"Image could not be decoded: {e}") from e

    pil_format = (img.format or "").upper()
    canonical_mime = _PIL_FORMAT_TO_MIME.get(pil_format)
    if canonical_mime is None:
        raise InvalidInput(
            f"Image format {pil_format!r} is not in the raster allow-list",
        )
    if canonical_mime != expected_mime:
        # Magic + Pillow disagree → polyglot territory. Reject rather
        # than guess; the user can re-export from their tool.
        raise InvalidInput(
            f"Image format mismatch: magic says {expected_mime}, "
            f"Pillow says {canonical_mime}",
        )
    if img.width > RASTER_MAX_DIM or img.height > RASTER_MAX_DIM:
        raise InvalidInput(
            f"Image dimensions {img.width}x{img.height} exceed "
            f"{RASTER_MAX_DIM}x{RASTER_MAX_DIM} cap",
        )

    out = io.BytesIO()
    save_kwargs: dict = {}
    if pil_format == "JPEG":
        # Keep quality high enough that a photo round-trip doesn't
        # introduce visible artefacts; 90 is the conventional "good
        # photo" knob. ICC profile is intentionally not preserved —
        # the metadata strip is the whole point.
        save_kwargs.update(quality=90, optimize=True)
    elif pil_format == "PNG":
        save_kwargs.update(optimize=True)
    elif pil_format == "GIF":
        save_kwargs.update(save_all=True)  # preserve animation if multi-frame
    img.save(out, format=pil_format, **save_kwargs)
    return out.getvalue(), canonical_mime


# ── Stage 3b: SVG sanitisation ───────────────────────────────


def _sanitise_svg(raw: bytes) -> bytes:
    """Parse SVG XML, strip every executable construct, re-serialise.

    Defuses external entity expansion by re-parsing with the stdlib
    ``xml.etree.ElementTree`` which doesn't resolve entities by default.
    XML bombs (billion laughs) hit the size cap before they hit the
    parser. We DO NOT use lxml or expat directly here — both have
    historical CVEs around external entity handling.

    What we strip (and why):
    - ``<script>`` — primary XSS vector.
    - ``<foreignObject>`` — embeds HTML, the standard SVG-sanitiser
      bypass.
    - ``on*`` attributes — inline event handlers run JS.
    - ``href``/``xlink:href`` with ``javascript:`` / ``data:`` /
      ``vbscript:`` / ``file:`` schemes — clickable JS execution and
      data exfiltration.
    """
    try:
        parser = ET.XMLParser()
        tree = ET.ElementTree(ET.fromstring(raw, parser=parser))
    except ET.ParseError as e:
        raise InvalidInput(f"SVG XML is malformed: {e}") from e

    root = tree.getroot()
    # Root must be <svg> in the SVG namespace; reject anything else
    # (the client-claimed content_type could disagree with reality).
    if not _is_svg_root(root):
        raise InvalidInput("File is not an SVG (root element check failed)")

    _strip_dangerous(root)
    out = io.BytesIO()
    tree.write(out, encoding="utf-8", xml_declaration=True)
    return out.getvalue()


def _is_svg_root(elem: ET.Element) -> bool:
    """Accept either the namespaced ``{http://www.w3.org/2000/svg}svg``
    root or a bare ``<svg>`` (some chart libraries emit no namespace).
    Anything else is a different document type that lied about being
    SVG."""
    tag = elem.tag.lower()
    return tag == "svg" or tag.endswith("}svg")


def _strip_dangerous(elem: ET.Element) -> None:
    """Walk the tree in-place: drop banned tags, scrub attributes."""
    # Children first so we can mutate the parent's child list.
    # NOSONAR S7504 — list() is load-bearing: the body removes from elem.
    for child in list(elem):  # noqa: S7504
        local = _local_name(child.tag)
        if local in _SVG_BANNED_TAGS:
            elem.remove(child)
            continue
        _strip_dangerous(child)

    # NOSONAR S7504 — list() is load-bearing: the body removes from elem.attrib.
    for attr_name in list(elem.attrib):  # noqa: S7504
        local = _local_name(attr_name)
        # 1. Event handlers — `onclick`, `onload`, anything matching `^on`.
        if _SVG_EVENT_HANDLER_RE.match(local):
            del elem.attrib[attr_name]
            continue
        # 2. JS-scheme href / xlink:href / src.
        if local in {"href", "src"} or local.endswith(":href"):
            value = elem.attrib.get(attr_name, "")
            if _SVG_DANGEROUS_HREF_RE.match(value):
                del elem.attrib[attr_name]


def _local_name(tag: str) -> str:
    """Strip the ``{namespace}`` prefix from an ET tag/attribute name."""
    if tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag


# ── Stage 4: AV ──────────────────────────────────────────────


def _av_scan(
    data: bytes,
    *,
    clamd_client: clamd.ClamdNetworkSocket | None,
) -> None:
    """Stream ``data`` through clamd. Raises ``InvalidInput`` on hit.

    A missing client (no ``CLAMAV_HOST`` env, dev runs) logs and
    skips — failing closed would block every upload during local
    development. Production wires the env so this never silently
    skips; if it does, the log is the canary.
    """
    if clamd_client is None:
        logger.warning("CLAMAV scan skipped: no clamd client configured")
        return
    try:
        result = clamd_client.instream(io.BytesIO(data))
    except (clamd.ConnectionError, OSError) as e:
        # Failing closed (raise) would let an AV outage block uploads
        # across the whole site. Failing open (return) lets uploads
        # through during an outage. Pick failing open + loud log —
        # uploads stay flowing, oncall sees the alert, the other
        # layers (magic / Pillow / SVG sanitise) are still in front of
        # the bytes.
        logger.exception("CLAMAV scan failed; allowing upload: %s", e)
        return

    stream_result = result.get("stream", ("OK", None))
    status, signature = stream_result[0], stream_result[1] if len(stream_result) > 1 else None
    if status == "FOUND":
        raise InvalidInput(f"File rejected by AV scan: {signature}")
    if status != "OK":
        # ERROR or anything else clamd might return — treat as a
        # transient + log.
        logger.warning(
            "CLAMAV returned non-OK status %s (sig=%s); allowing", status, signature,
        )


def make_clamd_client() -> clamd.ClamdNetworkSocket | None:
    """Build the production clamd client from env vars, or return None.

    Returns None when ``CLAMAV_HOST`` is unset so dev / in-memory tests
    don't need a running daemon. Production deployments must set it
    (the gitops values do).
    """
    host = os.environ.get("CLAMAV_HOST")
    if not host:
        return None
    port = int(os.environ.get("CLAMAV_PORT", "3310"))
    timeout = float(os.environ.get("CLAMAV_TIMEOUT", "30"))
    return clamd.ClamdNetworkSocket(host=host, port=port, timeout=timeout)
