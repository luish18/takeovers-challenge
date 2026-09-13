"""Paths, model names and other settings, resolved from the environment."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
# The nearest .env up the tree, so a git worktree picks up the main checkout's.
if dotenv_path := next((base / ".env" for base in (REPO_ROOT, *REPO_ROOT.parents) if (base / ".env").is_file()), None):
    load_dotenv(dotenv_path)

SIREN = "480489707"
SUBJECT_NAME = "ARCHEAN TECHNOLOGIES"

# Filings of other companies that record movements in the subject's shares. ARCHEAN's own folder shows
# HADEAN as sole shareholder from June 2008 without the transfers that got it there; these HADEAN actes
# (its incorporation by contribution of ARCHEAN shares, and a later contribution) are that missing link.
CROSS_REFERENCE_DOCS = {
    "499979540": [
        "63f0a89c7a07a2434c069135",  # 2007-09-18 HADEAN statuts constitutifs: apports of ARCHEAN shares
        "63f0a89c7a07a2434c069136",  # 2008-04-30 commissaire aux apports: CAPGRAS contributes 225 shares
        "63f0a89c7a07a2434c069137",  # 2008-05-29 HADEAN statuts mis à jour
    ],
}


def _find_challenges_root() -> Path:
    """The `engineering-challenges` checkout: $ACTES_DATA_DIR's parent, or the nearest one up the tree.

    Walking up lets a git worktree (whose submodule is not initialised) reuse the main checkout.
    """
    if env := os.getenv("ACTES_DATA_DIR"):
        return Path(env).expanduser().resolve().parent
    for base in (REPO_ROOT, *REPO_ROOT.parents):
        candidate = base / "engineering-challenges"
        if (candidate / "data" / SIREN / "actes").is_dir():
            return candidate
    raise FileNotFoundError(
        "engineering-challenges data not found: run `git submodule update --init` or set ACTES_DATA_DIR"
    )


CHALLENGES_ROOT = _find_challenges_root()
DATA_DIR = CHALLENGES_ROOT / "data"
SCHEMA_DIR = CHALLENGES_ROOT / "challenges" / "actes" / "schema"
BBOX_VIEWER = CHALLENGES_ROOT / "tools" / "bbox_viewer.py"

EXTRACT_MODEL = os.getenv("ACTES_EXTRACT_MODEL", "claude-sonnet-5")
REVIEW_MODEL = os.getenv("ACTES_REVIEW_MODEL", "claude-opus-5")

CACHE_DIR = REPO_ROOT / "cache" / "llm"
OVERRIDES_PATH = REPO_ROOT / "overrides.yaml"
RESULTS_PATH = REPO_ROOT / "results.json"
REVIEW_DIR = REPO_ROOT / "review"
