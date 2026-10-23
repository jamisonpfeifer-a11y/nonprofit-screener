"""Rule-based review indicators for a single organization.

Each rule returns a flag dict or None. A flag means "worth a look,"
never "wrongdoing." Rules skip quietly when the data they need is missing.
"""


def _flag(rule, points, detail):
    return {"rule": rule, "points": points, "detail": detail}


def officer_comp_share(filings, cfg, min_revenue):
    f = filings[0]
    share = f.get("officer_comp_share")
    if share is None or (f.get("revenue") or 0) < min_revenue:
        return None
    if share >= cfg["threshold"]:
        return _flag("officer_comp_share", cfg["points"],
                     f"Officer compensation is {share:.0%} of expenses ({f['tax_year']})")
    return None


def fundraising_fee_share(filings, cfg, min_revenue):
    f = filings[0]
    share = f.get("fundraising_fee_share")
    if share is None or (f.get("revenue") or 0) < min_revenue:
        return None
    if share >= cfg["threshold"]:
        return _flag("fundraising_fee_share", cfg["points"],
                     f"Professional fundraising fees equal {share:.0%} of contributions ({f['tax_year']})")
    return None


def _filing_year(f):
    if f.get("tax_year"):
        return int(f["tax_year"])
    if f.get("tax_prd"):
        return int(f["tax_prd"]) // 100
    return None


def revenue_swing(filings, cfg, min_revenue):
    if len(filings) < 2:
        return None
    new, old = filings[0].get("revenue"), filings[1].get("revenue")
    # Only compare back-to-back years; a gap makes the ratio meaningless.
    y_new, y_old = _filing_year(filings[0]), _filing_year(filings[1])
    if y_new is None or y_old is None or y_new - y_old != 1:
        return None
    # Negative revenue (e.g. investment losses) has no meaningful ratio.
    if new is None or old is None or new <= 0 or old <= 0:
        return None
    # Skip startups ramping up from near zero.
    if old < cfg.get("min_prior_revenue", 0) or max(new, old) < min_revenue:
        return None
    change = new / old
    if change >= cfg["up_ratio"] or change <= cfg["down_ratio"]:
        return _flag("revenue_swing", cfg["points"],
                     f"Revenue moved {change:.1f}x between {filings[1]['tax_year']} and {filings[0]['tax_year']}")
    return None


def persistent_deficit(filings, cfg, min_revenue):
    n = cfg["years"]
    recent = filings[:n]
    if len(recent) < n:
        return None
    if all((f.get("expenses") or 0) > (f.get("revenue") or 0) > 0 for f in recent):
        return _flag("persistent_deficit", cfg["points"],
                     f"Expenses exceeded revenue in each of the last {n} filings")
    return None


def liabilities_exceed_assets(filings, cfg, min_revenue):
    f = filings[0]
    assets, liabilities = f.get("assets"), f.get("liabilities")
    if assets is None or liabilities is None or liabilities <= 0:
        return None
    if liabilities > assets:
        return _flag("liabilities_exceed_assets", cfg["points"],
                     f"Liabilities exceed assets at year-end {f['tax_year']}")
    return None


RULES = {
    "officer_comp_share": officer_comp_share,
    "fundraising_fee_share": fundraising_fee_share,
    "revenue_swing": revenue_swing,
    "persistent_deficit": persistent_deficit,
    "liabilities_exceed_assets": liabilities_exceed_assets,
}


def run_rules(org, rules_config):
    """Run every configured rule against an org's filings (newest first)."""
    filings = org.get("filings") or []
    if not filings:
        return []
    min_revenue = rules_config.get("min_revenue", 0)
    ntee = (org.get("ntee_code") or "").upper()
    flags = []
    for name, fn in RULES.items():
        if name in rules_config:
            # Some rules are routine for certain sectors (prefix match on NTEE code).
            skip = rules_config[name].get("skip_ntee") or []
            if any(ntee.startswith(p.upper()) for p in skip):
                continue
            result = fn(filings, rules_config[name], min_revenue)
            if result:
                flags.append(result)
    return flags
