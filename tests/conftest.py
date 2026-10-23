import pytest
from npscreen.config import load_config


@pytest.fixture
def cfg():
    return load_config()


def make_filing(year, revenue=1_000_000, expenses=900_000, officer_comp=100_000,
                contributions=800_000, fundraising=20_000, assets=2_000_000,
                liabilities=500_000, formtype=0, updated="2020-01-01"):
    comp_key = "compofficers" if formtype == 2 else "compnsatncurrofcr"
    return {
        "tax_prd": year * 100 + 12, "tax_prd_yr": year, "formtype": formtype,
        "updated": updated, "pdf_url": None,
        "totrevenue": revenue, "totfuncexpns": expenses,
        "totassetsend": assets, "totliabend": liabilities,
        comp_key: officer_comp, "totcntrbgfts": contributions,
        "profndraising": fundraising,
    }


def make_org(ein, filings, ntee="P20", state="NY", name=None):
    return {
        "organization": {"ein": ein, "strein": str(ein), "name": name or f"ORG {ein}",
                         "state": state, "ntee_code": ntee},
        "filings_with_data": filings,
    }
