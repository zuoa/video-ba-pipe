from app.branding import DEFAULT_COMPANY_NAME, get_company_name


def test_company_name_defaults_when_environment_is_missing(monkeypatch):
    monkeypatch.delenv("COMPANY_NAME", raising=False)

    assert get_company_name() == DEFAULT_COMPANY_NAME


def test_company_name_uses_trimmed_environment_value(monkeypatch):
    monkeypatch.setenv("COMPANY_NAME", "  示例科技  ")

    assert get_company_name() == "示例科技"


def test_company_name_defaults_when_environment_is_blank(monkeypatch):
    monkeypatch.setenv("COMPANY_NAME", "   ")

    assert get_company_name() == DEFAULT_COMPANY_NAME
