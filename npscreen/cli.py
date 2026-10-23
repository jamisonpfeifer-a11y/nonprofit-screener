"""Command-line entry point.

  python -m npscreen inspect 142007220          # see raw field names for one org
  python -m npscreen org 142007220              # rule check for one org
  python -m npscreen screen --state NY --ntee 5 --pages 2 --out results.csv
"""
import argparse
import csv
import json
import sys

from npscreen.client import NonprofitClient
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
    print(f"\n{DISCLAIMER}")


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

    write_csv(orgs, args.out)
    print(f"\nTop {min(args.top, len(orgs))} by review priority:")
    for org in orgs[:args.top]:
        reasons = [f["rule"] for f in org["rule_flags"]] + [f["metric"] + " (peer)" for f in org["peer_flags"]]
        print(f"  {org['score']:>5}  {org['name'][:45]:<45} {', '.join(reasons) or '-'}")
    print(f"\nFull results: {args.out}\n{DISCLAIMER}")


def write_csv(orgs, path):
    cols = ["score", "ein", "name", "state", "ntee_code", "tax_year", "formtype",
            "revenue", "expenses", "officer_comp_share", "fundraising_fee_share",
            "peer_group_size", "rule_flags", "peer_flags", "pdf_url"]
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

    p = sub.add_parser("screen", help="screen a batch of orgs from a search")
    p.add_argument("--q", help="keyword search")
    p.add_argument("--state", help="two-letter state code, e.g. NY")
    p.add_argument("--ntee", type=int, help="NTEE major group 1-10 (e.g. 5 = Human Services)")
    p.add_argument("--c-code", type=int, default=3, help="501(c) subsection (default 3)")
    p.add_argument("--pages", type=int, default=2, help="search pages to pull (25 orgs each)")
    p.add_argument("--top", type=int, default=15)
    p.add_argument("--out", default="results.csv")

    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    client = NonprofitClient(cfg["api"])
    {"inspect": cmd_inspect, "org": cmd_org, "screen": cmd_screen}[args.command](client, cfg, args)


if __name__ == "__main__":
    main()
