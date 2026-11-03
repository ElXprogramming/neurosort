# Contributing to NeuroSort 🧠📁

Thank you for your interest in contributing to **NeuroSort**! We welcome community contributions, bug reports, and feature proposals to make local-first file organization faster, safer, and smarter.

---

## 🛠️ Development Setup

1. **Fork and Clone the Repository**:
   ```bash
   git clone https://github.com/<your-username>/neurosort.git
   cd neurosort
   ```

2. **Create and Activate a Virtual Environment**:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. **Install Dependencies in Editable Mode**:
   ```bash
   pip install -e ".[dev,ocr,embeddings]"
   ```

4. **Verify Installation**:
   ```bash
   neurosort --help
   pytest -v
   ```

---

## 🧪 Testing Guidelines

We prioritize **zero data-loss** and **strict regression safety**. All contributions that modify file operations, ledger transactions, or extractors must include automated tests:

- Add or update tests in `tests/`.
- Ensure all tests pass:
  ```bash
  pytest -v
  ```
- Run formatting and linting:
  ```bash
  ruff check .
  ```

---

## 💡 Submitting Issues & Pull Requests

1. **Bug Reports**: Open an issue detailing your OS, Python version, Ollama model (if applicable), steps to reproduce, and any relevant traceback or logs.
2. **Feature Requests**: Describe the use case and why local-first file management benefits from it.
3. **Pull Requests**:
   - Create a feature branch: `git checkout -b feature/my-new-feature`
   - Keep commits atomic and descriptive.
   - Ensure `pytest` passes with 100% success rate.
   - Submit PR targeting the `main` branch.

---

## 📜 Code of Conduct

Please review and adhere to our [Code of Conduct](CODE_OF_CONDUCT.md) in all community interactions.
