from npscreen.filings import normalize_org
from npscreen.peers import compare_peers, robust_z
from npscreen.scoring import score
from tests.conftest import make_filing, make_org


def test_mad_zero_does_not_explode():
    # Most peers identical: must not return infinity or auto-max.
    z = robust_z(0.5, [0.1, 0.1, 0.1, 0.1, 0.1, 0.5])
    assert 0 < z < 100
    assert robust_z(0.1, [0.1] * 6) == 0.0


def test_peer_outlier_is_flagged(cfg):
    orgs = [normalize_org(make_org(i, [make_filing(2022, officer_comp=90_000 + i * 1000)]), cfg["fields"])
            for i in range(8)]
    orgs.append(normalize_org(make_org(99, [make_filing(2022, officer_comp=450_000)]), cfg["fields"]))
    compare_peers(orgs, cfg["peers"])
    outlier = orgs[-1]
    assert any(f["metric"] == "officer_comp_share" for f in outlier["peer_flags"])
    assert not orgs[0]["peer_flags"]


def test_tiny_values_below_floor_are_not_flagged(cfg):
    # Most peers report $0 fundraising fees; one reports a trivial amount (0.1%).
    orgs = [normalize_org(make_org(i, [make_filing(2022, fundraising=0)]), cfg["fields"]) for i in range(8)]
    orgs.append(normalize_org(make_org(99, [make_filing(2022, fundraising=800)]), cfg["fields"]))
    compare_peers(orgs, cfg["peers"])
    assert not any(f["metric"] == "fundraising_fee_share" for f in orgs[-1]["peer_flags"])


def test_private_foundations_have_own_peer_group(cfg):
    # 8 public charities + 1 private foundation in the same NTEE group and band.
    orgs = [normalize_org(make_org(i, [make_filing(2022)]), cfg["fields"]) for i in range(8)]
    orgs.append(normalize_org(make_org(99, [make_filing(2022, formtype=2, officer_comp=450_000)]), cfg["fields"]))
    compare_peers(orgs, cfg["peers"])
    pf = orgs[-1]
    assert pf["peer_group_size"] == 1      # not compared with the public charities
    assert pf["peer_flags"] == []
    assert orgs[0]["peer_group_size"] == 8


def test_small_peer_groups_are_skipped(cfg):
    orgs = [normalize_org(make_org(i, [make_filing(2022)]), cfg["fields"]) for i in range(3)]
    compare_peers(orgs, cfg["peers"])
    assert all(o["peer_flags"] == [] for o in orgs)


def test_scores_separate_instead_of_capping(cfg):
    high = {"rule_flags": [{"points": 3}] * 4, "peer_flags": []}
    higher = {"rule_flags": [{"points": 3}] * 6, "peer_flags": []}
    s1, s2 = score(high, cfg["peers"], cfg["scoring"]), score(higher, cfg["peers"], cfg["scoring"])
    assert s1 < s2 < 10


def test_clean_org_scores_zero(cfg):
    assert score({"rule_flags": [], "peer_flags": []}, cfg["peers"], cfg["scoring"]) == 0.0
