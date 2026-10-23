from npscreen.filings import normalize_org
from npscreen.rules import run_rules
from tests.conftest import make_filing, make_org


def flags_for(cfg, filings, ntee="P20"):
    org = normalize_org(make_org(1, filings, ntee=ntee), cfg["fields"])
    return {f["rule"] for f in run_rules(org, cfg["rules"])}


def test_clean_org_has_no_flags(cfg):
    assert flags_for(cfg, [make_filing(2022), make_filing(2021), make_filing(2020)]) == set()


def test_high_officer_comp(cfg):
    assert "officer_comp_share" in flags_for(cfg, [make_filing(2022, officer_comp=400_000)])


def test_small_orgs_skip_ratio_rules(cfg):
    tiny = make_filing(2022, revenue=50_000, expenses=40_000, officer_comp=30_000)
    assert "officer_comp_share" not in flags_for(cfg, [tiny])


def test_high_fundraising_fees(cfg):
    assert "fundraising_fee_share" in flags_for(cfg, [make_filing(2022, fundraising=400_000)])


def test_revenue_swing(cfg):
    assert "revenue_swing" in flags_for(cfg, [make_filing(2022, revenue=5_000_000), make_filing(2021, revenue=1_000_000)])


def test_revenue_swing_skips_gap_years(cfg):
    # 2017 -> 2023 is not a year-over-year change.
    filings = [make_filing(2023, revenue=5_000_000), make_filing(2017, revenue=1_000_000)]
    assert "revenue_swing" not in flags_for(cfg, filings)


def test_revenue_swing_skips_negative_revenue(cfg):
    filings = [make_filing(2023, revenue=-1_800_000), make_filing(2022, revenue=2_000_000)]
    assert "revenue_swing" not in flags_for(cfg, filings)


def test_revenue_swing_skips_startup_ramp_up(cfg):
    # $6K -> $1.4M is a new org getting going, not a swing.
    filings = [make_filing(2023, revenue=1_400_000), make_filing(2022, revenue=6_000)]
    assert "revenue_swing" not in flags_for(cfg, filings)


def test_revenue_swing_still_catches_real_drop(cfg):
    filings = [make_filing(2023, revenue=400_000), make_filing(2022, revenue=2_000_000)]
    assert "revenue_swing" in flags_for(cfg, filings)


def test_persistent_deficit(cfg):
    years = [make_filing(y, revenue=1_000_000, expenses=1_200_000) for y in (2022, 2021, 2020)]
    assert "persistent_deficit" in flags_for(cfg, years)


def test_liabilities_exceed_assets(cfg):
    assert "liabilities_exceed_assets" in flags_for(cfg, [make_filing(2022, assets=100, liabilities=200)])


def test_housing_orgs_skip_balance_sheet_rules(cfg):
    years = [make_filing(y, revenue=1_000_000, expenses=1_200_000, assets=100, liabilities=200)
             for y in (2022, 2021, 2020)]
    assert flags_for(cfg, years, ntee="L22") & {"persistent_deficit", "liabilities_exceed_assets"} == set()
    # Same numbers for a non-housing org still flag.
    assert {"persistent_deficit", "liabilities_exceed_assets"} <= flags_for(cfg, years, ntee="P20")
