"""Combines rule flags and peer flags into a 0-10 review-priority score.

Uses soft saturation (10 * (1 - e^(-raw / s))) instead of a hard cap,
so high-scoring orgs still separate rather than all tying at 10.0.
"""
import math


def raw_points(org, peers_config):
    rule_pts = sum(f["points"] for f in org.get("rule_flags", []) + org.get("efile_flags", []))
    peer_pts = sum(f["z"] for f in org.get("peer_flags", [])) * peers_config.get("weight", 1.0)
    return rule_pts + peer_pts


def score(org, peers_config, scoring_config):
    raw = raw_points(org, peers_config)
    return round(10 * (1 - math.exp(-raw / scoring_config["saturation"])), 2)
