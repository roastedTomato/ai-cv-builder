"""Local resume document text extraction."""

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
import tempfile

from docx import Document


class ImportErrorBase(ValueError):
    code = "import_error"


class UnsupportedFormatError(ImportErrorBase):
    code = "unsupported_format"


class EmptyDocumentError(ImportErrorBase):
    code = "no_extractable_text"


class InvalidDocumentError(ImportErrorBase):
    code = "invalid_document"


@dataclass(frozen=True)
class ExtractedText:
    text: str
    format: str


def _pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages).strip()
    except Exception as exc:
        raise InvalidDocumentError("PDF 文件无法读取，请检查文件是否损坏。") from exc


def extract_text(path: Path) -> ExtractedText:
    """Extract from DOCX, legacy DOC (LibreOffice), or text-based PDF.

    The supplied filename is used only for suffix detection; content always
    remains in temporary/user-selected storage and is never copied to the repo.
    """
    suffix = path.suffix.lower()
    if suffix == ".docx":
        try:
            doc = Document(path)
            text = "\n".join(p.text for p in doc.paragraphs)
            text += "\n" + "\n".join("\t".join(c.text for c in row.cells) for table in doc.tables for row in table.rows)
        except Exception as exc:
            raise InvalidDocumentError("DOCX 文件无法读取，请检查文件是否损坏。") from exc
        fmt = "docx"
    elif suffix == ".pdf":
        text, fmt = _pdf_text(path), "pdf"
    elif suffix == ".doc":
        executable = shutil.which("soffice") or shutil.which("libreoffice")
        if not executable:
            raise UnsupportedFormatError("读取旧版 DOC 需要安装 LibreOffice。")
        with tempfile.TemporaryDirectory(prefix="cvbuilder-import-") as folder:
            try:
                subprocess.run([executable, "--headless", "--convert-to", "txt:Text", "--outdir", folder, str(path)], capture_output=True, timeout=60, check=True)
                converted = Path(folder) / f"{path.stem}.txt"
                text = converted.read_text(encoding="utf-8", errors="replace")
            except Exception as exc:
                raise InvalidDocumentError("DOC 文件无法转换，请检查文件或 LibreOffice 配置。") from exc
        fmt = "doc"
    else:
        raise UnsupportedFormatError("仅支持 .docx、.doc 和可提取文本的 .pdf 文件。")
    text = "\n".join(line.strip() for line in text.splitlines() if line.strip()).strip()
    if not text:
        if fmt == "pdf":
            raise EmptyDocumentError("PDF 未提取到文本，可能是扫描件；当前不支持 OCR，请改用文本型 PDF 或手动填写。")
        raise EmptyDocumentError("文件中未提取到可用文本，请手动填写简历。")
    return ExtractedText(text=text, format=fmt)

