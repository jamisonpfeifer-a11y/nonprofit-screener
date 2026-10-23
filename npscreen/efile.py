"""Full Form 990 e-file XML from the IRS: fetch, parse, and governance rules.

Summary data (ProPublica) can't see Schedule J, Schedule L, or most of
Part VI. The IRS publishes every e-filed return as XML:

- A yearly index CSV maps EIN -> OBJECT_ID and the monthly zip (XML_BATCH_ID).
- Each zip holds tens of thousands of returns and is very large, so we read
  single files out of it with HTTP range requests (remotezip) instead of
  downloading the whole archive.
- Index files and extracted returns are cached under .cache/irs/.

Only Form 990 is parsed for now (not 990-EZ or 990-PF).
"""
import csv
import io
import struct
import time
import zlib
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

NS = "{http://www.irs.gov/efile}"
DEFLATE64 = 9   # zip compression method some IRS archives use; Python's zipfile can't read it
TRUE_VALUES = {"1", "true", "x"}
FALSE_VALUES = {"0", "false"}


# -- fetching ---------------------------------------------------------------

class EfileClient:
    def __init__(self, efile_config, session=None, zip_opener=None):
        self.base_url = efile_config["base_url"].rstrip("/")
        self.index_years = efile_config.get("index_years", [])
        self.max_index_age = efile_config.get("index_max_age_days", 30) * 86400
        self.cache_dir = Path(efile_config.get("cache_dir", ".cache/irs"))
        self.timeout = efile_config.get("timeout_seconds", 60)
        self.session = session or requests.Session()
        self._zip_opener = zip_opener      # lets tests supply a local zip
        self._index = None                 # EIN -> list of index rows
        self._zips = {}                    # open remote zips, reused per run

    def latest_990(self, ein):
        """Index row for the most recent Form 990 an EIN e-filed, or None."""
        rows = self._load_index().get(str(ein).replace("-", ""), [])
        if not rows:
            return None
        return max(rows, key=lambda r: (r["TAX_PERIOD"], r["OBJECT_ID"]))

    def get_xml(self, row):
        """Raw XML bytes for one index row (cached after the first fetch)."""
        cache = self.cache_dir / "xml" / f"{row['OBJECT_ID']}.xml"
        if cache.exists():
            return cache.read_bytes()
        zf = self._open_zip(row)
        wanted = f"{row['OBJECT_ID']}_public.xml"
        name = next((n for n in zf.namelist() if n.endswith(wanted)), None)
        if name is None:
            raise LookupError(f"{wanted} not found in {row['XML_BATCH_ID']}")
        data = read_zip_member(zf, name)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(data)
        return data

    # -- internals ------------------------------------------------------

    def _load_index(self):
        if self._index is not None:
            return self._index
        index = {}
        for year in self.index_years:
            path = self._index_file(year)
            if path is None:
                continue
            with open(path, newline="", encoding="utf-8", errors="replace") as fh:
                for row in csv.DictReader(fh):
                    if row.get("RETURN_TYPE") == "990" and row.get("XML_BATCH_ID"):
                        row["_year"] = year
                        index.setdefault(row["EIN"], []).append(row)
        self._index = index
        return index

    def _index_file(self, year):
        path = self.cache_dir / f"index_{year}.csv"
        fresh = path.exists() and time.time() - path.stat().st_mtime < self.max_index_age
        if fresh:
            return path
        url = f"{self.base_url}/{year}/index_{year}.csv"
        resp = self.session.get(url, timeout=self.timeout)
        if resp.status_code == 404:
            return path if path.exists() else None   # year not published yet
        resp.raise_for_status()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(resp.content)
        return path

    def _open_zip(self, row):
        batch = row["XML_BATCH_ID"]
        if batch not in self._zips:
            url = f"{self.base_url}/{row['_year']}/{batch}.zip"
            if self._zip_opener:
                self._zips[batch] = self._zip_opener(url)
            else:
                from remotezip import RemoteZip   # imported lazily; only needed online
                self._zips[batch] = RemoteZip(url)
        return self._zips[batch]


def read_zip_member(zf, name):
    """Read one file from a zip, including Deflate64 entries zipfile can't handle."""
    info = zf.getinfo(name)
    if info.compress_type != DEFLATE64:
        return zf.read(name)
    import inflate64
    # Skip the local file header (30 bytes + name + extra), then inflate the raw bytes.
    zf.fp.seek(info.header_offset)
    header = zf.fp.read(30)
    name_len, extra_len = struct.unpack("<HH", header[26:30])
    zf.fp.seek(info.header_offset + 30 + name_len + extra_len)
    data = inflate64.Inflater().inflate(zf.fp.read(info.compress_size))
    if zlib.crc32(data) != info.CRC:
        raise ValueError(f"CRC mismatch reading {name}")
    return data


# -- parsing ----------------------------------------------------------------

def _text(el, name):
    found = el.find(f".//{NS}{name}") if el is not None else None
    return (found.text or "").strip() if found is not None else None


def _bool(el, name):
    value = _text(el, name)
    if value is None:
        return None
    value = value.lower()
    if value in TRUE_VALUES:
        return True
    if value in FALSE_VALUES:
        return False
    return None


def _num(el, name):
    value = _text(el, name)
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


def _person(el):
    return (_text(el, "PersonNm") or _text(el, "BusinessNameLine1Txt") or "").strip()


