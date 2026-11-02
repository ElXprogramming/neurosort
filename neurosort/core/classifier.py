"""Semantic classification and filename standardization engine."""

from __future__ import annotations

from datetime import datetime
import fnmatch
import json
import logging
from pathlib import Path
import re
from typing import Any, Dict, List, Optional
import httpx
from pydantic import BaseModel, Field

from neurosort.config import NeuroSortConfig
from neurosort.extractors.generic import FileContentContext
from neurosort.utils.hashing import sanitize_filename, sanitize_path

logger = logging.getLogger("neurosort.classifier")


class ClassificationResult(BaseModel):
    """Pydantic model representing LLM or rule-based classification output."""

    target_folder: str = Field(
        description="Relative target subfolder under destination_root (e.g., Finance/Invoices)"
    )
    standardized_name: str = Field(
        description="Clean standardized filename including extension"
    )
    confidence: float = Field(
        ge=0.0, le=1.0, description="Confidence score between 0.0 and 1.0"
    )
    reasoning: str = Field(description="1-sentence justification for classification")
    tags: List[str] = Field(
        default_factory=list, description="2 to 4 descriptive categorization tags"
    )
    category: str = Field(default="Unsorted", description="Primary category cluster")
    entity: str = Field(default="general", description="Identified entity, vendor, or project")


