"""
CV Upload Manager — handles uploaded CV/resume files in any format.

Supports: PDF, DOCX, DOC, TXT, MD, RTF, HTML, ODT
- Stores files in data/cvs/
- Extracts text content for AI generation context
- Stores metadata in the uploaded_cvs table
"""

import os
import sqlite3
import base64
import hashlib
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "data" / "jobagent.db"
CV_DIR = BASE_DIR / "data" / "cvs"
CV_DIR.mkdir(parents=True, exist_ok=True)

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB

ALLOWED_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".txt", ".md", ".rtf",
    ".html", ".htm", ".odt"
}


def init_cv_table():
    """Create the uploaded_cvs table if it doesn't exist."""
    db = sqlite3.connect(str(DB_PATH))
    db.execute("""
        CREATE TABLE IF NOT EXISTS uploaded_cvs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            original_filename TEXT NOT NULL,
            file_path TEXT NOT NULL,
            file_size INTEGER,
            file_type TEXT,
            text_content TEXT,
            uploaded_at TEXT NOT NULL,
            is_primary INTEGER DEFAULT 0
        )
    """)
    db.commit()
    db.close()


def _extract_text_pdf(file_path: str) -> str:
    """Extract text from PDF."""
    try:
        import pdfplumber
        text_parts = []
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    text_parts.append(t)
        return "\n".join(text_parts)
    except Exception:
        pass
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(file_path)
        text_parts = []
        for page in reader.pages:
            t = page.extract_text()
            if t:
                text_parts.append(t)
        return "\n".join(text_parts)
    except Exception:
        return ""


def _extract_text_docx(file_path: str) -> str:
    """Extract text from DOCX."""
    try:
        from docx import Document
        doc = Document(file_path)
        return "\n".join(para.text for para in doc.paragraphs if para.text.strip())
    except Exception:
        return ""


def _extract_text_txt(file_path: str) -> str:
    """Extract text from plain text files."""
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception:
        try:
            with open(file_path, "r", encoding="latin-1") as f:
                return f.read()
        except Exception:
            return ""


def _extract_text_html(file_path: str) -> str:
    """Extract text from HTML files."""
    try:
        import re
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            html = f.read()
        # Remove scripts and styles
        html = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', html, flags=re.DOTALL | re.IGNORECASE)
        # Remove tags
        text = re.sub(r'<[^>]+>', ' ', html)
        # Clean whitespace
        text = re.sub(r'\s+', ' ', text).strip()
        return text
    except Exception:
        return ""


def _extract_text_rtf(file_path: str) -> str:
    """Extract text from RTF files (basic)."""
    try:
        import re
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            rtf = f.read()
        # Remove RTF control words
        text = re.sub(r'\\[a-zA-Z]+-?\d*\s?', '', rtf)
        # Remove remaining RTF syntax
        text = re.sub(r'[{}\\]', '', text)
        # Clean whitespace
        text = re.sub(r'\s+', ' ', text).strip()
        return text
    except Exception:
        return ""


def extract_text(file_path: str, filename: str = "") -> str:
    """Extract text content from a file based on its extension."""
    ext = Path(filename or file_path).suffix.lower()

    if ext == ".pdf":
        return _extract_text_pdf(file_path)
    elif ext in (".docx", ".doc"):
        return _extract_text_docx(file_path)
    elif ext in (".txt", ".md"):
        return _extract_text_txt(file_path)
    elif ext in (".html", ".htm"):
        return _extract_text_html(file_path)
    elif ext == ".rtf":
        return _extract_text_rtf(file_path)
    elif ext == ".odt":
        # ODT is a zip, try to extract
        try:
            import zipfile, re
            with zipfile.ZipFile(file_path) as z:
                with z.open("content.xml") as f:
                    xml = f.read().decode("utf-8", errors="replace")
                text = re.sub(r'<[^>]+>', ' ', xml)
                text = re.sub(r'\s+', ' ', text).strip()
                return text
        except Exception:
            return ""
    else:
        return _extract_text_txt(file_path)


