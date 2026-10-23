"""calculate: a safe, deterministic calculator any agent can be granted (CALC-01).

Author: Claude (Opus 5.5, lead), 2026-10-01. Standalone: imports only the standard
library and touches no files, network, processes or environment. The host wires it
into a tool belt (``CalcTool.schema`` / ``CalcTool.dispatch``) and logs each receipt
in DIALOG at the tool boundary; that wiring is Praxis's integration work.

Why: every number an agent reports should be machine-produced and re-checkable.
An agent sends an expression; the tool returns the value plus a receipt whose hash
covers the normalized expression, the variables and the result, so forensics can
re-run it and get the same hash.

Design:
- The expression is parsed with ``ast`` and walked against a whitelist. It is never
  passed to eval/exec/compile-for-execution. Names exist only if whitelisted or
  declared in ``variables``. No attributes, subscripts, comprehensions, lambdas,
  keywords, starred args, f-strings or strings at all.
- Exact by default: integers and decimal literals become ``Fraction``. Only
  irrational operations (sqrt of a non-square, log, exp, trig, pi, e, non-integer
  powers) produce an approximate ``Decimal``, computed in an isolated decimal context
  at max(60, digits + 20) digits. The receipt separates arithmetic exactness from
  display rounding.
- ``^`` means power (math convention). Python ``**`` also works. Unicode × ÷ − are
  accepted.
- Limits: expression length, node count, nesting depth, integer size, list
  length, factorial/comb size, prime-test size, and a cooperative wall-clock
  deadline (checked between steps, inside loops and before returning). For a hard
  time/memory bound, hosts call ``evaluate_isolated``. Every failure is a clean
  ``{"ok": false, "error": ...}``.
- 1.1 (2026-10-01) resolves Praxis's seven review findings (Reviews/Praxis_1001_Calc01).
- 1.2 (2026-10-01) resolves the re-review: variables are resolved by dependency, not
  key order, and the receipt hashes an explicit ordered binding list; the isolated
  helper contains serialization failures.
- Scope: "no files, network or processes" describes the expression language and
  ``evaluate``. ``evaluate_isolated`` deliberately launches a Python child process
  that loads this file.
"""
from __future__ import annotations

import ast
import contextvars
import copy
import functools
import hashlib
import json
import math
import re
import time
import warnings
from decimal import (Context, Decimal, DivisionByZero, InvalidOperation, Overflow,
                     ROUND_HALF_EVEN, localcontext)
from fractions import Fraction

VERSION = "calc-1.2"
MAX_EXPRESSION_CHARS = 4_000
MAX_NODES = 2_000
MAX_DEPTH = 60
MAX_INT_BITS = 200_000          # about 60,000 decimal digits
MAX_LIST = 10_000
MAX_FACTORIAL = 10_000
MAX_PRIME_TEST = 10 ** 24
MAX_VARIABLES = 64
MAX_SECONDS = 2.0
WORK_DIGITS = 60                # minimum working precision for approximate values
GUARD_DIGITS = 20               # working precision is always >= requested digits + this
MAX_ARGUMENT_CHARS = 20_000     # raw tool-call arguments accepted by CalcTool.dispatch
DEFAULT_DIGITS = 30             # significant digits shown for decimals
MAX_DIGITS = 100
MAX_EXACT_DIGITS = 1_000         # longer exact integers are shown rounded
MAX_RATIONAL_DIGITS = 60         # longer exact fractions are shown rounded (smoke finding)
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,31}$")


class CalcError(Exception):
    """A clean, agent-facing refusal or math error."""


class _Deadline:
    def __init__(self, seconds):
        self.end = time.monotonic() + seconds

    def check(self):
        if time.monotonic() > self.end:
            raise CalcError(f"time limit exceeded ({MAX_SECONDS}s)")


# Per-evaluation state. Every Decimal operation runs inside an isolated context built
# here (review F3): the caller's precision, rounding and traps never leak in.
_WP = contextvars.ContextVar("calc_working_precision", default=WORK_DIGITS)
_DL = contextvars.ContextVar("calc_deadline", default=None)


def _wp():
    return _WP.get()


def _tick():
    """Cooperative deadline check inside loops (review F4). Not a hard bound; see
    evaluate_isolated for a process-level limit."""
    dl = _DL.get()
    if dl is not None:
        dl.check()


def _isolated_context(prec):
    return Context(prec=prec, rounding=ROUND_HALF_EVEN, Emin=-999_999, Emax=999_999,
                   capitals=1, clamp=0, flags=[], traps=[InvalidOperation, DivisionByZero, Overflow])


# ---------- value helpers ----------

def _is_num(v):
    return isinstance(v, (Fraction, Decimal)) and not isinstance(v, bool)


def _num(v, what="value"):
    if isinstance(v, bool):
        raise CalcError(f"{what} must be a number, not true/false")
    if isinstance(v, list):
        raise CalcError(f"{what} must be a number, not a list")
    if not _is_num(v):
        raise CalcError(f"{what} must be a number")
    return v


