"""Turns raw API filings into clean, comparable records.

Lessons carried over from the FEC tool:
- Deduplicate before scoring (one record per EIN + tax period).
- Map fields per form type, because 990 / 990-EZ / 990-PF use different keys.
"""


def _field_map(fields_config, formtype):
    mapping = dict(fields_config.get("default", {}))
    mapping.update(fields_config.get(str(formtype), {}))
    return mapping


def _num(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _ratio(numerator, denominator):
    if numerator is None or not denominator or denominator <= 0:
        return None
    return numerator / denominator


def normalize_filing(raw, fields_config):
    """One raw filing -> a flat dict of the metrics we score on."""
    mapping = _field_map(fields_config, raw.get("formtype"))
    rec = {
        "tax_prd": raw.get("tax_prd"),
        "tax_year": raw.get("tax_prd_yr"),
        "formtype": raw.get("formtype"),
        "pdf_url": raw.get("pdf_url"),
    }
    for metric, key in mapping.items():
        rec[metric] = _num(raw.get(key))

    rec["officer_comp_share"] = _ratio(rec.get("officer_comp"), rec.get("expenses"))
    rec["fundraising_fee_share"] = _ratio(rec.get("fundraising_fees"), rec.get("contributions"))
    rec["expense_ratio"] = _ratio(rec.get("expenses"), rec.get("revenue"))
    return rec


def normalize_org(org_json, fields_config):
    """API organization response -> profile + deduplicated filings, newest first."""
    org = org_json.get("organization", {}) or {}
    seen = {}
    for raw in org_json.get("filings_with_data", []) or []:
        key = raw.get("tax_prd")
        if key is None:
            continue
        # Keep the most recently updated copy of each tax period.
        if key not in seen or (raw.get("updated") or "") > (seen[key].get("updated") or ""):
            seen[key] = raw
    filings = [normalize_filing(r, fields_config) for r in seen.values()]
    filings.sort(key=lambda f: f["tax_prd"], reverse=True)
    return {
        "ein": org.get("strein") or str(org.get("ein", "")),
        "name": org.get("name", ""),
        "state": org.get("state", ""),
        "ntee_code": org.get("ntee_code") or "",
        "filings": filings,
    }
