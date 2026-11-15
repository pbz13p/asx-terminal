"""Formula engine for public alpha libraries (round 77).

Reads formulas written in the papers' own notation and evaluates them on wide price panels (rows = days,
columns = stocks) with numpy / bottleneck. One engine covers:
  - GTJA (国泰君安) "191 short-period price-volume alphas" (2017 report) notation: RANK, DELAY, SMA(A,n,m), WMA, DECAYLINEAR,
    REGBETA(A, SEQUENCE(n), n), COUNT, SUMIF, HIGHDAY/LOWDAY, cond ? a : b, ^, &&, ||, =, OR ...
  - WorldQuant "101 Formulaic Alphas" (Kakushadze 2016) notation: rank, ts_rank, correlation, decay_linear, scale,
    SignedPower, Ts_ArgMax, adv20, min(x,d)/max(x,d) as time-series ...
  - Microsoft Qlib Alpha158 notation (MIT): $close, Ref, Mean, Std, Greater, Less, Slope, Rsquare, Resi, Quantile, IdxMax ...
A formula that needs data we do not have (industry classes, market cap, Fama-French factors, its own past value)
raises Unsupported and is recorded as skipped with the reason.
"""
import re

import bottleneck as bn
import numpy as np
import pandas as pd


class Unsupported(Exception):
    pass


# ---------------- parser ----------------
TOKEN = re.compile(r"\s*(?:((?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?)|(\$?[A-Za-z_][A-Za-z_0-9.]*)|(\|\||&&|<=|>=|==|!=|[-+*/^(),?:<>=&|]))")


def tokenize(s):
    s = (s.replace("，", ",").replace("（", "(").replace("）", ")").replace("？", "?").replace("：", ":")
         .replace("–", "-").replace("self.", ""))
    out, i = [], 0
    s = s.strip()
    while i < len(s):
        m = TOKEN.match(s, i)
        if not m or m.end() == i:
            raise SyntaxError(f"bad char at {i}: {s[i:i + 10]!r}")
        num, name, op = m.groups()
        if num is not None:
            out.append(("num", float(num)))
        elif name is not None:
            low = name.lower().lstrip("$")
            low = {"vol": "volume", "l": "low", "hgih": "high", "ma": "mean", "delat": "delta"}.get(low, low)
            if low in ("or",):
                out.append(("op", "||"))
            elif low in ("and",):
                out.append(("op", "&&"))
            else:
                out.append(("name", low))
        else:
            out.append(("op", {"&": "&&", "|": "||", "=": "=="}.get(op, op)))
        i = m.end()
        while i < len(s) and s[i].isspace():
            i += 1
    return out


class Parser:
    """Precedence: ?: < || < && < comparisons < + - < * / < ^ < unary < atom."""

    def __init__(self, s):
        self.t = tokenize(s)
        self.i = 0

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else (None, None)

    def eat(self, v=None):
        tok = self.peek()
        if v is not None and tok[1] != v:
            raise SyntaxError(f"expected {v} got {tok}")
        self.i += 1
        return tok

    def parse(self):
        e = self.ternary()
        if self.i != len(self.t):
            raise SyntaxError(f"trailing tokens {self.t[self.i:self.i + 4]}")
        return e

    def ternary(self):
        c = self.orx()
        if self.peek()[1] == "?":
            self.eat("?")
            a = self.ternary()
            self.eat(":")
            b = self.ternary()
            return ("if", c, a, b)
        return c

    def binary(self, nxt, ops):
        e = nxt()
        while self.peek()[0] == "op" and self.peek()[1] in ops:
            op = self.eat()[1]
            e = (op, e, nxt())
        return e

    def orx(self):
        return self.binary(self.andx, ("||",))

    def andx(self):
        return self.binary(self.cmp, ("&&",))

    def cmp(self):
        return self.binary(self.add, ("<", ">", "<=", ">=", "==", "!="))

    def add(self):
        return self.binary(self.mul, ("+", "-"))

    def mul(self):
        return self.binary(self.unary, ("*", "/"))

    def unary(self):
        if self.peek()[1] == "-":
            self.eat()
            return ("neg", self.unary())
        if self.peek()[1] == "+":
            self.eat()
            return self.unary()
        return self.power()

    def power(self):
        b = self.atom()
        if self.peek()[1] == "^":
            self.eat()
            return ("^", b, self.unary())
        return b

    def atom(self):
        kind, v = self.eat()
        if kind == "num":
            return ("num", v)
        if kind == "name":
            if self.peek()[1] == "(":
                self.eat("(")
                args = []
                if self.peek()[1] != ")":
                    args.append(self.ternary())
                    while self.peek()[1] == ",":
                        self.eat(",")
                        args.append(self.ternary())
                self.eat(")")
                return ("call", v, args)
            return ("var", v)
        if v == "(":
            e = self.ternary()
            self.eat(")")
            return e
        raise SyntaxError(f"unexpected {kind} {v}")


