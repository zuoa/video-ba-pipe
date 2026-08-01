import os


DEFAULT_COMPANY_NAME = "码全科技"


def get_company_name() -> str:
    company_name = os.environ.get("COMPANY_NAME", "").strip()
    return company_name or DEFAULT_COMPANY_NAME
