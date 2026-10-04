"""Documentation structure checks: component docs, guides, ownership, no placeholders or dates, counts."""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECTIONS = [
    "Purpose",
    "Architecture",
    "How it works",
    "Key files",
    "Code excerpts",
    "Configuration",
    "Commands",
    "Real output",
    "Tests and eval gates",
    "Guardrails",
    "Security and governance",
    "Observability",
    "Failure modes",
    "Mapping to Azure services",
    "Limitations",
    "Interview talking points",
    "Adopt this",
]
SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".demo-logs", ".terraform"}
# Folder READMEs are checked by tests/test_repo_hygiene.py.


def _component_docs():
    return sorted(p for p in (ROOT / "docs/components").glob("*.md") if p.name != "README.md")


def test_every_component_doc_has_the_17_sections_in_order():
    docs = _component_docs()
    assert len(docs) >= 14
    for p in docs:
        heads = re.findall(r"^## \d+\. (.+)$", p.read_text(), re.M)
        assert heads == SECTIONS, p.name
        assert "```mermaid" in p.read_text(), p.name


def test_component_docs_are_indexed():
    index = (ROOT / "docs/components/README.md").read_text()
    for p in _component_docs():
        assert f"({p.name})" in index, p.name


def test_guides_exist_and_link_components():
    for name in ("implementation-guide.md", "adopt-this.md"):
        text = (ROOT / "docs" / name).read_text()
        assert "components/" in text, name


def test_codeowners():
    assert "* @jagadishmazure-jpg" in (ROOT / ".github/CODEOWNERS").read_text()


def test_no_placeholders_or_dates_in_docs():
    bad = []
    for p in ROOT.rglob("*.md"):
        if SKIP_DIRS & set(p.relative_to(ROOT).parts):
            continue
        text = p.read_text()
        if re.search(r"\b(TODO|TBD|FIXME)\b", text):
            bad.append(f"{p}: placeholder")
        if re.search(r"\b20\d\d-\d\d-\d\d\b", re.sub(r"`[^`]*`|```.*?```", "", text, flags=re.S)):
            bad.append(f"{p}: date")
    # API versions, model versions and fixture dates are written in code spans: identifiers, not doc dates.
    assert not bad, bad


def test_readme_test_count_matches_collection():
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "--co", "-q", "-p", "no:cacheprovider", str(ROOT / "tests")],
        capture_output=True,
        text=True,
        cwd=ROOT,
    ).stdout
    n = int(re.search(r"(\d+) tests? collected", out).group(1))
    readme = (ROOT / "README.md").read_text()
    counts = {int(x) for x in re.findall(r"(\d+)\*{0,2} (?:automated |offline )tests", readme)}
    assert counts == {n}, (counts, n)