class SemanticClassifier:
    """Classifies files using rule precedence, local Ollama / OpenAI LLMs, or rule heuristics."""

    def __init__(self, config: NeuroSortConfig):
        self.config = config

    def classify(self, context: FileContentContext) -> ClassificationResult:
        """Classify a file and generate its standardized name with full safety sanitization.

        Order of evaluation:
        1. Exact extension / glob pattern overrides from configuration.
        2. LLM classification (Ollama / OpenAI) if configured and reachable.
        3. Heuristic taxonomy matcher fallback.
        """
        # 1. Exact Precedence Overrides
        override_result = self._check_exact_overrides(context)
        if override_result:
            return self._sanitize_result(override_result, context)

        # 2. LLM Classification (if enabled)
        if self.config.ai_provider.provider in ("ollama", "openai"):
            try:
                llm_result = self._classify_with_llm(context)
                if llm_result:
                    return self._sanitize_result(llm_result, context)
            except Exception as exc:
                logger.warning(
                    f"LLM classification failed for {context.file_name}: {exc}. "
                    "Falling back to rule heuristics."
                )
                if not self.config.ai_provider.fallback_to_rules:
                    raise

        # 3. Deterministic Heuristic Fallback
        heuristic_result = self._classify_heuristically(context)
        return self._sanitize_result(heuristic_result, context)

    def _check_exact_overrides(
        self, context: FileContentContext
    ) -> Optional[ClassificationResult]:
        """Match filename against exact pattern overrides (e.g., *.iso -> Archives/ISO)."""
        overrides = self.config.rules_precedence.exact_overrides
        filename_lower = context.file_name.lower()

        for pattern, target_folder in overrides.items():
            if fnmatch.fnmatch(filename_lower, pattern.lower()):
                # Keep original base name sanitized with template or directly
                category = target_folder.split("/")[0] if "/" in target_folder else target_folder
                standardized = self._render_template(
                    context=context,
                    category=category,
                    entity="archive",
                )
                return ClassificationResult(
                    target_folder=target_folder,
                    standardized_name=standardized,
                    confidence=1.0,
                    reasoning=f"Matched exact pattern override rule '{pattern}'",
                    tags=["override", category.lower()],
                    category=category,
                    entity="system",
                )
        return None

    def _build_prompt(self, context: FileContentContext) -> str:
        """Construct a structured prompt for Ollama or OpenAI JSON generation."""
        taxonomy_list = list(self.config.rules_precedence.taxonomy_clusters.keys())
        date_str = context.created_date.strftime("%Y-%m-%d")

        snippet = context.extracted_text_snippet
        if len(snippet) > 1200:
            snippet = snippet[:1200] + "... [truncated]"

        prompt = f"""You are NeuroSort, an automated file taxonomy and naming expert.
Analyze the following file context and determine the ideal target folder and standardized filename.

=== FILE CONTEXT ===
Original Name: {context.file_name}
Extension: {context.extension}
MIME Type: {context.mime_type}
File Size: {context.file_size} bytes
File Date: {date_str}
Extracted Content / Metadata:
{snippet if snippet else "(No text extracted - binary or empty)"}

=== AVAILABLE TAXONOMY CLUSTERS ===
{json.dumps(taxonomy_list, indent=2)}

=== NAMING CONVENTION ===
Target template: {self.config.renaming_template}
- date: YYYY-MM-DD
- category: lowercase slug (e.g., invoices, medical, datasets, code, wallpapers)
- entity: identified company, institution, author, or project (or 'general')
- original_title: sanitized stem of the original file

=== REQUIREMENTS ===
Respond ONLY with a valid JSON object matching this schema:
{{
  "target_folder": "Subpath under destination_root, e.g., 'Finance/Invoices'",
  "category": "Top category, e.g., 'Finance'",
  "entity": "Extracted vendor, person, company, or 'general'",
  "standardized_name": "Formatted name with extension, e.g., '2024-03-15_finance_acme_march-bill.pdf'",
  "confidence": 0.95,
  "reasoning": "1-sentence explanation of classification",
  "tags": ["invoice", "tax", "acme"]
}}
Do NOT wrap your response in markdown code blocks or add explanations. Output raw JSON only.
"""
        return prompt

    def _classify_with_llm(
        self, context: FileContentContext
    ) -> Optional[ClassificationResult]:
        """Execute LLM call against Ollama or OpenAI endpoint with structured JSON parsing."""
        prompt = self._build_prompt(context)
        cfg = self.config.ai_provider

        if cfg.provider == "ollama":
            url = f"{cfg.base_url.rstrip('/')}/api/generate"
            payload = {
                "model": cfg.model,
                "prompt": prompt,
                "format": "json",
                "stream": False,
                "options": {
                    "temperature": cfg.temperature,
                },
            }
            with httpx.Client(timeout=cfg.timeout_seconds) as client:
                resp = client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
                raw_response = data.get("response", "{}")
                parsed = json.loads(raw_response)
                return ClassificationResult.model_validate(parsed)

        elif cfg.provider == "openai":
            url = f"{cfg.base_url.rstrip('/')}/chat/completions"
            headers = {"Content-Type": "application/json"}
            if cfg.api_key:
                headers["Authorization"] = f"Bearer {cfg.api_key}"

            payload = {
                "model": cfg.model,
                "messages": [
                    {
                        "role": "system",
                        "content": "You are a file classification engine that strictly outputs JSON.",
                    },
                    {"role": "user", "content": prompt},
                ],
                "response_format": {"type": "json_object"},
                "temperature": cfg.temperature,
            }
            with httpx.Client(timeout=cfg.timeout_seconds) as client:
                resp = client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                parsed = json.loads(content)
                return ClassificationResult.model_validate(parsed)

        return None

    def _classify_heuristically(
        self, context: FileContentContext
    ) -> ClassificationResult:
        """Deterministic keyword and MIME scoring fallback when LLM is unavailable."""
        search_corpus = f"{context.file_name} {context.extracted_text_snippet}".lower()
        clusters = self.config.rules_precedence.taxonomy_clusters

        best_cluster = "Documents/Work"
        highest_score = 0
        matched_tags: List[str] = []

        # Check clusters by keyword hit count
        for cluster_name, keywords in clusters.items():
            score = 0
            current_tags = []
            for kw in keywords:
                if re.search(r"\b" + re.escape(kw.lower()) + r"\b", search_corpus):
                    score += 2
                    current_tags.append(kw)
                elif kw.lower() in search_corpus:
                    score += 1

            if score > highest_score:
                highest_score = score
                best_cluster = cluster_name
                matched_tags = current_tags

        # If zero keyword score, make a sane guess based on MIME / extension
        if highest_score == 0:
            mime = context.mime_type
            ext = context.extension_normalized
            if mime.startswith("image/"):
                if context.raw_metadata.get("likely_wallpaper"):
                    best_cluster = "Media/Wallpapers"
                else:
                    best_cluster = "Media/Photos"
                matched_tags = ["image", ext]
            elif ext in ("py", "rs", "go", "js", "ts", "cpp", "c", "sh"):
                best_cluster = "Dev/SourceCode"
                matched_tags = ["code", ext]
            elif ext in ("csv", "json", "parquet", "tsv"):
                best_cluster = "Dev/Datasets"
                matched_tags = ["dataset", ext]
            elif ext in ("pdf", "docx", "doc"):
                best_cluster = "Documents/Work"
                matched_tags = ["document"]
            else:
                best_cluster = "Unsorted"
                matched_tags = [ext or "file"]

        # Infer category and entity
        category = best_cluster.split("/")[-1].lower()
        entity = self._extract_entity_heuristic(context)

        standardized_name = self._render_template(
            context=context, category=category, entity=entity
        )

        confidence = 0.85 if highest_score >= 2 else (0.70 if highest_score == 1 else 0.50)

        return ClassificationResult(
            target_folder=best_cluster,
            standardized_name=standardized_name,
            confidence=confidence,
            reasoning=f"Classified by keyword/MIME heuristics into {best_cluster}",
            tags=matched_tags[:4] or [category],
            category=category,
            entity=entity,
        )

    def _extract_entity_heuristic(self, context: FileContentContext) -> str:
        """Extract a plausible entity/vendor name from text snippet or filename."""
        # Check text snippet for common invoice entities or header patterns
        text = context.extracted_text_snippet
        m = re.search(r"(?:from|vendor|billed to|company|issuer):\s*([A-Za-z0-9_\-\s]{2,20})", text, re.IGNORECASE)
        if m:
            candidate = m.group(1).strip()
            return re.sub(r"\s+", "-", candidate).lower()

        # Check metadata author
        author = context.raw_metadata.get("author")
        if author and isinstance(author, str) and len(author.strip()) > 1:
            return re.sub(r"\s+", "-", author.strip()).lower()[:20]

        # Extract from file stem if contains hyphen/underscore
        stem_parts = re.split(r"[_\-\s]+", Path(context.file_name).stem)
        if len(stem_parts) > 1 and len(stem_parts[0]) > 2 and not stem_parts[0].isdigit():
            return stem_parts[0].lower()

        return "general"

    def _render_template(
        self, context: FileContentContext, category: str, entity: str
    ) -> str:
        """Render the renaming template string safely."""
        date_str = context.created_date.strftime("%Y-%m-%d")
        ext = context.extension_normalized
        original_stem = sanitize_filename(Path(context.file_name).stem)

        # Clean tokens
        clean_cat = sanitize_filename(category.lower())
        clean_ent = sanitize_filename(entity.lower())

        rendered = self.config.renaming_template.format(
            date=date_str,
            category=clean_cat,
            entity=clean_ent,
            original_title=original_stem,
            ext=ext,
        )

        # Fallback if rendered filename is missing extension
        if ext and not rendered.endswith(f".{ext}"):
            rendered = f"{rendered}.{ext}"

        return sanitize_filename(rendered)

    def _sanitize_result(
        self, result: ClassificationResult, context: FileContentContext
    ) -> ClassificationResult:
        """Enforce strict security sanitization on target_folder and standardized_name."""
        safe_folder = sanitize_path(result.target_folder)

        raw_name = result.standardized_name
        # Guarantee extension matches original file extension
        expected_ext = context.extension.lower()
        safe_name = sanitize_filename(raw_name)

        if expected_ext and not safe_name.lower().endswith(expected_ext):
            safe_name = f"{safe_name}{expected_ext}"

        return ClassificationResult(
            target_folder=safe_folder,
            standardized_name=safe_name,
            confidence=round(result.confidence, 2),
            reasoning=result.reasoning.strip(),
            tags=[sanitize_filename(t) for t in result.tags[:4]],
            category=sanitize_filename(result.category),
            entity=sanitize_filename(result.entity),
        )
