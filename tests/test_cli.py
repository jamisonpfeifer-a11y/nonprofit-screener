"""End-to-end screen run with a fake client (no network)."""
import csv

from npscreen import cli
from tests.conftest import make_filing, make_org


class FakeClient:
    def __init__(self, orgs):
        self.orgs = {o["organization"]["ein"]: o for o in orgs}

    def search(self, **kwargs):
        return {"organizations": [{"ein": e} for e in self.orgs], "num_pages": 1}

    def get_org(self, ein):
        if ein == 13:
            raise RuntimeError("simulated network failure")
        return self.orgs.get(ein) or self.orgs.get(int(ein))   # CLI passes EINs as strings


def test_screen_end_to_end(tmp_path, monkeypatch, capsys):
    orgs = [make_org(i, [make_filing(2022), make_filing(2021)]) for i in range(10)]
    orgs.append(make_org(50, [make_filing(2022, officer_comp=450_000), make_filing(2021)], name="OUTLIER ORG"))
    orgs.append(make_org(13, [make_filing(2022)]))  # will fail; batch should continue
    monkeypatch.setattr(cli, "NonprofitClient", lambda api_cfg: FakeClient(orgs))

    out = tmp_path / "results.csv"
    cli.main(["screen", "--out", str(out)])

    rows = list(csv.DictReader(open(out)))
    assert len(rows) == 11
    assert rows[0]["name"] == "OUTLIER ORG"
    assert "not evidence of wrongdoing" in capsys.readouterr().out


class FakeEfileClient:
    """Returns the fictional return for EIN 50 only."""
    def __init__(self, efile_cfg):
        pass

    def latest_990(self, ein):
        return {"OBJECT_ID": "222"} if str(ein) == "50" else None

    def get_xml(self, row):
        from tests.test_efile import FIXTURE
        return FIXTURE


def test_screen_with_efile_top(tmp_path, monkeypatch):
    orgs = [make_org(i, [make_filing(2022), make_filing(2021)]) for i in range(10)]
    orgs.append(make_org(50, [make_filing(2022, officer_comp=450_000), make_filing(2021)], name="OUTLIER ORG"))
    monkeypatch.setattr(cli, "NonprofitClient", lambda api_cfg: FakeClient(orgs))
    monkeypatch.setattr(cli, "EfileClient", FakeEfileClient)

    out = tmp_path / "results.csv"
    cli.main(["screen", "--out", str(out), "--efile-top", "5"])

    top = list(csv.DictReader(open(out)))[0]
    assert top["name"] == "OUTLIER ORG"
    assert top["efile_tax_period"] == "2025-06-30"
    assert "Schedule L" in top["efile_flags"]


def test_org_with_efile_prints_detail(monkeypatch, capsys):
    org = make_org(50, [make_filing(2022)], name="OUTLIER ORG")
    monkeypatch.setattr(cli, "NonprofitClient", lambda api_cfg: FakeClient([org]))
    monkeypatch.setattr(cli, "EfileClient", FakeEfileClient)
    cli.main(["org", "50", "--efile"])
    out = capsys.readouterr().out
    assert "Tax period ending 2025-06-30" in out
    assert "[insider_loans]" in out
