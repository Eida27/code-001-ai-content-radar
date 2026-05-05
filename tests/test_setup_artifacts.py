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
    root_requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    dependency_lines = [
        line.strip()
        for line in requirements.splitlines()
        if line.strip() and not line.startswith("#")
    ]
    root_dependency_lines = [
        line.strip()
        for line in root_requirements.splitlines()
        if line.strip() and not line.startswith("#")
    ]

    assert dependency_lines
    assert all("==" in line for line in dependency_lines)
    assert root_dependency_lines == dependency_lines


def test_railway_cron_operational_limits_are_documented():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "UTC" in readme
    assert "minimum frequency is 5 minutes" in readme
    assert "skip the next scheduled run" in readme
    assert "idempotent" in readme


def test_production_env_defaults_and_railway_config_are_documented():
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    railway = (ROOT / "railway.toml").read_text(encoding="utf-8")

    assert "ALLOW_PAID_FALLBACK=false" in env_example
    assert "MAX_PAID_FALLBACKS_PER_RUN=0" in env_example
    assert "RUN_LOCK_ENABLED=true" in env_example
    assert "RUN_LOCK_TTL_SECONDS=840" in env_example
    assert "PRODUCTION_DB_WARNING_MB=350" in env_example
    assert "PRODUCTION_STORAGE_FAIL_MB=950" in env_example
    assert "python -m worker.production_check" in readme
    assert "repository root" in readme
    assert 'builder = "RAILPACK"' in railway
    assert 'startCommand = "python -m worker.main"' in railway
    assert 'cronSchedule = "*/15 * * * *"' in railway
    assert 'restartPolicyType = "NEVER"' in railway
    assert not (ROOT / "worker" / "railway.toml").exists()


def test_railway_python_runtime_is_pinned_and_documented():
    python_version = (ROOT / ".python-version").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert python_version.strip() == "3.13.2"
    assert ".python-version" in readme
    assert "Python 3.13.2" in readme


def test_reliable_unofficial_rss_allowlist_is_documented():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "Reliable Unofficial RSS Sources" in readme
    assert "verified_creator" in readme
    assert "reliable_forum" in readme
    assert "ai_aggregator" in readme
    assert "supabase/queries/source_allowlist_examples.sql" in readme
    assert "Do not auto-discover sources" in readme