def _dec(v):
    """Approximate a number as Decimal at working precision."""
    v = _num(v)
    if isinstance(v, Decimal):
        return v
    with localcontext() as c:
        c.prec = _wp()
        return Decimal(v.numerator) / Decimal(v.denominator)


def _check_size(v):
    if isinstance(v, Fraction):
        if v.numerator.bit_length() > MAX_INT_BITS or v.denominator.bit_length() > MAX_INT_BITS:
            raise CalcError("number too large (integer size limit)")
    elif isinstance(v, Decimal):
        if not v.is_finite():
            raise CalcError("result is not a finite number")
        if v != 0 and abs(v.adjusted()) > 100_000:
            raise CalcError("number too large or too small")
    return v


def _as_int(v, what):
    v = _num(v, what)
    if isinstance(v, Fraction) and v.denominator == 1:
        return v.numerator
    if isinstance(v, Decimal) and v == v.to_integral_value():
        return int(v)
    raise CalcError(f"{what} must be an integer")


def _mixed(a, b):
    """Promote to a common kind: exact if both exact, else Decimal."""
    if isinstance(a, Fraction) and isinstance(b, Fraction):
        return a, b, True
    return _dec(a), _dec(b), False


# ---------- approximate functions (Decimal, WORK_DIGITS) ----------

@functools.lru_cache(maxsize=16)
def _pi_at(prec):
    """pi to prec+10 digits, computed in its own context (Machin's formula)."""
    with localcontext(_isolated_context(prec + 10)):
        def atan_inv(n):
            x = Decimal(1) / n
            x2 = x * x
            term, total, k, sign = x, x, 1, 1
            eps = Decimal(10) ** -(prec + 8)
            while abs(term) > eps:
                term *= x2
                k += 2
                sign = -sign
                total += sign * term / k
            return total
        return +(16 * atan_inv(5) - 4 * atan_inv(239))


@functools.lru_cache(maxsize=16)
def _e_at(prec):
    with localcontext(_isolated_context(prec + 10)):
        return Decimal(1).exp()


def _const(name):
    p = _wp()
    with localcontext(_isolated_context(p + 10)):
        v = {"pi": _pi_at(p), "e": _e_at(p), "tau": 2 * _pi_at(p)}[name]
    with localcontext(_isolated_context(p)):
        return +v


def _trig(x, kind, dl):
    with localcontext() as c:
        c.prec = _wp() + 10
        x = _dec(x)
        if abs(x) > Decimal(10) ** 30:
            raise CalcError("trig argument too large (|x| <= 1e30)")
        pi = _pi_at(_wp())
        x = x % (2 * pi)                    # reduce
        if kind == "cos":
            x = pi / 2 - x
        term, total, n = x, x, 1
        eps = Decimal(10) ** -(_wp() + 8)
        while abs(term) > eps:
            dl.check()
            term = -term * x * x / ((n + 1) * (n + 2))
            total += term
            n += 2
        return +total


def _sqrt(v):
    v = _num(v)
    if v < 0:
        raise CalcError("sqrt of a negative number")
    if isinstance(v, Fraction):
        n, d = v.numerator, v.denominator
        rn, rd = math.isqrt(n), math.isqrt(d)
        if rn * rn == n and rd * rd == d:
            return Fraction(rn, rd)
    with localcontext() as c:
        c.prec = _wp()
        return _dec(v).sqrt()


def _ln(v):
    v = _num(v)
    if v <= 0:
        raise CalcError("log of a non-positive number")
    with localcontext() as c:
        c.prec = _wp()
        return _dec(v).ln()


def _log(v, base=None):
    if base is None:
        return _ln(v)
    b = _num(base, "log base")
    if b <= 0 or b == 1:
        raise CalcError("log base must be positive and not 1")
    # exact when v is an integer power of an integer base
    if isinstance(v, Fraction) and isinstance(b, Fraction) and v > 0 and v.denominator == 1 and b.denominator == 1 and b > 1:
        n, k, p = v.numerator, 0, 1
        while p < n:
            _tick()
            p *= b.numerator
            k += 1
        if p == n:
            return Fraction(k)
    with localcontext() as c:
        c.prec = _wp()
        return _ln(v) / _ln(b)


def _exp(v):
    with localcontext() as c:
        c.prec = _wp()
        x = _dec(v)
        if x > 230_000:
            raise CalcError("exp argument too large")
        return x.exp()


# ---------- exact integer functions ----------

def _is_prime(n):
    if n < 2:
        return False
    small = (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41)
    for p in small:
        if n % p == 0:
            return n == p
    d, s = n - 1, 0
    while d % 2 == 0:
        d //= 2
        s += 1
    for a in small:     # deterministic for n < 3.3e24
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(s - 1):
            _tick()
            x = x * x % n
            if x == n - 1:
                break
        else:
            return False
    return True


