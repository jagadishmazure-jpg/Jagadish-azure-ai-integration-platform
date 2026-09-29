"""Repository hygiene: no secrets or personal email addresses, every folder documents itself,
sandbox stand-ins are labeled, and internal reference material is never committed."""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".demo-logs",
    "evals-out",
    "node_modules",
}
# folders whose loaders glob files: a README would be read as data
NO_README = {
    ROOT / "src" / "aiip" / "fakesaas" / "fixtures",
    ROOT / "evals" / "golden",
    ROOT / "control-plane" / "agent-cards",
    ROOT / "logicapps" / "vendor-invoice",
}


def repo_files():
    try:
        out = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
        return [ROOT / f for f in out if (ROOT / f).is_file()]
    except (subprocess.CalledProcessError, FileNotFoundError):
        return [p for p in ROOT.rglob("*") if p.is_file() and not SKIP_DIRS & set(p.parts)]


def test_no_personal_emails_or_secrets():
    # personal addresses are stored only as SHA-256 digests so this test does not contain them
    personal_digests = {
        "f8ece44ef1e93b2695d600702159fa2d2eb64a0004065607c3a79c2eda317e5d",
        "74c16c0397387420e8fa3b70abc0ef6ed8242090c04a0e39921e9bc8c5a2bb43",
    }
    email = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
    secretish = re.compile(
        r"(AKIA[0-9A-Z]{16}|-----BEGIN (RSA |EC )?PRIVATE KEY-----|gh[pousr]_[A-Za-z0-9]{30,}|xox[bap]-[A-Za-z0-9-]{10,}|AccountKey=[A-Za-z0-9+/=]{20,}|InstrumentationKey=[0-9a-f-]{36})"
    )
    for f in repo_files():
        if f.suffix in {".png", ".jpg", ".pyc"}:
            continue
        text = f.read_text(errors="ignore")
        found = {hashlib.sha256(m.lower().encode()).hexdigest() for m in email.findall(text)}
        assert not found & personal_digests, f"personal email in {f.relative_to(ROOT)}"
        assert not secretish.search(text), f"secret-like value in {f.relative_to(ROOT)}"


def test_reference_material_is_not_in_the_repo():
    names = {f.name for f in repo_files()}
    assert not any(n.startswith("azure-integration-standard") for n in names)
    assert not (ROOT / "refs").exists()


def test_every_folder_has_a_readme_with_a_file_table():
    dirs = {f.parent for f in repo_files()} - NO_README
    missing, no_table = [], []
    for d in sorted(dirs):
        if SKIP_DIRS & set(d.relative_to(ROOT).parts):
            continue
        readme = d / "README.md"
        if not readme.exists():
            missing.append(str(d.relative_to(ROOT)))
        elif "| File | What it does |" not in readme.read_text():
            no_table.append(str(d.relative_to(ROOT)))
    assert not missing, f"folders without README.md: {missing}"
    assert not no_table, f"READMEs without a 'File | What it does' table: {no_table}"


def test_no_markdown_inside_globbed_data_folders():
    for d in NO_README:
        assert not list(d.glob("*.md")), d


def test_required_docs_exist():
    for doc in (
        "architecture",
        "identity",
        "events",
        "bpm",
        "connectors",
        "observability",
        "interview-guide",
        "sdk-notes",
        "cost-estimate",
        "deploy",
    ):
        assert (ROOT / "docs" / f"{doc}.md").exists(), doc


def test_stand_ins_are_labeled():
    readme = (ROOT / "src" / "aiip" / "fakesaas" / "README.md").read_text().lower()
    assert "stand-in" in readme and "not" in readme
