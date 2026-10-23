"""Command-line entry point.

  python -m npscreen inspect 142007220          # see raw field names for one org
  python -m npscreen org 142007220              # rule check for one org
  python -m npscreen screen --state NY --ntee 5 --pages 2 --out results.csv
  python -m npscreen org 142007220 --efile      # add full e-file XML checks
  python -m npscreen screen --state VT --ntee 5 --pages 8 --efile-top 10
"""
import argparse
import csv
import json
import sys

from npscreen.client import NonprofitClient
from npscreen.efile import EfileClient, parse_return, run_efile_rules
from npscreen.config import load_config
from npscreen.filings import normalize_org
from npscreen.peers import compare_peers
from npscreen.rules import run_rules
from npscreen.scoring import score

DISCLAIMER = ("Scores rank filings for human review. A flag is not evidence "
              "of wrongdoing; check the underlying Form 990 before drawing conclusions.")


def cmd_inspect(client, cfg, args):
    data = client.get_org(args.ein)
    if not data:
        sys.exit(f"No organization found for EIN {args.ein}")
    filings = data.get("filings_with_data") or []
    print(f"{data['organization'].get('name')}: {len(filings)} filings with data")
    if filings:
        latest = max(filings, key=lambda f: f.get("tax_prd") or 0)
        print(f"\nFields in latest filing ({latest.get('tax_prd_yr')}, formtype {latest.get('formtype')}):")
        for key in sorted(latest):
            print(f"  {key}: {latest[key]}")


def cmd_org(client, cfg, args):
    data = client.get_org(args.ein)
    if not data:
        sys.exit(f"No organization found for EIN {args.ein}")
    org = normalize_org(data, cfg["fields"])
    flags = run_rules(org, cfg["rules"])
    print(f"{org['name']} ({org['ein']}), {org['state']}, NTEE {org['ntee_code']}")
    print(f"Filings with data: {len(org['filings'])}")
    if not flags:
        print("No rule-based indicators triggered.")
    for f in flags:
        print(f"  [{f['rule']}] {f['detail']}")
    if args.efile:
        print_efile(EfileClient(cfg["efile"]), cfg, org["ein"])
    print(f"\n{DISCLAIMER}")


def fetch_efile(efile_client, cfg, ein):
    """(parsed return, flags) for an EIN's latest e-filed 990, or (None, [])."""
    row = efile_client.latest_990(ein)
    if row is None:
        return None, []
    parsed = parse_return(efile_client.get_xml(row))
    parsed["object_id"] = row["OBJECT_ID"]
    return parsed, run_efile_rules(parsed, cfg["efile"]["rules"])


def _yn(value):
    return {True: "Yes", False: "No", None: "-"}[value]


def print_efile(efile_client, cfg, ein):
    print("\nFull e-file return (IRS XML):")
    parsed, flags = fetch_efile(efile_client, cfg, ein)
    if parsed is None:
        print("  No e-filed Form 990 found in the configured IRS index years.")
        return
    print(f"  Tax period ending {parsed['tax_period_end']} (object ID {parsed['object_id']})")
    print("  Top compensated:")
    for o in parsed["officers"][:5]:
        hours = f"{o['hours']:.0f} hrs/wk" if o["hours"] is not None else ""
        print(f"    {o['name'][:30]:<30} {o['title'][:22]:<22} ${o['compensation']:>11,.0f}  {hours}")
    print(f"  Pay tied to revenue / net earnings:  {_yn(parsed['comp_based_on_revenue'])} / "
          f"{_yn(parsed['comp_based_on_net_earnings'])}")
    print(f"  Family or business relationships:   {_yn(parsed['family_or_business_relationship'])}")
    print(f"  Independent review of CEO pay:      {_yn(parsed['ceo_comp_review'])}")
    print(f"  Conflict / whistleblower / records: {_yn(parsed['conflict_policy'])} / "
          f"{_yn(parsed['whistleblower_policy'])} / {_yn(parsed['document_retention_policy'])}")
    print(f"  Audited / compiled or reviewed:     {_yn(parsed['fs_audited'])} / "
          f"{_yn(parsed['fs_compiled_or_reviewed'])}")
    w2, f1099 = parsed["w2_count"], parsed["forms_1099_count"]
    print(f"  W-2 employees / Forms 1099:         {w2 if w2 is None else int(w2)} / "
          f"{f1099 if f1099 is None else int(f1099)}")
    for loan in parsed["insider_loans"]:
        direction = "to the org from" if loan["direction"] == "to_org" else "from the org to"
        print(f"  Loan {direction} {loan['person']} ({loan['purpose'].lower()}): "
              f"balance ${loan['balance']:,.0f}, written agreement {_yn(loan['written_agreement'])}")
    if not flags:
        print("  No e-file indicators triggered.")
    for f in flags:
        print(f"  [{f['rule']}] {f['detail']}")