def _round(v, nd=None):
    v = _num(v)
    nd = 0 if nd is None else _as_int(nd, "round digits")
    if not -1000 <= nd <= 1000:
        raise CalcError("round digits out of range")
    # Half away from zero (school rounding): round(2.5) = 3, round(-2.5) = -3.
    if isinstance(v, Fraction):
        scale = Fraction(10) ** nd
        mag = math.floor(abs(v) * scale + Fraction(1, 2))
        return Fraction(mag if v >= 0 else -mag) / scale
    with localcontext() as c:
        c.prec = _wp()
        return v.quantize(Decimal(1).scaleb(-nd), rounding="ROUND_HALF_UP")


def _floor(v):
    v = _num(v)
    return Fraction(math.floor(v)) if isinstance(v, Fraction) else Fraction(int(v.to_integral_value(rounding="ROUND_FLOOR")))


def _ceil(v):
    v = _num(v)
    return Fraction(math.ceil(v)) if isinstance(v, Fraction) else Fraction(int(v.to_integral_value(rounding="ROUND_CEILING")))


def _flatten(args, what):
    vals = []
    for a in args:
        _tick()
        if isinstance(a, list):
            vals.extend(a)
        else:
            vals.append(a)
    if not vals:
        raise CalcError(f"{what} needs at least one number")
    if len(vals) > MAX_LIST:
        raise CalcError("list too long")
    return [_num(v, what) for v in vals]


def _sum(*args):
    vals = _flatten(args, "sum")
    exact = all(isinstance(v, Fraction) for v in vals)
    total = Fraction(0) if exact else Decimal(0)
    for v in vals:
        _tick()
        total = _check_size(total + v if exact else _add(total, v))
    return total


def _mean(*args):
    vals = _flatten(args, "mean")
    return _div(_sum(vals), Fraction(len(vals)))


def _median(*args):
    vals = _flatten(args, "median")
    vals = sorted(vals)          # exact: Python compares Fraction and Decimal exactly (review F1)
    n = len(vals)
    mid = n // 2
    return vals[mid] if n % 2 else _div(_add(vals[mid - 1], vals[mid]), Fraction(2))


def _variance(vals, sample):
    n = len(vals)
    if sample and n < 2:
        raise CalcError("sample stdev needs at least two numbers")
    m = _div(_sum(vals), Fraction(n))
    squares = []
    for v in vals:
        _tick()
        d = _sub(v, m)
        squares.append(_mul(d, d))
    ss = _sum(squares)
    return _div(ss, Fraction(n - 1 if sample else n))


def _stdev(*args):
    return _sqrt(_variance(_flatten(args, "stdev"), True))


def _pstdev(*args):
    return _sqrt(_variance(_flatten(args, "pstdev"), False))


def _minmax(fn, *args):
    vals = _flatten(args, fn.__name__)
    return fn(vals)              # exact ordering (review F1)


# ---------- arithmetic ----------

def _add(a, b):
    a, b, exact = _mixed(_num(a), _num(b))
    if exact:
        return a + b
    with localcontext() as c:
        c.prec = _wp()
        return a + b


def _sub(a, b):
    a, b, exact = _mixed(_num(a), _num(b))
    if exact:
        return a - b
    with localcontext() as c:
        c.prec = _wp()
        return a - b


def _mul(a, b):
    a, b, exact = _mixed(_num(a), _num(b))
    if exact:
        return a * b
    with localcontext() as c:
        c.prec = _wp()
        return a * b


def _div(a, b):
    a, b, exact = _mixed(_num(a), _num(b))
    if b == 0:
        raise CalcError("division by zero")
    if exact:
        return a / b
    with localcontext() as c:
        c.prec = _wp()
        return a / b


