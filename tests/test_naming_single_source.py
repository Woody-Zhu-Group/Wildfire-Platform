"""Naming conventions are defined once, in the registry and its naming module.

Fails if a module outside ``services/shared/dataset_registry.py`` and
``services/shared/naming.py`` defines its own list of utility, county, HFTD
tier, or dataset names or aliases. A module that needs one imports it from
``services.shared.dataset_registry``.

What counts as a definition (string literals only; derived values are fine):

- a list, tuple, set, or dict holding two or more utility spellings, two or
  more California county names, or both HFTD tier names;
- a dict that maps one dataset name or alias to another, or maps dataset names
  to dataset labels the registry already defines;
- a regex string that names two or more utilities or three or more datasets
  (two dataset words beside other nouns are usually a topic check, not a list).

A single name used in logic (``utility == "PGE"``) is not a definition.

Not scanned, on purpose: tests (they assert the values), ``analysis/``
(one-off research scripts whose results are committed beside them),
``services/risk_forecasting/legacy/`` and the two model files the project
keeps unmodified, and ``frontend/assets/js`` (the legacy static map page, see
services/shared/README.md). The website source is scanned for the county list,
utility lists, and tier lists.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from services.shared import dataset_registry as reg

ROOT = Path(__file__).resolve().parents[1]
PY_ROOTS = ("services", "db", "scripts", "shared")
EXEMPT = {
    "services/shared/dataset_registry.py",
    "services/shared/naming.py",
    "services/risk_forecasting/models.py",
    "services/risk_forecasting/grid_data_prep.py",
}
EXEMPT_DIRS = ("services/risk_forecasting/legacy/",)


def _fold(value: str) -> str:
    return " ".join(value.split()).casefold()


UTILITY_WORDS = {
    _fold(value)
    for value in (
        *reg.UTILITY_CODES,
        *reg.UTILITY_DISPLAY_LABELS.values(),
        *reg.WORKSPACE_UTILITIES,
        *reg.UTILITY_FULL_NAMES.values(),
        *reg.UTILITY_POSSESSIVE_NAMES.values(),
        *reg.UTILITY_CLARIFY_LABELS.values(),
        *reg.IOU_PUBLISHER_UTILITY_CODES,
        *reg.UTILITY_ARGUMENT_ALIASES,
        *reg.UTILITY_FILTER_KEYS,
    )
} - {_fold(reg.UNTAGGED_UTILITY)}
# The website compares case-sensitively: 'pge' there is a filter mode, not a code.
WEBSITE_UTILITY_WORDS = {
    " ".join(value.split())
    for value in (
        *reg.UTILITY_CODES,
        *reg.UTILITY_DISPLAY_LABELS.values(),
        *reg.WORKSPACE_UTILITIES,
        *reg.UTILITY_FULL_NAMES.values(),
    )
}
COUNTY_WORDS = {_fold(name) for name in reg.CALIFORNIA_COUNTIES}
TIER_WORDS = {_fold(name) for name in reg.HFTD_TIER_NAMES}
DATASET_WORDS = {
    _fold(value)
    for entry in reg.DATASETS.values()
    for value in (entry.key, *entry.aliases, entry.viz_key, entry.agent_key)
    if value
} | {_fold(key) for key in reg.ARGUMENT_RECORDS_DATASET_ALIASES}
DATASET_LABELS = {
    _fold(value)
    for entry in reg.DATASETS.values()
    for value in (
        entry.stat_label,
        (entry.style or {}).get("label"),
        (entry.style or {}).get("chart_short_label"),
    )
    if value
} | {_fold(value) for value in reg.CLARIFY_DATASET_LABELS.values()}

# Words a regex uses to name a utility or a dataset.
_UTILITY_REGEX_WORDS = re.compile(
    r"pge|pg\\s|pacific\s+gas|\bsce\b|edison|sdge|sdg\\s|san\s+diego\s+gas|pacificorp|"
    r"liberty|bves|bear\s+valley",
    re.I,
)
# Dataset names (not generic nouns such as circuits or outages).
_DATASET_REGEX_WORDS = {
    "cpuc": re.compile(r"cpuc", re.I),
    "calfire": re.compile(r"cal\\s\*fire|calfire|cal fire", re.I),
    "epss": re.compile(r"epss", re.I),
    "psps": re.compile(r"psps", re.I),
    "hftd": re.compile(r"hftd", re.I),
    "us_sample": re.compile(r"\bus\s+ignitions|firecast", re.I),
}


def _python_files() -> list[Path]:
    files: list[Path] = []
    for top in PY_ROOTS:
        for path in sorted((ROOT / top).rglob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            if rel in EXEMPT or rel.startswith(EXEMPT_DIRS) or "/node_modules/" in rel:
                continue
            files.append(path)
    return files


def _strings(nodes) -> list[str]:
    return [n.value for n in nodes if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def _hits(values: list[str], vocabulary: set[str]) -> set[str]:
    return {_fold(value) for value in values} & vocabulary


def _collection_problems(node: ast.AST) -> list[str]:
    problems: list[str] = []
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        values = _strings(node.elts)
    elif isinstance(node, ast.Dict):
        values = _strings([k for k in node.keys if k is not None]) + _strings(node.values)
    else:
        return problems
    if len(_hits(values, UTILITY_WORDS)) >= 2:
        problems.append(f"utility names {sorted(_hits(values, UTILITY_WORDS))}")
    if len(_hits(values, COUNTY_WORDS)) >= 2:
        problems.append(f"county names {sorted(_hits(values, COUNTY_WORDS))[:4]}")
    if _hits(values, TIER_WORDS) == TIER_WORDS:
        problems.append("HFTD tier names")
    if isinstance(node, ast.Dict):
        pairs = [
            (k.value, v.value)
            for k, v in zip(node.keys, node.values)
            if isinstance(k, ast.Constant) and isinstance(k.value, str)
            and isinstance(v, ast.Constant) and isinstance(v.value, str)
        ]
        aliases = [
            (k, v)
            for k, v in pairs
            if _fold(k) in DATASET_WORDS and _fold(v) in DATASET_WORDS and _fold(k) != _fold(v)
        ]
        if len(aliases) >= 2:
            problems.append(f"dataset alias map {aliases[:3]}")
        labels = [(k, v) for k, v in pairs if _fold(k) in DATASET_WORDS and _fold(v) in DATASET_LABELS]
        if len(labels) >= 2:
            problems.append(f"dataset label map {labels[:3]}")
    return problems


def _regex_problems(value: str) -> list[str]:
    if "\\b" not in value and "(?:" not in value:
        return []
    problems: list[str] = []
    utilities = {m.group(0).casefold() for m in _UTILITY_REGEX_WORDS.finditer(value)}
    if len(utilities) >= 2:
        problems.append(f"utility regex naming {sorted(utilities)}")
    datasets = [name for name, pattern in _DATASET_REGEX_WORDS.items() if pattern.search(value)]
    if len(datasets) >= 3:
        problems.append(f"dataset regex naming {datasets}")
    return problems


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def python_findings() -> list[str]:
    findings: list[str] = []
    for path in _python_files():
        rel = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        docstrings = _docstring_nodes(tree)
        for node in ast.walk(tree):
            for problem in _collection_problems(node):
                findings.append(f"{rel}:{node.lineno}: {problem}")
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstrings
            ):
                for problem in _regex_problems(node.value):
                    findings.append(f"{rel}:{node.lineno}: {problem}")
    return findings


_QUOTED = re.compile(r"""(['"`])((?:(?!\1).){1,40})\1""")


def website_findings() -> list[str]:
    findings: list[str] = []
    for path in sorted((ROOT / "website" / "src").rglob("*.ts*")):
        rel = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        counties = {_fold(m.group(2)) for m in _QUOTED.finditer(text)} & COUNTY_WORDS
        if len(counties) >= 3:
            findings.append(f"{rel}: county names {sorted(counties)[:4]}")
        for number, line in enumerate(text.splitlines(), start=1):
            raw = {" ".join(m.group(2).split()) for m in _QUOTED.finditer(line)}
            if len(raw & WEBSITE_UTILITY_WORDS) >= 2:
                findings.append(f"{rel}:{number}: utility names {sorted(raw & WEBSITE_UTILITY_WORDS)}")
            quoted = {_fold(value) for value in raw}
            if quoted & TIER_WORDS == TIER_WORDS:
                findings.append(f"{rel}:{number}: HFTD tier names")
    return findings


def test_no_module_outside_the_registry_defines_naming_lists():
    findings = python_findings()
    assert not findings, (
        "Naming lists defined outside services/shared/naming.py or the registry; "
        "import them from services.shared.dataset_registry instead:\n  " + "\n  ".join(findings)
    )


def test_website_reads_names_from_the_generated_catalog():
    findings = website_findings()
    assert not findings, (
        "Website source hardcodes naming lists; read them from shared/naming.json "
        "(generated by scripts/generate_frontend_registry.py):\n  " + "\n  ".join(findings)
    )


def test_the_guard_catches_a_copied_list():
    """The scan must flag the shapes it exists to catch."""
    samples = {
        'X = ("PGE", "SCE")': "utility names",
        'X = {"Butte": 1, "Lake": 2}': "county names",
        'X = frozenset({"Tier 2", "Tier 3"})': "HFTD tier names",
        'X = {"cal fire": "calfire", "epss": "epss_outages"}': "dataset alias map",
        'X = {"cpuc_ignitions": "CPUC ignitions", "psps_events": "PSPS events"}': "dataset label map",
        r'X = r"\b(?:pge|edison)\b"': "utility regex",
        r'X = r"\bepss\b|\bpsps\b|\bcal\s*fire\b"': "dataset regex",
    }
    for source, expected in samples.items():
        tree = ast.parse(source)
        found = [p for node in ast.walk(tree) for p in _collection_problems(node)]
        found += [
            p
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            for p in _regex_problems(node.value)
        ]
        assert any(expected in item for item in found), (source, found)
    # A single name in logic is not a list.
    tree = ast.parse('if utility not in ("PGE", "untagged"): pass')
    assert not [p for node in ast.walk(tree) for p in _collection_problems(node)]