def parse_return(xml_bytes):
    """Form 990 XML -> flat dict of the fields our rules use."""
    root = ET.parse(io.BytesIO(xml_bytes)).getroot()
    header = root.find(f"{NS}ReturnHeader")
    f990 = root.find(f".//{NS}IRS990")
    sched_j = root.find(f".//{NS}IRS990ScheduleJ")
    sched_l = root.find(f".//{NS}IRS990ScheduleL")

    officers = []
    for grp in root.iter(f"{NS}Form990PartVIISectionAGrp"):
        comp = sum(_num(grp, k) or 0 for k in (
            "ReportableCompFromOrgAmt", "ReportableCompFromRltdOrgAmt", "OtherCompensationAmt"))
        officers.append({
            "name": _person(grp),
            "title": _text(grp, "TitleTxt") or "",
            "hours": _num(grp, "AverageHoursPerWeekRt"),
            "compensation": comp,
        })
    officers.sort(key=lambda o: o["compensation"], reverse=True)

    loans = []
    for grp in (sched_l.iter(f"{NS}LoansBtwnOrgInterestedPrsnGrp") if sched_l is not None else []):
        loans.append({
            "person": _person(grp),
            "relationship": _text(grp, "RelationshipWithOrgTxt") or "",
            "purpose": _text(grp, "LoanPurposeTxt") or "",
            "direction": "to_org" if _bool(grp, "LoanToOrganizationInd") else "from_org",
            "original": _num(grp, "OriginalPrincipalAmt"),
            "balance": _num(grp, "BalanceDueAmt") or 0,
            "approved": _bool(grp, "BoardOrCommitteeApprovalInd"),
            "written_agreement": _bool(grp, "WrittenAgreementInd"),
        })

    return {
        "ein": _text(header, "EIN"),
        "return_type": _text(header, "ReturnTypeCd"),
        "tax_period_end": _text(header, "TaxPeriodEndDt"),
        "total_expenses": _num(f990, "CYTotalExpensesAmt"),
        "total_revenue": _num(f990, "CYTotalRevenueAmt"),
        # Part V
        "forms_1099_count": _num(f990, "IRPDocumentCnt"),
        "w2_count": _num(f990, "EmployeeCnt"),
        # Part VI
        "family_or_business_relationship": _bool(f990, "FamilyOrBusinessRlnInd"),
        "ceo_comp_review": _bool(f990, "CompensationProcessCEOInd"),
        "conflict_policy": _bool(f990, "ConflictOfInterestPolicyInd"),
        "whistleblower_policy": _bool(f990, "WhistleblowerPolicyInd"),
        "document_retention_policy": _bool(f990, "DocumentRetentionPolicyInd"),
        # Part XII
        "fs_audited": _bool(f990, "FSAuditedInd"),
        "fs_compiled_or_reviewed": _bool(f990, "AccountantCompileOrReviewInd"),
        # Schedule J
        "comp_based_on_revenue": _bool(sched_j, "CompBasedOnRevenueOfFlngOrgInd"),
        "comp_based_on_net_earnings": _bool(sched_j, "CompBsdNetEarnsFlngOrgInd"),
        # Schedule L and Part VII
        "insider_loans": loans,
        "officers": officers,
    }


# -- rules ------------------------------------------------------------------

def _flag(rule, points, detail):
    return {"rule": rule, "points": points, "detail": detail}


def contingent_comp(r, cfg):
    if r["comp_based_on_net_earnings"]:
        return _flag("contingent_comp", cfg["net_earnings_points"],
                     "Officer pay is contingent on net earnings (Schedule J line 6a)")
    if r["comp_based_on_revenue"]:
        return _flag("contingent_comp", cfg["revenue_points"],
                     "Officer pay is contingent on revenue (Schedule J line 5a)")
    return None


def insider_loans(r, cfg):
    open_loans = [l for l in r["insider_loans"] if l["balance"] > 0]
    if not open_loans:
        return None
    total = sum(l["balance"] for l in open_loans)
    unwritten = sum(1 for l in open_loans if l["written_agreement"] is False)
    detail = f"{len(open_loans)} open loan(s) with insiders, ${total:,.0f} outstanding (Schedule L)"
    if unwritten:
        detail += f"; {unwritten} without a written agreement"
    return _flag("insider_loans", cfg["points"], detail)


def governance_gaps(r, cfg):
    gaps = []
    if r["family_or_business_relationship"]:
        gaps.append("family/business relationships among officers")
    if r["ceo_comp_review"] is False:
        gaps.append("no independent review of CEO pay")
    if r["conflict_policy"] is False:
        gaps.append("no conflict-of-interest policy")
    if r["whistleblower_policy"] is False:
        gaps.append("no whistleblower policy")
    if r["document_retention_policy"] is False:
        gaps.append("no document retention policy")
    if (r["fs_audited"] is False and r["fs_compiled_or_reviewed"] is False
            and (r["total_expenses"] or 0) >= cfg["audit_expense_floor"]):
        gaps.append("financial statements neither audited nor reviewed")
    if len(gaps) < cfg["min_gaps"]:
        return None
    return _flag("governance_gaps", cfg["points"], "Governance: " + "; ".join(gaps) + " (Part VI/XII)")


def contractor_heavy(r, cfg):
    forms_1099, w2 = r["forms_1099_count"], r["w2_count"]
    if forms_1099 is None or w2 is None or forms_1099 < cfg["min_1099"]:
        return None
    if forms_1099 >= cfg["ratio"] * max(w2, 1):
        return _flag("contractor_heavy", cfg["points"],
                     f"{forms_1099:.0f} Forms 1099 vs {w2:.0f} W-2 employees (Part V)")
    return None


EFILE_RULES = {
    "contingent_comp": contingent_comp,
    "insider_loans": insider_loans,
    "governance_gaps": governance_gaps,
    "contractor_heavy": contractor_heavy,
}


def run_efile_rules(parsed, rules_config):
    flags = []
    for name, fn in EFILE_RULES.items():
        if name in rules_config:
            result = fn(parsed, rules_config[name])
            if result:
                flags.append(result)
    return flags
