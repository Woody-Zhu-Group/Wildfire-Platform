"""The accounts requirement subset must not drift from the root requirements."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def requirements(path: Path) -> list[str]:
    lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def test_accounts_requirements_are_root_requirements():
    root = requirements(ROOT / "requirements.txt")
    missing = [line for line in requirements(ROOT / "services/accounts/requirements.txt") if line not in root]
    assert not missing, f"Not in requirements.txt exactly as written: {missing}"
