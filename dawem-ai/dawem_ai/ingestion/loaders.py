"""Document loaders: PDF and plain-text notes -> a list of ``PageText``.

``page_number`` is the 1-based physical page of the PDF (blank pages are skipped but
still counted, so numbers match what the student sees in a PDF viewer). Notes have no
pages, so ``page_number`` is ``None``.

Scanned/image-only PDFs have no text layer; there is deliberately no OCR here, so they
raise ``EmptyDocumentError`` instead of silently indexing nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from dawem_ai.exceptions import (
    DocumentLoadError,
    EmptyDocumentError,
    UnsupportedFileTypeError,
)

try:  # Only required when a PDF is actually loaded.
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError
except ImportError:  # pragma: no cover
    PdfReader = None  # type: ignore[assignment,misc]
    PyPdfError = Exception  # type: ignore[assignment,misc]

__all__ = ["PageText", "load_document", "load_pdf", "load_text_file", "load_notes"]

SUPPORTED_EXTENSIONS = (".pdf", ".txt", ".md")

_SPACES_RE = re.compile(r"[ \t\u00a0]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


@dataclass(frozen=True, slots=True)
class PageText:
    page_number: int | None  # 1-based; None for non-paginated notes
    text: str


def _normalize(text: str) -> str:
    """Collapse runs of spaces and 3+ newlines; keep single paragraph breaks."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = _SPACES_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return _BLANK_LINES_RE.sub("\n\n", text).strip()


def load_pdf(path: str | Path) -> list[PageText]:
    if PdfReader is None:
        raise ImportError("pypdf is not installed. Run: pip install pypdf")
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            raise DocumentLoadError("PDF is password-protected")
        pages: list[PageText] = []
        for index, page in enumerate(reader.pages):
            text = _normalize(page.extract_text() or "")
            if text:
                pages.append(PageText(page_number=index + 1, text=text))
    except DocumentLoadError:
        raise
    except (PyPdfError, OSError, ValueError, KeyError) as exc:
        raise DocumentLoadError("PDF could not be read") from exc

    if not pages:
        raise EmptyDocumentError("PDF has no extractable text (scanned document?)")
    return pages


def load_notes(text: str) -> list[PageText]:
    """Notes pasted/typed in the app (already a string)."""
    cleaned = _normalize(text)
    if not cleaned:
        raise EmptyDocumentError("Notes are empty")
    return [PageText(page_number=None, text=cleaned)]


def load_text_file(path: str | Path) -> list[PageText]:
    try:
        raw = Path(path).read_bytes()
        text = raw.decode("utf-8-sig")  # tolerates a BOM from Windows editors
    except UnicodeDecodeError as exc:
        raise DocumentLoadError("Text file is not valid UTF-8") from exc
    except OSError as exc:
        raise DocumentLoadError("File could not be read") from exc
    return load_notes(text)


def load_document(path: str | Path) -> list[PageText]:
    """Dispatch on file extension."""
    suffix = Path(path).suffix.lower()
    if suffix == ".pdf":
        return load_pdf(path)
    if suffix in (".txt", ".md"):
        return load_text_file(path)
    raise UnsupportedFileTypeError(
        f"Unsupported file type {suffix or '(none)'!r}; supported: {', '.join(SUPPORTED_EXTENSIONS)}"
    )
