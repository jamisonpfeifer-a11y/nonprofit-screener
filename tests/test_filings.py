from npscreen.filings import normalize_org
from tests.conftest import make_filing, make_org


def test_deduplicates_tax_periods_keeping_latest_update(cfg):
    old = make_filing(2022, revenue=100, updated="2023-01-01")
    new = make_filing(2022, revenue=999, updated="2024-01-01")
    org = normalize_org(make_org(1, [old, new]), cfg["fields"])
    assert len(org["filings"]) == 1
    assert org["filings"][0]["revenue"] == 999


def test_filings_sorted_newest_first(cfg):
    org = normalize_org(make_org(1, [make_filing(2020), make_filing(2022), make_filing(2021)]), cfg["fields"])
    assert [f["tax_year"] for f in org["filings"]] == [2022, 2021, 2020]


def test_private_foundation_uses_different_comp_field(cfg):
    pf = make_filing(2022, officer_comp=50_000, expenses=100_000, formtype=2)
    org = normalize_org(make_org(1, [pf]), cfg["fields"])
    assert org["filings"][0]["officer_comp_share"] == 0.5


def test_ratios_handle_zero_and_missing(cfg):
    raw = make_filing(2022, expenses=0, contributions=None)
    f = normalize_org(make_org(1, [raw]), cfg["fields"])["filings"][0]
    assert f["officer_comp_share"] is None
    assert f["fundraising_fee_share"] is None