# ---------------- numeric helpers (axis 0 = time) ----------------
def _w(d):
    d = int(d)
    if d < 1:
        raise Unsupported("window < 1")
    return d


def shift(x, d):
    d = int(d)
    if d == 0:
        return x
    out = np.full_like(x, np.nan)
    out[d:] = x[:-d]
    return out


def mmean(x, d):
    return bn.move_mean(x, _w(d), axis=0)


def msum(x, d):
    return bn.move_sum(x, _w(d), axis=0)


def mstd(x, d):
    return bn.move_std(x, _w(d), axis=0, ddof=1) if _w(d) > 1 else np.zeros_like(x)


def both(x, y):
    m = np.isnan(x) | np.isnan(y)
    if m.any():
        x, y = np.where(m, np.nan, x), np.where(m, np.nan, y)
    return x, y


def mcov(x, y, d):
    x, y = both(x, y)
    return mmean(x * y, d) - mmean(x, d) * mmean(y, d)


def mcorr(x, y, d):
    x, y = both(x, y)
    cov = mmean(x * y, d) - mmean(x, d) * mmean(y, d)
    vx = mmean(x * x, d) - mmean(x, d) ** 2
    vy = mmean(y * y, d) - mmean(y, d) ** 2
    with np.errstate(all="ignore"):
        r = cov / np.sqrt(vx * vy)
    r[(vx <= 1e-12) | (vy <= 1e-12)] = np.nan
    return np.clip(r, -1, 1)


def tsrank(x, d):
    return (bn.move_rank(x, _w(d), axis=0) + 1) / 2


def tsmax(x, d):
    return bn.move_max(x, _w(d), axis=0)


def tsmin(x, d):
    return bn.move_min(x, _w(d), axis=0)


def days_since_max(x, d):
    return bn.move_argmax(x, _w(d), axis=0) + 1


def days_since_min(x, d):
    return bn.move_argmin(x, _w(d), axis=0) + 1


def weighted(x, w):
    w = np.asarray(w, dtype="float64")
    w = w / w.sum()
    out = np.zeros_like(x)
    for k, wk in enumerate(w[::-1]):        # w[-1] applies to today
        out = out + shift(x, k) * wk
    return out


def decay_linear(x, d):
    d = _w(d)
    return weighted(x, np.arange(1, d + 1))


def wma(x, d):
    d = _w(d)
    return weighted(x, 0.9 ** np.arange(d - 1, -1, -1))


def sma(x, n, m):
    a = float(m) / float(n)
    return pd.DataFrame(x).ewm(alpha=a, adjust=False, ignore_na=True).mean().values * np.where(np.isnan(x), np.nan, 1)


def cs_rank(x):
    return pd.DataFrame(x).rank(axis=1, pct=True).values


def scale(x, a=1.0):
    s = np.nansum(np.abs(x), axis=1, keepdims=True)
    with np.errstate(all="ignore"):
        return x / s * a


def prod(x, d):
    d = _w(d)
    with np.errstate(all="ignore"):
        la = msum(np.log(np.abs(x)), d)
        neg = msum((x < 0).astype(float) * np.where(np.isnan(x), np.nan, 1), d)
    return np.exp(la) * np.where(neg % 2 == 1, -1, 1)


def regbeta(y, x, d):
    return mcov(y, x, d) / (mmean(x * x, d) - mmean(x, d) ** 2)


