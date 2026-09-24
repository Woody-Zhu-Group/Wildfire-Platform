"""Naming conventions are defined once, in the registry and its naming module.

Fails if a module outside ``services/shared/dataset_registry.py`` and
``services/shared/naming.py`` defines its own list of utility, county, HFTD
tier, or dataset names or aliases, EPSS cause codes, or the CAL FIRE default
incident types. A module that needs one imports it from
``services.shared.dataset_registry``.

What counts as a definition (string literals only; derived values are fine):

- a list, tuple, set, or dict, or the body of an Enum subclass (member names
  and values together), holding two or more utility spellings, two or more
  California county names, both HFTD tier names, two or more EPSS cause codes,
  or both CAL FIRE default incident types ("Wildfire" and "Fire");
- a dict that maps one dataset name or alias to another, maps dataset names
  to dataset labels the registry already defines, or maps two or more EPSS
  cause codes to their word forms;
- a regex string (any string with ``|`` alternation; an f-string is checked
  with its literal parts joined) that names two or more utilities, two or
  more counties, or three or more datasets (two dataset words beside other
  nouns are usually a topic check, not a list);
- a string that spells the CAL FIRE default as SQL or a parameter
  (``'Wildfire', 'Fire'`` or ``Wildfire,Fire``).

A single name used in logic (``utility == "PGE"``) is not a definition. Prose
is not a definition either (services/shared/README.md keeps caveat text,
prompts, tool descriptions, and error messages as written): a string with a
sentence break, or the ``description``, ``detail``, ``help``, or ``summary``
argument of a call, is not read as a regex.

Not scanned, on purpose: tests (they assert the values), ``analysis/``
(one-off research scripts whose results are committed beside them),
``services/risk_forecasting/legacy/`` and the two model files the project
keeps unmodified, and ``frontend/assets/js`` (the legacy static map page, see
services/shared/README.md). ``services/data_query/_smoke_test.py`` is a
test script and asserts the values like the tests do. Each website source file is scanned as a whole
for the county list, utility lists, and tier lists, so a list written one item
per line is caught.
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
    "services/data_query/_smoke_test.py",
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
# EPSS cause codes and their word forms. The codes are distinctive; a word
# form alone ("Unknown") is ordinary text, so only code-to-word pairs count.
EPSS_CAUSE_CODES = {
    _fold(code) for code in (*reg.EPSS_CAUSE_CODE_WORDS, *reg.EPSS_CAUSE_CODES_LEFT_ALONE)
}
EPSS_CAUSE_PAIRS = {(_fold(code), _fold(word)) for code, word in reg.EPSS_CAUSE_CODE_WORDS.items()}
# The stored CAL FIRE default types. Compared case-sensitively: lowercase
# "fire" and "wildfire" are question words, not the stored values.
CALFIRE_DEFAULT_TYPES = set(reg.CALFIRE_DEFAULT_INCIDENT_TYPES)
_CALFIRE_DEFAULT_STRING = re.compile(
    r"""(['"])Wildfire\1\s*,\s*\1Fire\1|(?<![A-Za-z])Wildfire,Fire(?![A-Za-z])"""
)

# A regex word that is not inside a longer word; "\b" in the regex source
# counts as a boundary (the "b" before the name is part of the escape).
_L = r"(?:(?<![a-z])|(?<=\\b))"
_R = r"(?![a-z])"
# Words a regex uses to name each utility, so two spellings of one utility
# ("pge|pg&e") count once.
_UTILITY_REGEX_WORDS = {
    "PGE": re.compile(r"pge|pg\\s|pg\s*&|pacific\s+gas", re.I),
    "SCE": re.compile(_L + r"sce" + _R + r"|edison", re.I),
    "SDGE": re.compile(r"sdge|sdg\\s|sdg\s*&|san\s+diego\s+gas", re.I),
    "PACIFICORP": re.compile(r"pacificorp", re.I),
    "LIBERTY": re.compile(r"liberty", re.I),
    "BVES": re.compile(r"bves|bear\s+valley", re.I),
}
# County names in a regex: words separated by whitespace or a "\s" escape.
_REGEX_SPACE = r"(?:\s|\\s[+*?]?)+"
_COUNTY_REGEX = re.compile(
    _L
    + "(?:"
    + "|".join(
        _REGEX_SPACE.join(re.escape(word) for word in name.split())
        for name in sorted(COUNTY_WORDS, key=len, reverse=True)
    )
    + ")"
    + _R,
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
_ENUM_BASES = {"Enum", "StrEnum", "IntEnum", "Flag", "IntFlag"}
# Prose: a sentence break, or a documentation argument of a call.
_SENTENCE_BREAK = re.compile(r"[a-z][.!?]\s+[A-Z]")
_DOC_KEYWORDS = {"description", "detail", "help", "summary"}


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


def _is_enum_class(node: ast.AST) -> bool:
    if not isinstance(node, ast.ClassDef):
        return False
    for base in node.bases:
        name = base.attr if isinstance(base, ast.Attribute) else getattr(base, "id", None)
        if name in _ENUM_BASES:
            return True
    return False


def _enum_values(node: ast.ClassDef) -> list[str]:
    """Member names (underscores read as spaces) and string values of an Enum body."""
    values: list[str] = []
    for statement in node.body:
        if isinstance(statement, ast.Assign):
            targets, value = statement.targets, statement.value
        elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
            targets, value = [statement.target], statement.value
        else:
            continue
        values += [t.id.replace("_", " ") for t in targets if isinstance(t, ast.Name)]
        values += _strings([value])
    return values


def _collection_problems(node: ast.AST) -> list[str]:
    problems: list[str] = []
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        values = _strings(node.elts)
    elif isinstance(node, ast.Dict):
        values = _strings([k for k in node.keys if k is not None]) + _strings(node.values)
    elif _is_enum_class(node):
        values = _enum_values(node)
    else:
        return problems
    if len(_hits(values, UTILITY_WORDS)) >= 2:
        problems.append(f"utility names {sorted(_hits(values, UTILITY_WORDS))}")
    if len(_hits(values, COUNTY_WORDS)) >= 2:
        problems.append(f"county names {sorted(_hits(values, COUNTY_WORDS))[:4]}")
    if _hits(values, TIER_WORDS) == TIER_WORDS:
        problems.append("HFTD tier names")
    if len(_hits(values, EPSS_CAUSE_CODES)) >= 2:
        problems.append(f"EPSS cause codes {sorted(_hits(values, EPSS_CAUSE_CODES))}")
    if CALFIRE_DEFAULT_TYPES <= set(values):
        problems.append("CAL FIRE default incident types")
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
        causes = [(k, v) for k, v in pairs if (_fold(k), _fold(v)) in EPSS_CAUSE_PAIRS]
        if len(causes) >= 2:
            problems.append(f"EPSS cause code map {causes}")
    return problems


def _string_problems(value: str) -> list[str]:
    problems: list[str] = []
    if _CALFIRE_DEFAULT_STRING.search(value):
        problems.append("CAL FIRE default incident types in a string")
    if "|" not in value or _SENTENCE_BREAK.search(value):
        return problems
    utilities = [name for name, pattern in _UTILITY_REGEX_WORDS.items() if pattern.search(value)]
    if len(utilities) >= 2:
        problems.append(f"utility regex naming {utilities}")
    counties = {" ".join(re.split(_REGEX_SPACE, m.group(0))).casefold() for m in _COUNTY_REGEX.finditer(value)}
    if len(counties) >= 2:
        problems.append(f"county regex naming {sorted(counties)[:4]}")
    datasets = [name for name, pattern in _DATASET_REGEX_WORDS.items() if pattern.search(value)]
    if len(datasets) >= 3:
        problems.append(f"dataset regex naming {datasets}")
    return problems


def _joined_literal(node: ast.JoinedStr) -> str:
    """An f-string's literal parts, with each interpolation as a neutral placeholder."""
    return "".join(
        part.value if isinstance(part, ast.Constant) and isinstance(part.value, str) else "{}"
        for part in node.values
    )


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def _tree_problems(tree: ast.AST) -> list[tuple[int, str]]:
    skipped = _docstring_nodes(tree) | {
        id(keyword.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for keyword in node.keywords
        if keyword.arg in _DOC_KEYWORDS
    }
    in_fstring = {
        id(part)
        for node in ast.walk(tree)
        if isinstance(node, ast.JoinedStr)
        for part in node.values
    }
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        for problem in _collection_problems(node):
            found.append((node.lineno, problem))
        if id(node) in skipped:
            continue
        if isinstance(node, ast.JoinedStr):
            text = _joined_literal(node)
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in in_fstring
        ):
            text = node.value
        else:
            continue
        for problem in _string_problems(text):
            found.append((node.lineno, problem))
    return found


def python_findings() -> list[str]:
    findings: list[str] = []
    for path in _python_files():
        rel = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        findings += [f"{rel}:{line}: {problem}" for line, problem in _tree_problems(tree)]
    return findings


_QUOTED = re.compile(r"""(['"`])((?:(?!\1).){1,40})\1""")


def _website_problems(text: str) -> list[str]:
    """Naming lists hardcoded anywhere in one website source file."""
    problems: list[str] = []
    raw = {" ".join(m.group(2).split()) for m in _QUOTED.finditer(text)}
    quoted = {_fold(value) for value in raw}
    counties = quoted & COUNTY_WORDS
    if len(counties) >= 3:
        problems.append(f"county names {sorted(counties)[:4]}")
    if len(raw & WEBSITE_UTILITY_WORDS) >= 2:
        problems.append(f"utility names {sorted(raw & WEBSITE_UTILITY_WORDS)}")
    if quoted & TIER_WORDS == TIER_WORDS:
        problems.append("HFTD tier names")
    return problems


def website_findings() -> list[str]:
    findings: list[str] = []
    for path in sorted((ROOT / "website" / "src").rglob("*.ts*")):
        rel = path.relative_to(ROOT).as_posix()
        findings += [f"{rel}: {problem}" for problem in _website_problems(path.read_text(encoding="utf-8"))]
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
        # An Enum body is one collection, by member name or by value.
        "class U(str, Enum):\n    PGE = 'PGE'\n    SCE = 'SCE'": "utility names",
        "class U(enum.Enum):\n    PGE = 1\n    SDGE = 2": "utility names",
        # A regex needs no \\b or (?: to be a list; alternation is enough.
        r'X = re.compile(r"pge|sce|sdge")': "utility regex",
        r'X = r"\b(?:pg&e|sdg&e)\b"': "utility regex",
        r'X = r"\b(?:butte|shasta|san\s+luis\s+obispo)\b"': "county regex",
        r'X = rf"\b(?:pge|{extra}|sce)\b"': "utility regex",
        'X = {"VEG": "Vegetation", "UNK": "Unknown"}': "EPSS cause code map",
        'X = ("Wildfire", "Fire")': "CAL FIRE default incident types",
        "X = \"WHERE incident_type IN ('Wildfire', 'Fire')\"": "CAL FIRE default incident types",
        'X = {"incident_type": "Wildfire,Fire"}': "CAL FIRE default incident types",
    }
    for source, expected in samples.items():
        found = [problem for _, problem in _tree_problems(ast.parse(source))]
        assert any(expected in item for item in found), (source, found)

    website_samples = {
        "export const U = [\n  'PGE',\n  'SCE',\n];": "utility names",
        "const T = [\n  'Tier 2',\n  'Tier 3',\n];": "HFTD tier names",
        "const C = [\n  'Butte',\n  'Lake',\n  'Shasta',\n];": "county names",
    }
    for source, expected in website_samples.items():
        found = _website_problems(source)
        assert any(expected in item for item in found), (source, found)

    # A single name in logic is not a list, and one utility spelled two ways
    # in a regex is one utility.
    for source in (
        'if utility not in ("PGE", "untagged"): pass',
        r'X = r"\b(?:pge|pg\s*&\s*e|pacific gas)\b"',
        'X = "fire|wildfire|burn"',
        'X = ("wildfire", "fire")',
        'X = Query(..., description="cpuc_ignitions | epss_outages | psps_events")',
        'X = "Default incident_type IN (Wildfire, Fire); untyped excluded."',
    ):
        assert not _tree_problems(ast.parse(source)), source
