# NeuroSort 🧠📁

> **Modern, local-first, safe, and intelligent automatic file management daemon and CLI.**

NeuroSort continuously monitors your intake directories (such as `~/Downloads` or `~/Desktop/Inbox`), extracts content metadata from documents, images, and source code, classifies files semantically using local LLMs (via Ollama or OpenAI-compatible APIs) or rules, standardizes filenames with customizable naming templates, and moves files to organized target folders with **zero data-loss risk** and **instant rollback** via an audit-grade SQLite transaction ledger.

---

## ⚡ Key Features

- **🛡️ Zero-Data-Loss Transaction Engine**: Atomic file operations with SHA-256 integrity verification, automatic conflict handling (`_v1`, `_v2`), and instant `neurosort undo`.
- **⏳ Debounced Ingestion Daemon**: Multi-stage stabilization check ensures files are completely written and idle before processing (rejects partial downloads like `.crdownload` or `.tmp`).
- **🔍 Deep Content Extraction**:
  - **Documents**: `pymupdf` (fitz) text extraction for PDFs, DOCX, TXT, MD, CSV with OCR fallback.
  - **Images**: `Pillow` + `piexif` metadata extraction (EXIF, dates, GPS coordinates, dimensions).
  - **Code**: Language detection, package/module imports, top-level comments, and docstrings.
  - **MIME**: `python-magic` combined with byte signature analysis.
- **🤖 Local-First Intelligence**:
  - Native integration with **Ollama** (`llama3.2`, `phi3.5`, `qwen2.5`, etc.) with strict JSON schema validation.
  - Fallback to OpenAI-compatible endpoints or LiteLLM.
  - Offline heuristic rule-based classification fallback when offline.
- **📊 Rich CLI & TUI**: Beautiful interactive output powered by `typer` and `rich`, featuring live progress bars, dry-run simulation tables, and transaction audit histories.
- **🌐 Cloud-Ready Web Dashboard**: Visual interactive interface deployed to Vercel for live rule testing, template previews, and transaction ledger inspection.

---

## 🚀 Quick Start

### 1. Installation

```bash
# Clone the repository
git clone https://github.com/ElXprogramming/neurosort.git
cd filesortingsystem

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install package with dependencies
pip install -e .
```

### 2. Initialize Configuration

```bash
neurosort init
```

This creates a default `config.yaml` and default target directory hierarchy (`~/Documents/Organized/`).

### 3. Dry-Run Scan

Preview how NeuroSort would organize a directory without touching any files:

```bash
neurosort scan ~/Downloads --dry-run
```

### 4. Execute Organization

Organize files directly:

```bash
neurosort scan ~/Downloads
```

### 5. Start Background Watcher Daemon

Watch intake folders automatically:

```bash
neurosort start
```

### 6. Instant Rollback

Undo previous file movements safely:

```bash
# Undo the last operation
neurosort undo

# Undo the last 5 operations
neurosort undo --last 5

# Undo a specific transaction by ID
neurosort undo --id 42
```

### 7. Inspect History

```bash
neurosort history --limit 20
```

---

## ⚙️ Configuration (`config.yaml`)

```yaml
watch_paths:
  - path: "~/Downloads"
    debounce_seconds: 5.0
  - path: "~/Desktop/Inbox"
    debounce_seconds: 5.0

destination_root: "~/Documents/Organized"

rules_precedence:
  exact_overrides:
    "*.iso": "Archives/ISO"
    "*.dmg": "Installers/macOS"
    "*.deb": "Installers/Linux"
    "*.rpm": "Installers/Linux"
    "*.torrent": "Downloads/Torrents"

  taxonomy_clusters:
    "Finance/Invoices":
      - "invoice"
      - "receipt"
      - "billing"
      - "tax"
      - "statement"
      - "w2"
      - "1099"
    "Health/Medical":
      - "prescription"
      - "medical"
      - "lab_result"
      - "vaccine"
      - "doctor"
      - "blood_test"
    "Dev/Datasets":
      - "dataset"
      - "csv"
      - "jsonl"
      - "parquet"
      - "benchmark"
    "Dev/SourceCode":
      - "script"
      - "source"
      - "module"
      - "python"
      - "rust"
      - "javascript"
    "Media/Wallpapers":
      - "wallpaper"
      - "background"
      - "4k"
      - "desktop"
    "Media/Photos":
      - "dcim"
      - "camera"
      - "photo"
      - "portrait"
    "Documents/Work":
      - "report"
      - "contract"
      - "nda"
      - "proposal"
      - "resume"

renaming_template: "{date}_{category}_{entity}_{original_title}.{ext}"

ai_provider:
  provider: "ollama"
  model: "llama3.2"
  temperature: 0.0
  base_url: "http://localhost:11434"
  fallback_to_rules: true

dry_run: false
ignore_patterns:
  - "*.crdownload"
  - "*.tmp"
  - "~*"
  - ".*"
  - "*.part"
  - "*.swp"
```

---

## 🏛️ Architecture

```
┌─────────────────┐      ┌───────────────────────────┐      ┌─────────────────────────┐
│ Watchdog Daemon │ ───> │ Multi-Stage Stabilization │ ───> │ Content Extractor       │
│ (Debounced FS)  │      │ (Size Delta / Non-Zero)   │      │ (PDF, EXIF, Code, MIME) │
└─────────────────┘      └───────────────────────────┘      └────────────┬────────────┘
                                                                         │
                                                                         ▼
┌─────────────────┐      ┌───────────────────────────┐      ┌─────────────────────────┐
│ SQLite Ledger   │ <─── │ Transaction Safety Runner │ <─── │ Semantic Classifier     │
│ (Audit / Undo)  │      │ (Collision & Atomic Move) │      │ (Ollama / OpenAI Rules) │
└─────────────────┘      └───────────────────────────┘      └─────────────────────────┘
```

---

## 🧪 Testing

Run comprehensive pytest suite:

```bash
pytest -v
```

---

## 📜 License

MIT © 2026 NeuroSort Authors.
