"""Guard rails for the design: the rules engine must stay provider-independent."""
import pathlib
import re

APP = pathlib.Path(__file__).resolve().parents[1] / "app"


def test_rules_package_never_imports_providers():
    rules = APP / "rules"
    if not rules.exists():  # rules engine arrives in Phase 5; this then guards it
        return
    for path in rules.rglob("*.py"):
        assert not re.search(r"(from|import)\s+app\.providers", path.read_text()), path


def test_internal_data_classes_have_no_provider_specific_imports():
    text = (APP / "providers" / "models.py").read_text()
    assert "httpx" not in text and "sportmonks" not in text.lower()