def _floordiv(a, b):
    a, b, exact = _mixed(_num(a), _num(b))
    if b == 0:
        raise CalcError("division by zero")
    if exact:
        return Fraction(a // b)
    return _floor(_div(a, b))


def _mod(a, b):
    a, b, exact = _mixed(_num(a), _num(b))
    if b == 0:
        raise CalcError("modulo by zero")
    if exact:
        return a % b
    with localcontext() as c:
        c.prec = _wp()
        return a - b * _dec(_floor(a / b))


def _pow(a, b):
    a, b = _num(a), _num(b)
    if isinstance(b, Fraction) and b.denominator == 1 and isinstance(a, Fraction):
        e = b.numerator
        if a == 0 and e < 0:
            raise CalcError("zero to a negative power")
        if abs(a) not in (0, 1):
            def log2(n):
                n = abs(n)
                return math.log2(n) if n.bit_length() < 1000 else n.bit_length()
            bits = max(log2(a.numerator), log2(a.denominator))
            if bits * abs(e) > MAX_INT_BITS:
                raise CalcError("power too large (integer size limit)")
        return a ** e
    # exact rational root when possible, e.g. 8^(1/3), 4^(1/2)
    if isinstance(a, Fraction) and isinstance(b, Fraction) and a > 0 and b.denominator <= 64:
        q = b.denominator
        def iroot(n):
            r = round(n ** (1.0 / q)) if n.bit_length() < 1000 else None
            if r is None:
                return None
            for cand in (r - 1, r, r + 1):
                if cand >= 0 and cand ** q == n:
                    return cand
            return None
        rn, rd = iroot(a.numerator), iroot(a.denominator)
        if rn is not None and rd is not None:
            return _pow(Fraction(rn, rd), Fraction(b.numerator))
    x, y = _dec(a), _dec(b)
    if x < 0:
        raise CalcError("negative base with a non-integer exponent")
    if x == 0:
        if y <= 0:
            raise CalcError("zero to a non-positive power")
        return Decimal(0)
    with localcontext() as c:
        c.prec = _wp() + 5
        if y * x.ln() > 230_000:
            raise CalcError("power too large")
        return +(x ** y)


def _cmp(op, a, b):
    a, b = _num(a), _num(b)      # Fraction/Decimal comparisons are exact in Python
    return {ast.Eq: a == b, ast.NotEq: a != b, ast.Lt: a < b, ast.LtE: a <= b,
            ast.Gt: a > b, ast.GtE: a >= b}[op]


# ---------- the whitelist ----------

def _fn_int(name, f):
    def g(*args):
        return f(*[_as_int(a, f"{name} argument") for a in args])
    return g


def _factorial(n):
    if n < 0 or n > MAX_FACTORIAL:
        raise CalcError(f"factorial argument must be 0..{MAX_FACTORIAL}")
    return Fraction(math.factorial(n))


def _comb(n, k):
    if n < 0 or k < 0 or n > MAX_FACTORIAL * 10:
        raise CalcError("comb arguments out of range")
    return Fraction(math.comb(n, k))


def _perm(n, k):
    if n < 0 or k < 0 or n > MAX_FACTORIAL * 10:
        raise CalcError("perm arguments out of range")
    return Fraction(math.perm(n, k))


def _isprime(n):
    if abs(n) > MAX_PRIME_TEST:
        raise CalcError("isprime limit is 10^24")
    return _is_prime(n)


FUNCTIONS = {
    "abs": (1, 1, lambda v: abs(_num(v))),
    "sign": (1, 1, lambda v: Fraction((_num(v) > 0) - (_num(v) < 0))),
    "min": (1, MAX_LIST, lambda *a: _minmax(min, *a)),
    "max": (1, MAX_LIST, lambda *a: _minmax(max, *a)),
    "floor": (1, 1, _floor),
    "ceil": (1, 1, _ceil),
    "round": (1, 2, _round),
    "frac": (1, 1, lambda v: _sub(v, _floor(v))),
    "mod": (2, 2, _mod),
    "sqrt": (1, 1, _sqrt),
    "cbrt": (1, 1, lambda v: _pow(v, Fraction(1, 3)) if _num(v) >= 0 else -_pow(-_num(v), Fraction(1, 3))),
    "log": (1, 2, _log),
    "ln": (1, 1, _ln),
    "log10": (1, 1, lambda v: _log(v, Fraction(10))),
    "log2": (1, 1, lambda v: _log(v, Fraction(2))),
    "exp": (1, 1, _exp),
    "gcd": (1, MAX_LIST, _fn_int("gcd", lambda *a: Fraction(math.gcd(*a)))),
    "lcm": (1, MAX_LIST, _fn_int("lcm", lambda *a: Fraction(math.lcm(*a)))),
    "factorial": (1, 1, _fn_int("factorial", _factorial)),
    "comb": (2, 2, _fn_int("comb", _comb)),
    "perm": (2, 2, _fn_int("perm", _perm)),
    "isprime": (1, 1, _fn_int("isprime", _isprime)),
    "sum": (1, MAX_LIST, _sum),
    "mean": (1, MAX_LIST, _mean),
    "median": (1, MAX_LIST, _median),
    "stdev": (1, MAX_LIST, _stdev),
    "pstdev": (1, MAX_LIST, _pstdev),
}
TRIG = {"sin", "cos", "tan"}
CONSTANTS = ("pi", "e", "tau")
RESERVED = set(FUNCTIONS) | TRIG | set(CONSTANTS) | {"true", "false", "True", "False", "and", "or", "not"}

_BINOPS = {ast.Add: _add, ast.Sub: _sub, ast.Mult: _mul, ast.Div: _div,
           ast.FloorDiv: _floordiv, ast.Mod: _mod, ast.Pow: _pow}
_ALLOWED = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Compare, ast.BoolOp, ast.Call,
            ast.Name, ast.Load, ast.Constant, ast.List, ast.Tuple,
            ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
            ast.UAdd, ast.USub, ast.Not, ast.And, ast.Or,
            ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE)


