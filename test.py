#!/usr/bin/env python3
"""
Simple CLI program that imports the `essaygrader` module and runs it.

Usage examples:
  1) Interactive (press Enter to use bundled sample data):
     $ python test.py

  2) Provide your own essay and requirements inline:
     $ python test.py --essay "Photosynthesis converts light to chemical energy." \
                      -r "Mentions photosynthesis" \
                      -r "Says light energy becomes chemical energy"

  3) From files:
     $ python test.py --essay-file my_essay.txt --requirements-file my_requirements.txt

Environment variables respected by the underlying grader:
  - ESSAYGRADER_MODEL (default: "llama3.1:8b")
  - ESSAYGRADER_OLLAMA_BASE_URL (default: "http://localhost:11434")
  - OPENAI_API_KEY (optional: if set, uses OpenAI)

This script prints the grading result as pretty JSON.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Optional, Union
from pathlib import Path

from essaygrader import grade_essay

def _read_text_file(path: Union[str, Path]) -> str:
    p = Path(path)
    try:
        return p.read_text(encoding="utf-8")
    except Exception as e:
        raise SystemExit(f"Failed to read file '{p}': {e}")


def _default_sample() -> tuple[str, list[str]]:
    essay = (
        "Photosynthesis converts light energy into chemical energy. "
        "Chlorophyll in chloroplasts absorbs light, producing glucose and oxygen. "
        "Carbon dioxide and water are essential inputs."
    )
    requirements = [
        "Explains that photosynthesis converts light energy into chemical energy",
        "Mentions the role of chlorophyll",
        "States that oxygen is produced",
        "Includes the inputs: carbon dioxide and water",
    ]
    return essay, requirements


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="test.py",
        description="Run essaygrader on an essay with a list of requirements and print the result as JSON.",
    )
    src = parser.add_argument_group("input sources")
    src.add_argument("--essay", help="Essay text (if omitted, use --essay-file or the built-in sample)")
    src.add_argument("--essay-file", help="Path to a UTF-8 text file containing the essay")
    src.add_argument(
        "-r",
        "--requirement",
        action="append",
        dest="requirements",
        help="Requirement sentence (may be repeated). Ignored if --requirements-file is provided.",
    )
    src.add_argument("--requirements-file", help="Path to a UTF-8 text file with one requirement per line")

    cfg = parser.add_argument_group("grading configuration")
    cfg.add_argument("--model", help="Model name for local LLM (overrides ESSAYGRADER_MODEL)")
    cfg.add_argument("--base-url", help="Base URL for Ollama server (overrides ESSAYGRADER_OLLAMA_BASE_URL)")
    cfg.add_argument("--temperature", type=float, default=0.2, help="Sampling temperature (default: 0.2)")
    cfg.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout seconds (default: 30.0)")

    return parser.parse_args(argv)


def collect_inputs(ns: argparse.Namespace) -> tuple[str, list[str]]:
    # Determine essay text
    if ns.essay_file:
        essay = _read_text_file(ns.essay_file)
    elif ns.essay:
        essay = ns.essay
    else:
        # No inputs given – fall back to the built-in sample
        essay, requirements = _default_sample()
        return essay, requirements

    # Determine requirements list
    if ns.requirements_file:
        text = _read_text_file(ns.requirements_file)
        requirements = [line.strip() for line in text.splitlines() if line.strip()]
        if not requirements:
            raise SystemExit("The requirements file is empty.")
    elif ns.requirements:
        requirements = [r for r in ns.requirements if r and r.strip()]
        if not requirements:
            raise SystemExit("No valid requirements provided via -r/--requirement.")
    else:
        # If essay was provided but no requirements, prompt minimal guidance
        raise SystemExit(
            "You provided an essay but no requirements. Use -r/--requirement multiple times or --requirements-file."
        )

    return essay, requirements


def main(argv: list[str]) -> int:
    ns = parse_args(argv)

    # Gather inputs (uses default sample when nothing was provided)
    essay, requirements = collect_inputs(ns)

    # Call the grader
    result = grade_essay(
        essay,
        requirements,
        model=ns.model,
        base_url=ns.base_url,
        temperature=ns.temperature,
        timeout=ns.timeout,
    )

    # Print pretty JSON to stdout
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
