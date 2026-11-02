"""Source code metadata and header extractor."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Dict, Tuple

from neurosort.extractors.generic import read_text_sample

EXTENSION_LANGUAGE_MAP = {
    ".py": "Python",
    ".pyw": "Python",
    ".js": "JavaScript",
    ".mjs": "JavaScript",
    ".jsx": "JavaScript (React)",
    ".ts": "TypeScript",
    ".tsx": "TypeScript (React)",
    ".rs": "Rust",
    ".go": "Go",
    ".java": "Java",
    ".c": "C",
    ".cpp": "C++",
    ".h": "C/C++ Header",
    ".hpp": "C++ Header",
    ".cs": "C#",
    ".rb": "Ruby",
    ".php": "PHP",
    ".sh": "Shell Script",
    ".bash": "Bash Script",
    ".zsh": "Zsh Script",
    ".sql": "SQL",
    ".html": "HTML",
    ".htm": "HTML",
    ".css": "CSS",
    ".scss": "SCSS",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".toml": "TOML",
    ".lua": "Lua",
    ".kt": "Kotlin",
    ".swift": "Swift",
    ".dart": "Dart",
}


def extract_code_data(file_path: Path) -> Tuple[str, Dict[str, Any]]:
    """Extract language, imports, module declarations, and docstrings from source code.

    Args:
        file_path: Path to code file.

    Returns:
        Tuple of (text_summary, metadata_dict).
    """
    ext = file_path.suffix.lower()
    language = EXTENSION_LANGUAGE_MAP.get(ext, "Source Code")

    sample = read_text_sample(file_path, max_bytes=8192)
    lines = sample.splitlines()

    metadata: Dict[str, Any] = {
        "format": "CODE",
        "language": language,
        "line_count_approx": len(lines),
    }

    # Detect shebang if generic
    if lines and lines[0].startswith("#!"):
        shebang = lines[0]
        metadata["shebang"] = shebang
        if "python" in shebang:
            language = "Python"
        elif "node" in shebang:
            language = "JavaScript"
        elif "bash" in shebang:
            language = "Bash Script"
        elif "sh" in shebang:
            language = "Shell Script"
        metadata["language"] = language

    # Extract top docstring / comment block
    docstring_chunks = []
    in_block_comment = False

    for line in lines[:30]:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            docstring_chunks.append(stripped.lstrip("#").strip())
        elif stripped.startswith("//"):
            docstring_chunks.append(stripped.lstrip("/").strip())
        elif '"""' in stripped or "'''" in stripped:
            docstring_chunks.append(stripped.replace('"""', "").replace("'''", "").strip())
        elif stripped.startswith("/*") or "*/" in stripped:
            docstring_chunks.append(stripped.replace("/*", "").replace("*/", "").strip())

    # Detect package / module / namespace
    package_name = None
    for line in lines[:40]:
        m_go = re.match(r"^package\s+([a-zA-Z0-9_]+)", line)
        if m_go:
            package_name = m_go.group(1)
            break
        m_java = re.match(r"^package\s+([a-zA-Z0-9_\.]+);", line)
        if m_java:
            package_name = m_java.group(1)
            break
        m_ns = re.match(r"^namespace\s+([a-zA-Z0-9_\.]+)", line)
        if m_ns:
            package_name = m_ns.group(1)
            break

    if package_name:
        metadata["package"] = package_name

    summary_parts = [f"Language: {language}"]
    if package_name:
        summary_parts.append(f"Package: {package_name}")
    if docstring_chunks:
        summary_parts.append(f"Docstring: {' '.join(docstring_chunks[:4])}")
    else:
        # Give sample lines
        preview = " ".join([l.strip() for l in lines[:5] if l.strip()])
        summary_parts.append(f"Header: {preview[:200]}")

    return " | ".join(summary_parts), metadata