def _prepare(expression):
    if not isinstance(expression, str):
        raise CalcError("expression must be a string")
    text = expression.strip()
    if not text:
        raise CalcError("empty expression")
    if len(text) > MAX_EXPRESSION_CHARS:
        raise CalcError(f"expression longer than {MAX_EXPRESSION_CHARS} characters")
    # ^ is power with the usual math precedence and right-associativity, so it is
    # rewritten to ** before parsing (Python's ^ would bind looser than +).
    for a, b in (("×", "*"), ("÷", "/"), ("−", "-"), ("π", "pi"), ("√", "sqrt"), ("^", "**")):
        text = text.replace(a, b)
    if any(ch in text for ch in "'\"`\\;@{}:\n\r"):
        raise CalcError("strings, statements and special characters are not allowed")
    try:
        with warnings.catch_warnings():          # e.g. "invalid decimal literal" noise
            warnings.simplefilter("ignore")
            tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise CalcError(f"syntax error: {exc.msg}") from None
    except (ValueError, RecursionError, MemoryError):
        raise CalcError("expression could not be parsed") from None
    count = 0

    def walk(node, depth):
        nonlocal count
        count += 1
        if count > MAX_NODES:
            raise CalcError("expression too complex (node limit)")
        if depth > MAX_DEPTH:
            raise CalcError("expression nested too deeply")
        if not isinstance(node, _ALLOWED):
            raise CalcError(f"not allowed: {type(node).__name__}")
        if isinstance(node, ast.Constant) and (isinstance(node.value, bool) or not isinstance(node.value, (int, float))):
            raise CalcError("only numeric literals are allowed")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.keywords:
                raise CalcError("only plain calls to listed functions are allowed")
            if node.func.id not in FUNCTIONS and node.func.id not in TRIG:
                raise CalcError(f"unknown function '{node.func.id}'")
        for child in ast.iter_child_nodes(node):
            walk(child, depth + 1)
    walk(tree, 0)
    return text, tree


def _literal(node, text):
    """Exact value of a numeric literal from its source text (0.1 stays 1/10)."""
    seg = ast.get_source_segment(text, node)
    v = node.value
    if isinstance(v, int):
        return Fraction(v)
    if seg and re.fullmatch(r"[0-9_]*\.?[0-9_]*([eE][+-]?[0-9_]+)?", seg):
        try:
            d = Decimal(seg.replace("_", ""))
        except (InvalidOperation, ValueError):
            d = None
        if d is not None:
            if abs(d.adjusted()) > 60_000:      # zero with a huge exponent included
                raise CalcError("literal out of range")
            return Fraction(d)
    if math.isinf(v) or math.isnan(v):
        raise CalcError("literal out of range")
    return Fraction(v)


def _canonical(tree, text):
    """Normalized expression text whose numeric literals keep their written form.

    ast.unparse alone would print the binary float that Python made of a decimal
    literal (review F2: 0.1234567890123456789 or 1e400 would not round-trip). Each
    literal is swapped for a placeholder, unparsed, then restored from its source text
    with underscores removed. Re-parsing the result yields the same values."""
    t = copy.deepcopy(tree)
    literals = []
    for node in ast.walk(t):
        if isinstance(node, ast.Constant):
            seg = ast.get_source_segment(text, node) or repr(node.value)
            literals.append(seg.replace("_", ""))
            node.__class__ = ast.Name
            node.id = f"_L{len(literals) - 1}_"
            node.ctx = ast.Load()
            for attr in ("value", "kind"):
                if hasattr(node, attr):
                    delattr(node, attr)
    out = ast.unparse(t)
    return re.sub(r"\b_L(\d+)_\b", lambda m: literals[int(m.group(1))], out)


