"""E-file XML parsing, rules, and fetching. All offline, using a fictional return."""
import copy
import io
import struct
import zipfile
import zlib
from pathlib import Path

from npscreen.efile import EfileClient, parse_return, read_zip_member, run_efile_rules

FIXTURE = (Path(__file__).parent / "fixtures" / "efile_990_fictional.xml").read_bytes()


def rules_for(cfg, parsed):
    return {f["rule"] for f in run_efile_rules(parsed, cfg["efile"]["rules"])}


def test_parse_fields():
    r = parse_return(FIXTURE)
    assert r["tax_period_end"] == "2025-06-30"
    assert r["family_or_business_relationship"] is True
    assert r["ceo_comp_review"] is False
    assert r["conflict_policy"] is True
    assert r["comp_based_on_revenue"] is True and r["comp_based_on_net_earnings"] is False
    assert (r["w2_count"], r["forms_1099_count"]) == (2, 30)


def test_parse_officers_sorted_with_total_comp():
    officers = parse_return(FIXTURE)["officers"]
    assert officers[0]["name"] == "JANE DOE"
    assert officers[0]["compensation"] == 310_000   # reportable + other compensation


def test_parse_loans_direction_and_agreement():
    loans = parse_return(FIXTURE)["insider_loans"]
    assert [l["direction"] for l in loans] == ["to_org", "from_org"]
    assert loans[0]["balance"] == 80_000 and loans[0]["written_agreement"] is False


def test_missing_schedules_are_none_not_false():
    r = parse_return(FIXTURE.replace(b"<IRS990ScheduleJ>", b"<Other>").replace(b"</IRS990ScheduleJ>", b"</Other>"))
    assert r["comp_based_on_revenue"] is None


def test_fictional_return_triggers_all_rules(cfg):
    assert rules_for(cfg, parse_return(FIXTURE)) == {
        "contingent_comp", "insider_loans", "governance_gaps", "contractor_heavy"}


def test_clean_return_triggers_nothing(cfg):
    r = parse_return(FIXTURE)
    r.update(family_or_business_relationship=False, ceo_comp_review=True, whistleblower_policy=True,
             document_retention_policy=True, fs_audited=True, comp_based_on_revenue=False,
             insider_loans=[], w2_count=40)
    assert rules_for(cfg, r) == set()


def test_net_earnings_scores_higher_than_revenue(cfg):
    r = parse_return(FIXTURE)
    by_revenue = run_efile_rules(r, cfg["efile"]["rules"])
    r["comp_based_on_net_earnings"] = True
    by_earnings = run_efile_rules(r, cfg["efile"]["rules"])
    pts = lambda flags: next(f["points"] for f in flags if f["rule"] == "contingent_comp")
    assert pts(by_earnings) > pts(by_revenue)


def test_repaid_loans_do_not_flag(cfg):
    r = parse_return(FIXTURE)
    for loan in r["insider_loans"]:
        loan["balance"] = 0
    assert "insider_loans" not in rules_for(cfg, r)


def test_small_orgs_not_expected_to_have_audits(cfg):
    r = parse_return(FIXTURE)
    r["total_expenses"] = 200_000
    flags = run_efile_rules(r, cfg["efile"]["rules"])
    detail = next(f["detail"] for f in flags if f["rule"] == "governance_gaps")
    assert "audited" not in detail


class FakeResponse:
    def __init__(self, content, status=200):
        self.content, self.status_code = content, status

    def raise_for_status(self):
        assert self.status_code == 200


class FakeSession:
    def __init__(self, index_csv):
        self.index_csv, self.calls = index_csv, []

    def get(self, url, timeout=None):
        self.calls.append(url)
        if url.endswith("index_2025.csv"):
            return FakeResponse(self.index_csv.encode())
        return FakeResponse(b"", status=404)


def make_client(tmp_path, cfg):
    index = ("RETURN_ID,FILING_TYPE,EIN,TAX_PERIOD,SUB_DATE,TAXPAYER_NAME,RETURN_TYPE,DLN,OBJECT_ID,XML_BATCH_ID\n"
             ",EFILE,000000001,202406,2025,EXAMPLE HELPERS INC,990,1,111,2025_TEOS_XML_01A\n"
             ",EFILE,000000001,202506,2025,EXAMPLE HELPERS INC,990,2,222,2025_TEOS_XML_02A\n"
             ",EFILE,000000001,202506,2025,EXAMPLE HELPERS INC,990T,3,333,2025_TEOS_XML_02A\n")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("222_public.xml", FIXTURE)
    opened = []

    def opener(url):
        opened.append(url)
        return zipfile.ZipFile(io.BytesIO(buf.getvalue()))

    efile_cfg = copy.deepcopy(cfg["efile"])
    efile_cfg.update(cache_dir=str(tmp_path), index_years=[2026, 2025])
    session = FakeSession(index)
    return EfileClient(efile_cfg, session=session, zip_opener=opener), session, opened


def test_client_picks_latest_990_and_skips_other_forms(tmp_path, cfg):
    client, _, _ = make_client(tmp_path, cfg)
    row = client.latest_990("00-0000001".replace("-", ""))
    assert row["OBJECT_ID"] == "222"            # newest period, Form 990 (not 990-T)
    assert client.latest_990("999999999") is None


def test_client_fetches_from_zip_then_uses_cache(tmp_path, cfg):
    client, _, opened = make_client(tmp_path, cfg)
    row = client.latest_990("000000001")
    assert parse_return(client.get_xml(row))["tax_period_end"] == "2025-06-30"
    client.get_xml(row)
    assert len(opened) == 1                      # second read came from the cache
    assert (tmp_path / "xml" / "222.xml").exists()
    assert opened[0].endswith("/2025/2025_TEOS_XML_02A.zip")


def test_unpublished_index_year_is_skipped(tmp_path, cfg):
    client, session, _ = make_client(tmp_path, cfg)
    client.latest_990("000000001")
    assert any(u.endswith("index_2026.csv") for u in session.calls)   # 404, ignored


def deflate64_zip(name, payload):
    """Build a one-file zip whose entry uses Deflate64 (method 9), like some IRS archives."""
    import inflate64
    d = inflate64.Deflater()
    comp = d.deflate(payload) + d.flush()
    crc, fname = zlib.crc32(payload), name.encode()
    local = struct.pack("<IHHHHHIIIHH", 0x04034B50, 20, 0, 9, 0, 0, crc, len(comp), len(payload), len(fname), 0)
    central = struct.pack("<IHHHHHHIIIHHHHHII", 0x02014B50, 20, 20, 0, 9, 0, 0, crc, len(comp),
                          len(payload), len(fname), 0, 0, 0, 0, 0, 0)
    body = local + fname + comp
    cd = central + fname
    end = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, 1, 1, len(cd), len(body), 0)
    return body + cd + end


def test_reads_deflate64_entries():
    zf = zipfile.ZipFile(io.BytesIO(deflate64_zip("222_public.xml", FIXTURE)))
    assert zf.getinfo("222_public.xml").compress_type == 9
    assert read_zip_member(zf, "222_public.xml") == FIXTURE
