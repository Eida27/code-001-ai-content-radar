from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_python_venv_setup_is_documented_and_ignored():
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert ".venv/" in gitignore
    assert "python -m venv .venv" in readme
    assert ".\\.venv\\Scripts\\Activate.ps1" in readme
    assert "Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass" in readme
    assert "python -m pip install --upgrade pip" in readme
    assert "python -m pip install -r worker/requirements.txt" in readme


def test_requirements_are_pinned():
    requirements = (ROOT / "worker" / "requirements.txt").read_text(encoding="utf-8")
    dependency_lines = [
        line.strip()
        for line in requirements.splitlines()
        if line.strip() and not line.startswith("#")
    ]

    assert dependency_lines
    assert all("==" in line for line in dependency_lines)


def test_railway_cron_operational_limits_are_documented():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "UTC" in readme
    assert "minimum frequency is 5 minutes" in readme
    assert "skip the next scheduled run" in readme
    assert "idempotent" in readme