def cmd_screen(client, cfg, args):
    eins = []
    for page in range(args.pages):
        result = client.search(q=args.q, state=args.state, ntee=args.ntee,
                               c_code=args.c_code, page=page)
        if not result or not result.get("organizations"):
            break
        eins += [o["ein"] for o in result["organizations"]]
        if page + 1 >= result.get("num_pages", 0):
            break
    print(f"Found {len(eins)} organizations. Pulling filings...")

    orgs = []
    for i, ein in enumerate(eins, 1):
        try:
            data = client.get_org(ein)
            if data:
                orgs.append(normalize_org(data, cfg["fields"]))
        except Exception as err:  # one bad org shouldn't kill the batch
            print(f"  skipped {ein}: {err}", file=sys.stderr)
        if i % 25 == 0:
            print(f"  {i}/{len(eins)}")

    orgs = [o for o in orgs if o["filings"]]
    for org in orgs:
        org["rule_flags"] = run_rules(org, cfg["rules"])
    compare_peers(orgs, cfg["peers"])
    for org in orgs:
        org["score"] = score(org, cfg["peers"], cfg["scoring"])
    orgs.sort(key=lambda o: o["score"], reverse=True)

    if args.efile_top:
        # Full XML only for the top of the list: each return is a separate download.
        efile_client = EfileClient(cfg["efile"])
        candidates = [o for o in orgs if o["score"] > 0][:args.efile_top]
        print(f"Checking full e-file returns for the top {len(candidates)}...")
        for org in candidates:
            try:
                parsed, flags = fetch_efile(efile_client, cfg, org["ein"])
            except Exception as err:  # one bad return shouldn't kill the batch
                print(f"  skipped e-file for {org['ein']}: {err}", file=sys.stderr)
                continue
            org["efile_tax_period"] = parsed["tax_period_end"] if parsed else ""
            org["efile_flags"] = flags
            org["score"] = score(org, cfg["peers"], cfg["scoring"])
        orgs.sort(key=lambda o: o["score"], reverse=True)

    write_csv(orgs, args.out)
    print(f"\nTop {min(args.top, len(orgs))} by review priority:")
    for org in orgs[:args.top]:
        reasons = ([f["rule"] for f in org["rule_flags"]]
                   + [f["metric"] + " (peer)" for f in org["peer_flags"]]
                   + [f["rule"] + " (e-file)" for f in org.get("efile_flags", [])])
        print(f"  {org['score']:>5}  {org['name'][:45]:<45} {', '.join(reasons) or '-'}")
    print(f"\nFull results: {args.out}\n{DISCLAIMER}")


def write_csv(orgs, path):
    cols = ["score", "ein", "name", "state", "ntee_code", "tax_year", "formtype",
            "revenue", "expenses", "officer_comp_share", "fundraising_fee_share",
            "peer_group_size", "rule_flags", "peer_flags", "efile_tax_period", "efile_flags", "pdf_url"]
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for o in orgs:
            f = o["filings"][0]
            w.writerow({
                "score": o["score"], "ein": o["ein"], "name": o["name"],
                "state": o["state"], "ntee_code": o["ntee_code"],
                "tax_year": f.get("tax_year"), "formtype": f.get("formtype"),
                "revenue": f.get("revenue"), "expenses": f.get("expenses"),
                "officer_comp_share": f.get("officer_comp_share"),
                "fundraising_fee_share": f.get("fundraising_fee_share"),
                "peer_group_size": o.get("peer_group_size", 0),
                "rule_flags": "; ".join(x["detail"] for x in o["rule_flags"]),
                "peer_flags": json.dumps(o["peer_flags"]),
                "efile_tax_period": o.get("efile_tax_period", ""),
                "efile_flags": "; ".join(x["detail"] for x in o.get("efile_flags", [])),
                "pdf_url": f.get("pdf_url"),
            })


def main(argv=None):
    parser = argparse.ArgumentParser(prog="npscreen", description="Screen Form 990 data for review indicators.")
    parser.add_argument("--config", help="path to config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("inspect", help="show raw field names for one org")
    p.add_argument("ein")

    p = sub.add_parser("org", help="run rule checks on one org")
    p.add_argument("ein")
    p.add_argument("--efile", action="store_true", help="also check the full e-file XML from the IRS")

    p = sub.add_parser("screen", help="screen a batch of orgs from a search")
    p.add_argument("--q", help="keyword search")
    p.add_argument("--state", help="two-letter state code, e.g. NY")
    p.add_argument("--ntee", type=int, help="NTEE major group 1-10 (e.g. 5 = Human Services)")
    p.add_argument("--c-code", type=int, default=3, help="501(c) subsection (default 3)")
    p.add_argument("--pages", type=int, default=2, help="search pages to pull (25 orgs each)")
    p.add_argument("--top", type=int, default=15)
    p.add_argument("--efile-top", type=int, default=0,
                   help="check full e-file XML for the N highest-scoring orgs (default 0 = off)")
    p.add_argument("--out", default="results.csv")

    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    client = NonprofitClient(cfg["api"])
    {"inspect": cmd_inspect, "org": cmd_org, "screen": cmd_screen}[args.command](client, cfg, args)


if __name__ == "__main__":
    main()
