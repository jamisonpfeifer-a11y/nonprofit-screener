# Nonprofit Screener

A command-line tool that screens public Form 990 data for financial patterns that may warrant a closer look: unusually high officer compensation, heavy reliance on professional fundraisers, sharp revenue swings, and organizations that look out of step with similar peers.

> **Responsible use.** Scores rank organizations for human review. A flag is not evidence of wrongdoing. Always read the underlying Form 990 (linked in the output) before drawing conclusions.

## How it works

1. **Pull data** from ProPublica's [Nonprofit Explorer API](https://projects.propublica.org/nonprofits/api). No API key needed.
2. **Normalize filings**: deduplicate by tax period and map fields by form type (990, 990-EZ, 990-PF use different keys).
3. **Rule checks**: configurable thresholds in `config.yaml`.
4. **Peer comparison**: robust z-scores (median and MAD) against organizations in the same NTEE group and revenue band.
5. **Score**: a 0-10 review priority with soft saturation, so top results don't tie at the maximum.

| Indicator | What it looks at |
|---|---|
| `officer_comp_share` | Officer compensation as a share of total expenses |
| `fundraising_fee_share` | Professional fundraising fees as a share of contributions |
| `revenue_swing` | Large year-over-year revenue changes |
| `persistent_deficit` | Expenses above revenue for several consecutive years |
| `liabilities_exceed_assets` | Year-end liabilities greater than assets |

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

```bash
# See the raw field names in one organization's latest filing
python -m npscreen inspect 142007220

# Run rule checks on one organization
python -m npscreen org 142007220

# Screen a batch: New York human services 501(c)(3)s, 2 pages (50 orgs)
python -m npscreen screen --state NY --ntee 5 --pages 2 --out results.csv
```

NTEE groups: 1 Arts, 2 Education, 3 Environment/Animals, 4 Health, 5 Human Services, 6 International, 7 Public/Societal Benefit, 8 Religion, 9 Mutual/Membership, 10 Unclassified.

## Tests

```bash
python -m pytest
```

Tests run offline using synthetic filings.

## Limitations

- ProPublica's structured financial data comes from IRS annual extracts and can lag recent filing years. Check the `tax_year` column.
- Summary data covers a subset of the full Form 990. Related-party transactions (Schedule L) and named officer pay require the full e-file XML, which is a planned extension.
- Ratios are skipped for very small organizations, where they're too noisy to mean much.
- Thresholds are starting points and need tuning against real filings.
- Some rules are skipped for housing and retirement communities (NTEE codes starting `L2`), where mortgages and resident entrance fees make deficits and liabilities above assets routine. This relies on NTEE codes, which are sometimes inaccurate: a retirement community coded under senior services (P75) will still be flagged.

## Data source

ProPublica Nonprofit Explorer API, drawing on the IRS Exempt Organizations Business Master File and the IRS Annual Extract of Tax-Exempt Organization Financial Data. Use is subject to ProPublica's Data Terms of Use.

## License

MIT
