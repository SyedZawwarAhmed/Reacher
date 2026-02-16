"""Load and validate config.yaml into AppConfig."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import yaml
from pydantic import ValidationError

from src.models import AppConfig

DEFAULT_CONFIG_PATH = Path("config.yaml")
EXAMPLE_CONFIG_PATH = Path("config.yaml.example")


def load_config(path: Optional[Path] = None) -> AppConfig:
    """Load configuration from a YAML file and validate with Pydantic.

    Args:
        path: Path to the config file. Defaults to config.yaml in the project root.

    Returns:
        Validated AppConfig instance.

    Raises:
        SystemExit: If the config file is missing or invalid.
    """
    config_path = path or DEFAULT_CONFIG_PATH

    if not config_path.exists():
        print(f"Error: Config file not found at '{config_path}'.")
        if EXAMPLE_CONFIG_PATH.exists():
            print(
                f"  Copy the example config to get started:\n"
                f"    cp {EXAMPLE_CONFIG_PATH} {DEFAULT_CONFIG_PATH}\n"
                f"  Then fill in your API keys and preferences."
            )
        sys.exit(1)

    with open(config_path, "r") as f:
        raw = yaml.safe_load(f)

    if not raw or not isinstance(raw, dict):
        print(f"Error: Config file '{config_path}' is empty or malformed.")
        sys.exit(1)

    try:
        config = AppConfig(**raw)
    except ValidationError as e:
        print(f"Error: Invalid configuration in '{config_path}':\n{e}")
        sys.exit(1)

    _warn_placeholders(config)
    return config


def _warn_placeholders(config: AppConfig) -> None:
    """Warn if placeholder values are still present."""
    warnings = []
    if "YOUR_" in config.email.app_password:
        warnings.append("  - email.app_password is still a placeholder")
    if "YOUR_" in config.gemini.api_key:
        warnings.append("  - gemini.api_key is still a placeholder")
    if "YOUR_" in config.twitter.bearer_token:
        warnings.append("  - twitter.bearer_token is still a placeholder")

    if warnings:
        print("Warning: Some config values appear to be placeholders:")
        for w in warnings:
            print(w)
        print("  Update config.yaml with your real credentials.\n")


def get_resume_text(config: AppConfig) -> str:
    """Extract plain text from the configured resume PDF.

    Falls back to reading a .txt file with the same base name.
    """
    pdf_path = Path(config.profile.resume_pdf)

    if pdf_path.exists() and pdf_path.suffix.lower() == ".pdf":
        try:
            from PyPDF2 import PdfReader

            reader = PdfReader(str(pdf_path))
            text = "\n".join(
                page.extract_text() or "" for page in reader.pages
            )
            if text.strip():
                return text.strip()
        except Exception as e:
            print(f"Warning: Could not read PDF '{pdf_path}': {e}")

    txt_path = pdf_path.with_suffix(".txt")
    if txt_path.exists():
        return txt_path.read_text().strip()

    print(
        f"Warning: No resume found at '{pdf_path}' or '{txt_path}'. "
        f"Email personalization may be limited."
    )
    return ""