def save_uploaded_cv(filename: str, content: bytes) -> dict:
    """
    Save an uploaded CV file.
    
    Args:
        filename: Original filename
        content: File content as bytes
    
    Returns:
        dict with success status and CV info
    """
    init_cv_table()

    # Validate
    if not filename:
        return {"error": "No filename provided"}

    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        return {"error": f"Unsupported format: {ext}. Supported: {', '.join(sorted(ALLOWED_EXTENSIONS))}"}

    if len(content) > MAX_FILE_SIZE:
        return {"error": f"File too large. Max size: {MAX_FILE_SIZE // (1024*1024)} MB"}

    # Generate safe filename
    safe_name = hashlib.md5(filename.encode()).hexdigest()[:8] + "_" + filename.replace(" ", "_")
    file_path = CV_DIR / safe_name

    # Write file
    with open(file_path, "wb") as f:
        f.write(content)

    # Extract text
    text_content = extract_text(str(file_path), filename)
    if not text_content:
        text_content = "(Text extraction failed — file stored for download)"

    # Truncate text for storage (keep first 10K chars)
    if len(text_content) > 10000:
        text_content = text_content[:10000] + "\n... [truncated]"

    # Determine file type label
    type_labels = {
        ".pdf": "PDF", ".docx": "Word (DOCX)", ".doc": "Word (DOC)",
        ".txt": "Text", ".md": "Markdown", ".rtf": "Rich Text",
        ".html": "HTML", ".htm": "HTML", ".odt": "OpenDocument",
    }
    file_type = type_labels.get(ext, ext.upper())

    # Store in DB
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row

    # Check if this is the first CV — make it primary
    count = db.execute("SELECT COUNT(*) FROM uploaded_cvs").fetchone()[0]
    is_primary = 1 if count == 0 else 0

    cursor = db.execute(
        """INSERT INTO uploaded_cvs 
           (filename, original_filename, file_path, file_size, file_type, text_content, uploaded_at, is_primary)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (safe_name, filename, str(file_path), len(content), file_type, text_content,
         datetime.now().isoformat(), is_primary)
    )
    cv_id = cursor.lastrowid
    db.commit()
    db.close()

    return {
        "success": True,
        "id": cv_id,
        "filename": filename,
        "file_type": file_type,
        "file_size": len(content),
        "is_primary": bool(is_primary),
        "text_length": len(text_content),
    }


def get_uploaded_cvs() -> list:
    """Get all uploaded CVs."""
    init_cv_table()
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    rows = db.execute("SELECT * FROM uploaded_cvs ORDER BY is_primary DESC, uploaded_at DESC").fetchall()
    db.close()
    return [dict(r) for r in rows]


def get_cv(cv_id: int) -> dict:
    """Get a single CV by ID."""
    init_cv_table()
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT * FROM uploaded_cvs WHERE id = ?", (cv_id,)).fetchone()
    db.close()
    return dict(row) if row else None


def get_primary_cv() -> dict:
    """Get the primary CV (for use in applications)."""
    init_cv_table()
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT * FROM uploaded_cvs WHERE is_primary = 1 ORDER BY uploaded_at DESC LIMIT 1").fetchone()
    if not row:
        row = db.execute("SELECT * FROM uploaded_cvs ORDER BY uploaded_at DESC LIMIT 1").fetchone()
    db.close()
    return dict(row) if row else None


def set_primary_cv(cv_id: int) -> bool:
    """Set a CV as the primary one."""
    init_cv_table()
    db = sqlite3.connect(str(DB_PATH))
    db.execute("UPDATE uploaded_cvs SET is_primary = 0")
    cursor = db.execute("UPDATE uploaded_cvs SET is_primary = 1 WHERE id = ?", (cv_id,))
    db.commit()
    success = cursor.rowcount > 0
    db.close()
    return success


def delete_cv(cv_id: int) -> bool:
    """Delete a CV."""
    init_cv_table()
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT file_path FROM uploaded_cvs WHERE id = ?", (cv_id,)).fetchone()
    if not row:
        db.close()
        return False
    # Delete file
    try:
        Path(row["file_path"]).unlink(missing_ok=True)
    except Exception:
        pass
    db.execute("DELETE FROM uploaded_cvs WHERE id = ?", (cv_id,))
    db.commit()
    db.close()
    return True


def get_cv_download(cv_id: int) -> tuple:
    """Get CV file for download. Returns (file_path, original_filename, content_bytes) or None."""
    cv = get_cv(cv_id)
    if not cv:
        return None
    file_path = cv["file_path"]
    if not Path(file_path).exists():
        return None
    with open(file_path, "rb") as f:
        content = f.read()
    return (file_path, cv["original_filename"], content, cv)
