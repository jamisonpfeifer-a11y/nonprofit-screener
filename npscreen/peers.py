"""Peer comparison: how unusual is an org compared with similar orgs?

Peers share an NTEE major group (first letter of the NTEE code), a
revenue band, and a filer type (private foundations, which file the
990-PF, are only compared with each other). Uses robust z-scores (median + MAD) so a few extreme orgs
don't distort the baseline. Handles the MAD = 0 edge case found in the FEC tool.
"""
import statistics

MAD_SCALE = 1.4826  # makes MAD comparable to a standard deviation


def revenue_band(revenue, edges):
    if revenue is None:
        return None
    band = 0
    for i, edge in enumerate(edges):
        if revenue >= edge:
            band = i
    return band


def peer_key(org, edges):
    latest = org["filings"][0] if org.get("filings") else {}
    group = (org.get("ntee_code") or "?")[:1].upper()
    filer = "PF" if str(latest.get("formtype")) == "2" else "PC"
    return (group, revenue_band(latest.get("revenue"), edges), filer)


def robust_z(value, values):
    """Robust z-score of value against values. Returns 0.0 when undefined."""
    if value is None or len(values) < 2:
        return 0.0
    med = statistics.median(values)
    mad = statistics.median(abs(v - med) for v in values)
    if mad == 0:
        # Most peers are identical. Fall back to mean absolute deviation;
        # if that is also zero, there is no spread to measure against.
        mean_ad = statistics.fmean(abs(v - med) for v in values)
        if mean_ad == 0:
            return 0.0
        return (value - med) / (1.2533 * mean_ad)
    return (value - med) / (MAD_SCALE * mad)


def compare_peers(orgs, peers_config):
    """Adds a 'peer_flags' list to each org dict (mutates in place)."""
    edges = peers_config["revenue_bands"]
    floors = peers_config.get("min_value") or {}
    groups = {}
    for org in orgs:
        if org.get("filings"):
            groups.setdefault(peer_key(org, edges), []).append(org)

    for org in orgs:
        org["peer_flags"] = []
        org["peer_group_size"] = 0
        if not org.get("filings"):
            continue
        peers = groups.get(peer_key(org, edges), [])
        org["peer_group_size"] = len(peers)
        if len(peers) < peers_config["min_peers"]:
            continue
        latest = org["filings"][0]
        for metric in peers_config["metrics"]:
            value = latest.get(metric)
            values = [p["filings"][0].get(metric) for p in peers]
            values = [v for v in values if v is not None]
            if value is None or len(values) < peers_config["min_peers"]:
                continue
            if value < floors.get(metric, float("-inf")):
                continue  # statistically unusual but too small to matter
            z = robust_z(value, values)
            if z >= peers_config["z_threshold"]:  # only unusually HIGH values
                org["peer_flags"].append({
                    "metric": metric,
                    "z": round(min(z, peers_config["z_cap"]), 2),
                    "value": round(value, 3),
                    "peer_median": round(statistics.median(values), 3),
                })
    return orgs