def _eval(node, text, env, dl):
    dl.check()
    if isinstance(node, ast.Expression):
        return _eval(node.body, text, env, dl)
    if isinstance(node, ast.Constant):
        return _literal(node, text)
    if isinstance(node, ast.Name):
        if node.id in env:
            return env[node.id]
        if node.id in CONSTANTS:
            return _const(node.id)
        if node.id in ("true", "True"):
            return True
        if node.id in ("false", "False"):
            return False
        raise CalcError(f"unknown name '{node.id}' (declare it in variables)")
    if isinstance(node, (ast.List, ast.Tuple)):
        if len(node.elts) > MAX_LIST:
            raise CalcError("list too long")
        return [_num(_eval(e, text, env, dl), "list item") for e in node.elts]
    if isinstance(node, ast.UnaryOp):
        v = _eval(node.operand, text, env, dl)
        if isinstance(node.op, ast.Not):
            if not isinstance(v, bool):
                raise CalcError("'not' needs true/false")
            return not v
        v = _num(v)
        return _check_size(-v if isinstance(node.op, ast.USub) else +v)
    if isinstance(node, ast.BinOp):
        a = _eval(node.left, text, env, dl)
        b = _eval(node.right, text, env, dl)
        return _check_size(_BINOPS[type(node.op)](a, b))
    if isinstance(node, ast.BoolOp):
        vals = [_eval(v, text, env, dl) for v in node.values]
        if not all(isinstance(v, bool) for v in vals):
            raise CalcError("'and'/'or' need true/false values")
        return all(vals) if isinstance(node.op, ast.And) else any(vals)
    if isinstance(node, ast.Compare):
        left = _eval(node.left, text, env, dl)
        for op, right_node in zip(node.ops, node.comparators):
            right = _eval(right_node, text, env, dl)
            if not _cmp(type(op), left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.Call):
        name = node.func.id
        args = [_eval(a, text, env, dl) for a in node.args]
        if name in TRIG:
            if len(args) != 1:
                raise CalcError(f"{name} takes 1 argument")
            if name == "tan":
                c = _trig(args[0], "cos", dl)
                if abs(c) < Decimal(10) ** -(_wp() - 5):
                    raise CalcError("tan is undefined here")
                return _check_size(_trig(args[0], "sin", dl) / c)
            return _check_size(_trig(args[0], name, dl))
        lo, hi, fn = FUNCTIONS[name]
        if not lo <= len(args) <= hi:
            raise CalcError(f"{name} takes {lo}" + ("" if lo == hi else f" to {hi}") + " argument(s)")
        list_ok = name in ("min", "max", "sum", "mean", "median", "stdev", "pstdev", "gcd", "lcm")
        if not list_ok and any(isinstance(a, list) for a in args):
            raise CalcError(f"{name} does not take a list")
        if name in ("gcd", "lcm"):
            args = _flatten(args, name)
        out = fn(*args)
        return _check_size(out) if not isinstance(out, bool) else out
    raise CalcError(f"not allowed: {type(node).__name__}")


# ---------- formatting and receipts ----------

def _fmt_decimal(v, digits):
    with localcontext() as c:
        c.prec = digits
        d = +_dec(v)
    s = format(d, "f") if -30 < d.adjusted() < 40 else format(d, "E")
    if "." in s and "E" not in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def _digit_count(n):
    n = abs(n)
    return 1 if n == 0 else Decimal(n).adjusted() + 1


def _render(v, digits):
    if isinstance(v, bool):
        return {"value": "true" if v else "false", "type": "boolean", "exact": True, "arithmetic": "exact"}
    if isinstance(v, list):
        raise CalcError("the result is a list; wrap it in a function such as sum or mean")
    if isinstance(v, Fraction):
        nd = _digit_count(v.numerator) + (_digit_count(v.denominator) if v.denominator != 1 else 0)
        if nd > (MAX_EXACT_DIGITS if v.denominator == 1 else MAX_RATIONAL_DIGITS):
            # Too long to hand back usefully (and Python caps int->str): show it rounded,
            # and say exactly what was withheld.
            out = {"value": _fmt_decimal(v, digits), "type": "integer" if v.denominator == 1 else "rational",
                   "exact": False, "arithmetic": "exact", "display": "rounded",
                   "precision": (f"computed exactly; the exact value has {nd} digits, "
                                 f"so it is shown rounded to {digits} significant digits")}
            if v.denominator == 1:
                out["digit_count"] = _digit_count(v.numerator)
                out["last_30_digits"] = format(abs(v.numerator) % 10 ** 30, "030d")
            return out
        value = str(v.numerator) if v.denominator == 1 else f"{v.numerator}/{v.denominator}"
        out = {"value": value, "type": "integer" if v.denominator == 1 else "rational",
               "exact": True, "arithmetic": "exact"}
        if v.denominator != 1:
            out["decimal"] = _fmt_decimal(v, digits)
            out["decimal_note"] = f"rounded to {digits} significant digits"
        return out
    return {"value": _fmt_decimal(v, digits), "type": "decimal", "exact": False, "arithmetic": "approximate",
            "display": "rounded",
            "precision": (f"approximate; computed with {_wp()}-digit working precision "
                          f"and shown to {digits} significant digits")}


