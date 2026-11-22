"""All costs here are scenario assumptions in basis points per leg."""


def net_bps(gross_bps, cost_bps, legs=1):
    return None if gross_bps is None else gross_bps - cost_bps * legs
