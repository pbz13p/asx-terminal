"""Multiple-testing gates for every research conclusion (Frank 2026-11-20). Pure numpy/scipy-free maths.

Three gates (all must pass for ✅):
  1. hlz(t)            Harvey, Liu & Zhu (2016): |t| (clustered by date) >= 3, not 2, once many things were tried.
  2. deflated_sharpe   Bailey & López de Prado (2014): probability that the true Sharpe is above the best Sharpe
                       you would expect from N_trials worthless strategies. Pass >= 0.95.
  3. pbo_cscv          Bailey, Borwein, López de Prado & Zhu (2017): probability of backtest overfitting by
                       combinatorially symmetric cross-validation on a (periods x configurations) return matrix,
                       S = 16 blocks. PBO > 0.5 = "the winner was picked by luck".
label(...) turns the three results into ✅ / ⚠️ / ❌ with a one-line reason.
Every new round registers its trial count in research/trials.json BEFORE the gates are run (CLAUDE.md).
"""
import itertools
import math

import numpy as np

EULER = 0.5772156649015329


def norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def norm_ppf(p):
    """Inverse normal CDF (Acklam's approximation, |error| < 1.2e-9)."""
    if not 0 < p < 1:
        raise ValueError("p must be in (0, 1)")
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02, 1.383577518672690e+02,
         -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02, 6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00, -2.549732539343734e+00,
         4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00]
    lo = 0.02425
    if p < lo:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > 1 - lo:
        return -norm_ppf(1 - p)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
        (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


# ------------------------------------------------------------------ gate 1
def hlz(t, bar=3.0):
    return t is not None and abs(t) >= bar


def clustered_t(values, clusters):
    """t of the mean, one observation per cluster (e.g. date or month): mean of cluster means / se."""
    by = {}
    for v, c in zip(values, clusters):
        by.setdefault(c, []).append(v)
    m = np.array([np.mean(x) for x in by.values()])
    if len(m) < 3 or m.std(ddof=1) == 0:
        return None
    return float(m.mean() / (m.std(ddof=1) / math.sqrt(len(m))))


# ------------------------------------------------------------------ gate 2
def expected_max_sharpe(n_trials, var_sr):
    """Expected maximum of n_trials Sharpe estimates when all true Sharpes are zero."""
    if n_trials <= 1:
        return 0.0
    return math.sqrt(var_sr) * ((1 - EULER) * norm_ppf(1 - 1 / n_trials) + EULER * norm_ppf(1 - 1 / (n_trials * math.e)))


def deflated_sharpe(returns, n_trials, var_sr=None):
    """returns: per-period strategy returns (net, non-overlapping). Sharpe is per period (not annualised).
    var_sr: variance of the Sharpe estimates across the trials; unknown -> null value 1/(T-1)."""
    r = np.asarray([x for x in returns if x is not None and np.isfinite(x)], dtype=float)
    T = len(r)
    if T < 10 or r.std(ddof=1) == 0:
        return {"T": T, "dsr": None}
    sr = float(r.mean() / r.std(ddof=1))
    z = (r - r.mean()) / r.std(ddof=0)
    skew, kurt = float(np.mean(z ** 3)), float(np.mean(z ** 4))
    v = var_sr if var_sr is not None else 1 / (T - 1)
    sr0 = expected_max_sharpe(max(1, int(n_trials)), v)
    den = 1 - skew * sr + (kurt - 1) / 4 * sr * sr
    stat = (sr - sr0) * math.sqrt(T - 1) / math.sqrt(max(den, 1e-12))
    return {"T": T, "sr": round(sr, 4), "sr_annual_hint": None, "skew": round(skew, 2), "kurt": round(kurt, 2),
            "sr0": round(sr0, 4), "n_trials": int(n_trials), "dsr": round(norm_cdf(stat), 4)}


# ------------------------------------------------------------------ gate 3
def pbo_cscv(M, S=16, metric="sharpe"):
    """M: array (T periods x N configurations) of per-period returns. Returns PBO and the logit distribution summary."""
    M = np.asarray(M, dtype=float)
    M = np.nan_to_num(M)
    T, N = M.shape
    if N < 2 or T < S:
        return {"pbo": None, "N": N, "T": T}
    blocks = np.array_split(np.arange(T), S)

    def perf(X):
        if metric == "mean":
            return X.mean(axis=0)
        sd = X.std(axis=0, ddof=1)
        return np.where(sd > 0, X.mean(axis=0) / np.where(sd > 0, sd, 1), 0)

    lam = []
    for combo in itertools.combinations(range(S), S // 2):
        tr = np.concatenate([blocks[k] for k in combo])
        te = np.concatenate([blocks[k] for k in range(S) if k not in combo])
        p_is, p_os = perf(M[tr]), perf(M[te])
        best = int(np.argmax(p_is))
        rank = (p_os < p_os[best]).sum() + 0.5 * ((p_os == p_os[best]).sum() - 1) + 1   # 1 = worst, N = best
        w = rank / (N + 1)
        lam.append(math.log(w / (1 - w)))
    lam = np.array(lam)
    return {"pbo": round(float((lam <= 0).mean()), 3), "N": int(N), "T": int(T), "S": S, "splits": len(lam),
            "logit_median": round(float(np.median(lam)), 2)}


# ------------------------------------------------------------------ verdict
def label(t, dsr=None, pbo=None, t_bar=3.0, dsr_bar=0.95, pbo_bar=0.5):
    fails, notes = [], []
    if not hlz(t, t_bar):
        fails.append(f"t {t if t is not None else '无'} < {t_bar}")
    if dsr is None:
        notes.append("去偏夏普算不出")
    elif dsr < dsr_bar:
        fails.append(f"去偏夏普 {dsr:.2f} < {dsr_bar}")
    if pbo is not None and pbo > pbo_bar:
        fails.append(f"PBO {pbo:.2f} > {pbo_bar}")
    if not fails and dsr is not None:
        return "✅", "三关都过" + ("" if pbo is not None else "（没有参数网格，PBO 不适用）")
    hard = (t is None or abs(t) < 2) or (dsr is not None and dsr < 0.5) or (pbo is not None and pbo > 0.7)
    return ("❌" if hard else "⚠️"), "；".join(fails + notes)


if __name__ == "__main__":
    # self-tests
    rng = np.random.default_rng(0)
    assert abs(norm_ppf(0.975) - 1.959964) < 1e-5 and abs(norm_cdf(1.959964) - 0.975) < 1e-6
    # pure noise grid: PBO should be around 0.5; a real edge in one column: PBO near 0
    noise = rng.normal(0, 0.01, (192, 30))
    p_noise = pbo_cscv(noise)["pbo"]
    edge = noise.copy()
    edge[:, 7] += 0.004
    p_edge = pbo_cscv(edge)["pbo"]
    assert 0.25 < p_noise < 0.8, p_noise
    assert p_edge < 0.1, p_edge
    # DSR: strong real edge passes with few trials, a lucky noise winner fails with many
    good = rng.normal(0.01, 0.03, 240)
    assert deflated_sharpe(good, 10)["dsr"] > 0.95
    best_noise = max((rng.normal(0, 0.03, 120) for _ in range(500)), key=lambda x: x.mean() / x.std())
    assert deflated_sharpe(best_noise, 500)["dsr"] < 0.95
    assert label(3.5, 0.97, 0.2)[0] == "✅" and label(2.4, 0.9, None)[0] == "⚠️" and label(1.2, 0.3, 0.8)[0] == "❌"
    print("validate.py self-tests ok | noise PBO", p_noise, "| edge PBO", p_edge)