def _parse_variables(variables, dl):
    """Resolve named values by their dependencies, not by object key order.

    Re-review finding (calc-1.2): a JSON round-trip may reorder object keys, so
    insertion order cannot carry meaning. Each variable may use any other variable;
    they are evaluated in dependency order (ties broken by name), and a cycle is a
    clean error. The receipt records the bindings as an ordered list of
    [name, expression] pairs, so the hashed form states the exact evaluation order.
    Input may be an object {name: value} or that same list of pairs."""
    env = {}
    if variables is None:
        return env, []
    if isinstance(variables, dict):
        items = list(variables.items())
    elif isinstance(variables, list):
        items = []
        for pair in variables:
            if not (isinstance(pair, (list, tuple)) and len(pair) == 2):
                raise CalcError("variables list must hold [name, value] pairs")
            items.append((pair[0], pair[1]))
    else:
        raise CalcError("variables must be an object of name: number or name: expression")
    if len(items) > MAX_VARIABLES:
        raise CalcError(f"at most {MAX_VARIABLES} variables")
    parsed = {}
    for name, raw in items:
        if not isinstance(name, str) or not NAME_RE.match(name) or name in RESERVED:
            raise CalcError(f"bad or reserved variable name: {name!r}")
        if name in parsed:
            raise CalcError(f"variable {name} is defined twice")
        if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
            raise CalcError(f"variable {name} must be a number or an expression string")
        src = raw if isinstance(raw, str) else repr(raw)
        parsed[name] = _prepare(src)
    deps = {name: {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id in parsed}
            for name, (_, tree) in parsed.items()}
    order, done = [], set()
    while len(order) < len(parsed):
        _tick()
        ready = sorted(n for n in parsed if n not in done and deps[n] <= done)
        if not ready:
            stuck = sorted(n for n in parsed if n not in done)
            raise CalcError("variables refer to each other in a cycle: " + ", ".join(stuck))
        order.append(ready[0])
        done.add(ready[0])
    shown = []
    for name in order:
        text, tree = parsed[name]
        val = _eval(tree, text, env, dl)
        if not _is_num(val):
            raise CalcError(f"variable {name} must be a number")
        env[name] = val
        shown.append([name, _canonical(tree, text)])
    return env, shown


CORE_FIELDS = ("tool", "version", "expression", "variables", "digits", "result")


def _core_hash(core):
    canonical = json.dumps({k: core[k] for k in CORE_FIELDS}, sort_keys=True,
                           ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _evaluate(expression, variables, digits, dl):
    if digits is None:
        digits = DEFAULT_DIGITS
    if isinstance(digits, bool) or not isinstance(digits, int) or not 1 <= digits <= MAX_DIGITS:
        raise CalcError(f"digits must be an integer 1..{MAX_DIGITS}")
    # Working precision always exceeds what is displayed (review F6).
    wp = max(WORK_DIGITS, digits + GUARD_DIGITS)
    wp_token, dl_token = _WP.set(wp), _DL.set(dl)
    try:
        with localcontext(_isolated_context(wp)):
            env, shown = _parse_variables(variables, dl)
            text, tree = _prepare(expression)
            value = _eval(tree, text, env, dl)
            dl.check()                       # final check: a slow step cannot return ok (F4)
            result = _render(value, digits)
            dl.check()
            core = {"tool": "calculate", "version": VERSION, "expression": _canonical(tree, text),
                    "variables": shown, "digits": digits, "result": result}
    finally:
        _WP.reset(wp_token)
        _DL.reset(dl_token)
    return core


def evaluate(expression, variables=None, digits=None):
    """Evaluate one expression. Never raises; returns a receipt dict.

    The deadline is cooperative: it is checked between steps, inside loops and before
    returning, so a slow evaluation never returns ok, but one long primitive operation
    cannot be interrupted in-process. Hosts that need a hard time/memory bound should
    call evaluate_isolated."""
    started = time.perf_counter()
    try:
        core = _evaluate(expression, variables, digits, _Deadline(MAX_SECONDS))
        return dict(ok=True, **core, receipt_sha256=_core_hash(core),
                    elapsed_ms=round((time.perf_counter() - started) * 1000, 3))
    except CalcError as exc:
        error = str(exc)
    except Exception as exc:                 # never raise into the agent turn (F7)
        error = f"could not compute ({type(exc).__name__})"
    return {"ok": False, "tool": "calculate", "version": VERSION, "error": error,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3)}


def verify(receipt):
    """Check a receipt three ways (review F5): its hash matches its own covered fields,
    it names this tool and version, and re-running it reproduces every covered field."""
    try:
        if not isinstance(receipt, dict) or receipt.get("ok") is not True:
            return False
        if any(k not in receipt for k in CORE_FIELDS) or "receipt_sha256" not in receipt:
            return False
        if receipt["tool"] != "calculate" or receipt["version"] != VERSION:
            return False
        if _core_hash(receipt) != receipt["receipt_sha256"]:
            return False
        again = evaluate(receipt["expression"], receipt["variables"] or None, receipt["digits"])
        return again.get("ok") is True and all(again[k] == receipt[k] for k in CORE_FIELDS) \
            and again["receipt_sha256"] == receipt["receipt_sha256"]
    except Exception:
        return False


# ---------- host-facing tool ----------

DESCRIPTION = (
    "Compute a number instead of estimating it. Send one math expression; the result comes back with a "
    "receipt. Quote the receipt's value exactly in your answer. Exact by default (integers and fractions stay "
    "exact; 0.1 means 1/10); irrational results are marked approximate. Operators: + - * / // % and ^ or ** "
    "for power (2^3^2 = 512); comparisons (<, <=, ==, !=, >=, >) and and/or/not give true/false. Functions: abs, sign, min, "
    "max, floor, ceil, round(x, digits) (halves away from zero), frac, mod, sqrt, cbrt, log(x) natural or log(x, base), ln, log10, "
    "log2, exp, sin, cos, tan (radians), gcd, lcm, factorial, comb, perm, isprime, sum, mean, median, stdev "
    "(sample), pstdev. Lists: [1, 2, 3] inside list functions. Constants: pi, e, tau. Optional variables: "
    "{\"name\": number or expression}; variables may use each other in any order (no cycles). "
    "No code, strings or files.")