def regresi(y, x, d):
    b = regbeta(y, x, d)
    a = mmean(y, d) - b * mmean(x, d)
    return y - (a + b * x)


def rquantile(x, d, q):
    return pd.DataFrame(x).rolling(_w(d)).quantile(float(q)).values


def skew(x, d):
    return pd.DataFrame(x).rolling(_w(d)).skew().values


# ---------------- evaluator ----------------
class Engine:
    def __init__(self, base):
        """base: dict of 2-D arrays: open high low close volume rawclose bench_close bench_open (bench as column vectors)."""
        self.b = base
        T = base["close"].shape[0]
        self.tt = np.arange(T, dtype="float64")[:, None] * np.ones((1, base["close"].shape[1]))
        self.cache = {}

    def var(self, n):
        if n in self.cache:
            return self.cache[n]
        b = self.b
        c, o, h, l = b["close"], b["open"], b["high"], b["low"]
        if n in ("open", "high", "low", "close", "volume"):
            v = b[n]
        elif n == "vwap":
            v = (h + l + c) / 3          # no turnover value in EODHD data: typical price stands in for VWAP
        elif n == "amount":
            v = b["volume"] * (h + l + c) / 3
        elif n in ("returns", "ret"):
            v = c / shift(c, 1) - 1
        elif n.startswith("adv") and n[3:].isdigit():
            v = mmean(self.var("amount"), int(n[3:]))
        elif n in ("banchmarkindexclose", "benchmarkindexclose"):
            v = b["bench_close"] * np.ones_like(c)
        elif n in ("banchmarkindexopen", "benchmarkindexopen"):
            v = b["bench_open"] * np.ones_like(c)
        elif n == "rawclose":
            v = b["rawclose"]
        elif n == "dtm":
            v = np.where(o <= shift(o, 1), 0, np.maximum(h - o, o - shift(o, 1)))
        elif n == "dbm":
            v = np.where(o >= shift(o, 1), 0, np.maximum(o - l, o - shift(o, 1)))
        elif n == "tr":
            pc = shift(c, 1)
            v = np.maximum(np.maximum(h - l, np.abs(h - pc)), np.abs(l - pc))
        elif n == "hd":
            v = h - shift(h, 1)
        elif n == "ld":
            v = shift(l, 1) - l
        else:
            raise Unsupported(f"no data for {n}")
        self.cache[n] = v
        return v

    def run(self, formula):
        with np.errstate(all="ignore"):
            r = self.ev(Parser(formula).parse())
        if np.isscalar(r):
            raise Unsupported("constant")
        r = np.array(r, dtype="float64")
        r[~np.isfinite(r)] = np.nan
        return r

    def num(self, e):
        if e[0] == "num":
            return e[1]
        if e[0] == "neg" and e[1][0] == "num":
            return -e[1][1]
        return None

    def ev(self, e):
        k = e[0]
        if k == "num":
            return e[1]
        if k == "var":
            return self.var(e[1])
        if k == "neg":
            return -self.ev(e[1])
        if k == "if":
            c = self.ev(e[1])
            return np.where(np.asarray(c, dtype=bool), self.ev(e[2]), self.ev(e[3]))
        if k == "call":
            return self.call(e[1], e[2])
        a, b = self.ev(e[1]), self.ev(e[2])
        if k == "+":
            return a + b
        if k == "-":
            return a - b
        if k == "*":
            return a * b
        if k == "/":
            return a / b
        if k == "^":
            return np.power(a, b)
        if k == "<":
            return (a < b) * 1.0
        if k == ">":
            return (a > b) * 1.0
        if k == "<=":
            return (a <= b) * 1.0
        if k == ">=":
            return (a >= b) * 1.0
        if k == "==":
            return (a == b) * 1.0
        if k == "!=":
            return (a != b) * 1.0
        if k == "&&":
            return ((a != 0) & (b != 0)) * 1.0
        if k == "||":
            return ((a != 0) | (b != 0)) * 1.0
        raise Unsupported(f"op {k}")

    def call(self, f, args):
        n = len(args)
        if f in ("indneutralize", "regresi_ff", "sumac") or f.startswith("indclass"):
            raise Unsupported(f"needs {f}")
        if f == "sequence":
            raise Unsupported("bare SEQUENCE")
        # REGBETA / REGRESI against SEQUENCE(n): slope against time
        if f in ("regbeta", "regresi", "slope", "resi", "rsquare"):
            y = self.ev(args[0])
            if f in ("slope", "resi", "rsquare"):
                d = self.num(args[1])
                x = self.tt
            else:
                if args[1][0] == "call" and args[1][1] == "sequence":
                    x, d = self.tt, self.num(args[1][2][0])
                else:
                    x, d = self.ev(args[1]), self.num(args[2]) if n > 2 else None
                if n > 2:
                    d = self.num(args[2])
            if d is None:
                raise Unsupported("regression window")
            if f in ("regbeta", "slope"):
                return regbeta(y, x, d)
            if f == "rsquare":
                return mcorr(y, x, d) ** 2
            return regresi(y, x, d)
        if f == "filter":
            raise Unsupported("FILTER")
        v = [self.ev(a) for a in args]
        sc = [self.num(a) for a in args]
        if f == "rank":
            return tsrank(v[0], sc[1]) if n == 2 else cs_rank(v[0])
        if f in ("ts_rank", "tsrank"):
            return tsrank(v[0], sc[1])
        if f in ("delay", "ref"):
            return shift(v[0], sc[1] if n > 1 else 1)
        if f == "delta":
            return v[0] - shift(v[0], sc[1] if n > 1 else 1)
        if f in ("log",):
            return np.log(v[0])
        if f == "abs":
            return np.abs(v[0])
        if f == "sign":
            return np.sign(v[0])
        if f in ("signedpower",):
            return np.sign(v[0]) * np.abs(v[0]) ** v[1]
        if f == "sum":
            if n < 2:
                raise Unsupported("SUM without window")
            return msum(v[0], sc[1])
        if f == "mean":
            return mmean(v[0], sc[1])
        if f in ("std", "stddev"):
            return mstd(v[0], sc[1])
        if f in ("corr", "correlation"):
            return mcorr(v[0], v[1], sc[2])
        if f in ("cov", "covariance", "coviance"):
            return mcov(v[0], v[1], sc[2])
        if f in ("tsmax", "ts_max"):
            return tsmax(v[0], sc[1])
        if f in ("tsmin", "ts_min"):
            return tsmin(v[0], sc[1])
        if f in ("max", "min"):
            if n == 2 and sc[1] is not None and sc[1] >= 2 and float(sc[1]).is_integer() and sc[0] is None:
                return tsmax(v[0], sc[1]) if f == "max" else tsmin(v[0], sc[1])
            return np.maximum(v[0], v[1]) if f == "max" else np.minimum(v[0], v[1])
        if f == "greater":
            return np.maximum(v[0], v[1])
        if f == "less":
            return np.minimum(v[0], v[1])
        if f in ("ts_argmax", "idxmax"):
            return sc[1] - days_since_max(v[0], sc[1])
        if f in ("ts_argmin", "idxmin"):
            return sc[1] - days_since_min(v[0], sc[1])
        if f == "highday":
            return days_since_max(v[0], sc[1])
        if f == "lowday":
            return days_since_min(v[0], sc[1])
        if f in ("decaylinear", "decay_linear"):
            return decay_linear(v[0], sc[1])
        if f == "wma":
            return wma(v[0], sc[1])
        if f == "sma":
            return sma(v[0], sc[1], sc[2])
        if f in ("prod", "product"):
            return prod(v[0], sc[1])
        if f == "count":
            return msum((np.asarray(v[0]) != 0) * 1.0, sc[1])
        if f == "sumif":
            return msum(np.where(np.asarray(v[2]) != 0, v[0], 0.0), sc[1])
        if f == "scale":
            return scale(v[0], sc[1] if n > 1 else 1.0)
        if f == "quantile":
            return rquantile(v[0], sc[1], sc[2])
        if f == "skew":
            return skew(v[0], sc[1])
        if f == "exp":
            return np.exp(v[0])
        raise Unsupported(f"function {f}")
