"""Elo, confidence intervals and SPRT over game scores.

Every strength claim in this repo has to carry its error bar, so these live
in one place rather than being re-derived per caller.

The SPRT is the Gaussian approximation: the log-likelihood ratio computed
from the sample mean and variance of the per-game scores, rather than the
BayesElo trinomial with a drawelo nuisance parameter. It agrees with the
trinomial form to well inside the noise at the sample sizes here and needs
no draw-rate model. Named so nobody later reports it as the fishtest
statistic and compares the two.
"""

import math


def elo(score):
    """Elo difference implied by an expected score in (0, 1)."""
    if score <= 0.0:
        return float("-inf")
    if score >= 1.0:
        return float("inf")
    return -400.0 * math.log10(1.0 / score - 1.0)


def score_of(elo_diff):
    """Inverse of elo(): expected score at a given Elo difference."""
    return 1.0 / (1.0 + 10.0 ** (-elo_diff / 400.0))


def score_stats(scores):
    """Mean, standard error and 95% CI of a list of per-game scores."""
    n = len(scores)
    if n == 0:
        return None
    mean = sum(scores) / n
    if n == 1:
        return {"n": 1, "mean": mean, "se": float("inf"),
                "lo": 0.0, "hi": 1.0}
    var = sum((s - mean) ** 2 for s in scores) / (n - 1)
    se = math.sqrt(var / n)
    return {"n": n, "mean": mean, "se": se,
            "lo": mean - 1.96 * se, "hi": mean + 1.96 * se}


def elo_with_ci(scores):
    """(elo, lower, upper, stats) from per-game scores. CI is on the score
    and then mapped through elo(), which is monotone, so the bounds stay
    correct without a delta-method approximation."""
    st = score_stats(scores)
    if st is None:
        return None
    lo = min(max(st["lo"], 1e-9), 1 - 1e-9)
    hi = min(max(st["hi"], 1e-9), 1 - 1e-9)
    return elo(st["mean"]), elo(lo), elo(hi), st


def sprt_llr(scores, elo0=0.0, elo1=4.0):
    """Log-likelihood ratio for H1: elo=elo1 against H0: elo=elo0.

    Gaussian approximation (see module docstring). Compare against
    log((1-beta)/alpha) and log(beta/(1-alpha)); at alpha=beta=0.05 those
    are +2.94 and -2.94.
    """
    st = score_stats(scores)
    if st is None or st["n"] < 2:
        return 0.0
    var = st["se"] ** 2 * st["n"]
    if var <= 0:
        return 0.0
    mu0, mu1 = score_of(elo0), score_of(elo1)
    return st["n"] * (mu1 - mu0) * (st["mean"] - (mu0 + mu1) / 2.0) / var


def sprt_verdict(llr, alpha=0.05, beta=0.05):
    """ACCEPT H1 / ACCEPT H0 / CONTINUE for an LLR."""
    upper = math.log((1 - beta) / alpha)
    lower = math.log(beta / (1 - alpha))
    if llr >= upper:
        return "ACCEPT H1"
    if llr <= lower:
        return "ACCEPT H0"
    return "CONTINUE"


def report(scores, elo0=0.0, elo1=4.0, label=""):
    """One formatted block: never a bare Elo, always the margin."""
    got = elo_with_ci(scores)
    if got is None:
        return "%s: no games" % (label or "result")
    e, lo, hi, st = got
    llr = sprt_llr(scores, elo0, elo1)
    wins = sum(1 for s in scores if s == 1.0)
    draws = sum(1 for s in scores if s == 0.5)
    losses = sum(1 for s in scores if s == 0.0)
    return "\n".join([
        "%sGames:   %d" % (label and label + "\n" or "", st["n"]),
        "Score:   %.4f +/- %.4f" % (st["mean"], 1.96 * st["se"]),
        "W/D/L:   %d / %d / %d" % (wins, draws, losses),
        "Elo:     %+.2f  [%+.2f, %+.2f]" % (e, lo, hi),
        "SPRT:    [%g,%g] LLR %+.3f -> %s"
        % (elo0, elo1, llr, sprt_verdict(llr)),
    ])