SCHEMA = {
    "type": "function",
    "function": {
        "name": "calculate",
        "description": DESCRIPTION,
        "parameters": {
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "One math expression, e.g. (17^3 - 5) / 12"},
                "variables": {"type": "object", "description": "Optional named values, e.g. {\"r\": 2.5, \"area\": \"pi*r^2\"}; "
                              "order does not matter. Long decimals are safest as strings.",
                              "additionalProperties": {"type": ["number", "string"]}},
                "digits": {"type": "integer", "minimum": 1, "maximum": MAX_DIGITS,
                           "description": f"Significant digits for decimals (default {DEFAULT_DIGITS})"},
            },
            "required": ["expression"],
            "additionalProperties": False,
        },
    },
}


class CalcTool:
    """Stateless adapter for a tool belt. ``dispatch`` takes the raw JSON
    arguments string from a tool call and returns a JSON-serializable receipt."""
    name = "calculate"
    schema = SCHEMA

    @staticmethod
    def dispatch(arguments):
        if isinstance(arguments, str) and len(arguments) > MAX_ARGUMENT_CHARS:
            return {"ok": False, "tool": "calculate", "version": VERSION,
                    "error": f"arguments longer than {MAX_ARGUMENT_CHARS} characters"}
        try:
            args = json.loads(arguments) if isinstance(arguments, str) else arguments
        except Exception:                    # ValueError, RecursionError, MemoryError (F7)
            return {"ok": False, "tool": "calculate", "version": VERSION, "error": "arguments are not valid JSON"}
        if not isinstance(args, dict) or set(args) - {"expression", "variables", "digits"} or "expression" not in args:
            return {"ok": False, "tool": "calculate", "version": VERSION,
                    "error": "arguments must be {expression, variables?, digits?}"}
        return evaluate(args["expression"], args.get("variables"), args.get("digits"))


def evaluate_isolated(expression, variables=None, digits=None, *, timeout=5.0, memory_mb=512):
    """Run evaluate in a separate Python process with a hard wall-clock limit (and, on
    POSIX, an address-space limit). Use this when the host must not let one tool call
    hold the harness process (review F4). The child imports this same file and nothing
    else from the harness; the receipt is identical to evaluate's."""
    import os
    import subprocess
    import sys
    child = ("import importlib.util,json,sys\n"
             "spec=importlib.util.spec_from_file_location('calc_isolated', sys.argv[1])\n"
             "m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
             "a=json.loads(sys.stdin.read())\n"
             "sys.stdout.write(json.dumps(m.evaluate(a['expression'], a['variables'], a['digits'])))\n")
    preexec = None
    if os.name == "posix" and memory_mb:
        def preexec():
            import resource
            limit = int(memory_mb) * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    started = time.perf_counter()
    try:
        payload = json.dumps({"expression": expression, "variables": variables, "digits": digits})
        if len(payload) > MAX_ARGUMENT_CHARS:
            raise CalcError(f"arguments longer than {MAX_ARGUMENT_CHARS} characters")
        done = subprocess.run([sys.executable, "-I", "-B", "-c", child, os.path.abspath(__file__)],
                              input=payload, capture_output=True, text=True, timeout=timeout,
                              preexec_fn=preexec)
        out = json.loads(done.stdout) if done.returncode == 0 and done.stdout else None
    except CalcError as exc:
        out = {"ok": False, "tool": "calculate", "version": VERSION, "error": str(exc)}
    except (TypeError, ValueError):
        out = {"ok": False, "tool": "calculate", "version": VERSION,
               "error": "arguments must be JSON-serializable (expression, variables, digits)"}
    except subprocess.TimeoutExpired:
        out = {"ok": False, "tool": "calculate", "version": VERSION,
               "error": f"hard time limit exceeded ({timeout}s, isolated worker stopped)"}
    except Exception as exc:
        out = {"ok": False, "tool": "calculate", "version": VERSION,
               "error": f"isolated worker failed ({type(exc).__name__})"}
    if not isinstance(out, dict):
        out = {"ok": False, "tool": "calculate", "version": VERSION,
               "error": "isolated worker ended without a result (resource limit or crash)"}
    out["isolated_elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
    return out


if __name__ == "__main__":   # tiny CLI: python -m aether_bridge.calc "2^10 + sqrt(2)"
    import sys
    print(json.dumps(evaluate(" ".join(sys.argv[1:])), indent=2, ensure_ascii=False))
