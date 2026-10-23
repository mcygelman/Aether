"""TEACHFRAME-1 parsing, generated lesson cards, and migration validation.

The TeachFrame is the canonical source.  ``[ CARD ]`` is a deterministic,
ASCII-safe projection of the title, purpose, named failure, and first example;
it is never an independently authored rendering.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

from aether.models.budget import est_tokens

CARD_TOKEN_LIMIT = 120
REQUIRED_METADATA = (
    "TYPE",
    "VERSION",
    "DATE",
    "CREATOR",
    "SCHOOL",
    "AUDIENCE",
    "PREREQUISITES",
    "LINEAGE",
    "PREVENTS",
    "EVIDENCE",
    "MEASURED_BY",
    "RETIRES_WHEN",
    "OBSERVED_BY",
)

_META_RE = re.compile(r"^⟪([A-Z_]+)::(.*?)⟫\s*$", re.MULTILINE)
_SECTION_RE = re.compile(r"^\[\s*([A-Z][A-Z0-9_-]*)\s*\]\s*$", re.MULTILINE)
_CARD_RE = re.compile(
    r"^\[\s*CARD\s*\]\s*\n.*?(?=^\[\s*[A-Z][A-Z0-9_-]*\s*\]\s*$|^⟪(?:END_TEACHFRAME|TEACHFRAME_END)⟫)",
    re.MULTILINE | re.DOTALL,
)
_TRANSLATE = str.maketrans(
    {
        "—": "-",
        "–": "-",
        "→": "->",
        "←": "<-",
        "≤": "<=",
        "≥": ">=",
        "≠": "!=",
        "≈": "~=",
        "±": "+/-",
        "×": "x",
        "÷": "/",
        "·": "*",
        "⊨": "entails",
        "⊩": "proves",
        "⊢": "derives",
        "ζ": "zeta",
        "π": "pi",
        "ε": "epsilon",
        "μ": "mu",
        "τ": "tau",
        "φ": "phi",
        "θ": "theta",
        "₀": "0",
        "₁": "1",
        "₂": "2",
        "ᵣ": "r",
        "⌞": "",
        "⌟": "",
        "⟨": "<",
        "⟩": ">",
    }
)


class TeachFrameError(ValueError):
    """A canonical TeachFrame or its generated card is invalid."""


@dataclass(frozen=True)
class TeachFrameValidation:
    name: str
    card_tokens: int


def metadata(text: str) -> dict[str, str]:
    return {key: value.strip() for key, value in _META_RE.findall(text)}


def section(text: str, name: str) -> str:
    matches = list(_SECTION_RE.finditer(text))
    wanted = name.upper()
    for index, match in enumerate(matches):
        if match.group(1).upper() != wanted:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[match.end() : end]
        block = re.split(r"^⟪(?:END_TEACHFRAME|TEACHFRAME_END)⟫", block, maxsplit=1, flags=re.MULTILINE)[0]
        return block.strip()
    return ""


def without_card(text: str) -> str:
    return _CARD_RE.sub("", text, count=1)


def _ascii(value: str) -> str:
    value = value.translate(_TRANSLATE)
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return " ".join(value.split())


def _clip(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    clipped = value[: max(0, limit - 3)].rsplit(" ", 1)[0]
    return (clipped or value[: max(0, limit - 3)]) + "..."


def _first_sentence(value: str) -> str:
    flat = " ".join(value.split())
    match = re.match(r"(.+?[.!?])(?:\s|$)", flat)
    return match.group(1) if match else flat


def _first_example(value: str) -> str:
    for raw in value.splitlines():
        line = raw.strip()
        if not line or line.startswith("//") or set(line) <= {"━", "-"}:
            continue
        if line.startswith(("[Setup]:", "[Input or setup]:", "[Result]:")):
            line = line.split(":", 1)[1].strip()
        if line:
            return line
    return "See the TeachFrame's first worked example."


def frame_name(text: str) -> str:
    first = re.search(r"^⟪(TEACHFRAME_[^⟫]+)⟫\s*$", text, re.MULTILINE)
    if not first:
        raise TeachFrameError("missing TeachFrame opening marker")
    return first.group(1)


def generate_card(text: str) -> str:
    """Return the sole valid ``[ CARD ]`` projection for *text*."""
    source = without_card(text)
    meta = metadata(source)
    name = _ascii(frame_name(source).removeprefix("TEACHFRAME_"))
    title = _ascii(section(source, "TITLE"))
    purpose = _ascii(_first_sentence(section(source, "PURPOSE")))
    prevents = _ascii(meta.get("PREVENTS", ""))
    example = _ascii(_first_example(section(source, "EXAMPLES")))
    if not all((title, purpose, prevents, example)):
        raise TeachFrameError("card source requires TITLE, PURPOSE, PREVENTS, and EXAMPLES")

    # Fixed per-field ceilings keep every projection useful while guaranteeing
    # the 120-token conservative Bridge estimate.  The final guard catches any
    # future format growth.
    lines = [
        "[ CARD ]",
        f"  LESSON::{_clip(name, 48)}",
        f"  FOR::{_clip(purpose, 120)}",
        f"  PREVENTS::{_clip(prevents, 90)}",
        f"  EXAMPLE::{_clip(example, 90)}",
    ]
    card = "\n".join(lines).rstrip() + "\n"
    while est_tokens(card) > CARD_TOKEN_LIMIT:
        # Reduce the longest projected value without ever dropping a required
        # component.  This is deterministic and therefore seal/replay safe.
        value_index = max(range(1, len(lines)), key=lambda i: len(lines[i]))
        prefix, value = lines[value_index].split("::", 1)
        if len(value) <= 24:
            raise TeachFrameError("generated card cannot fit the 120-token ceiling")
        lines[value_index] = f"{prefix}::{_clip(value, len(value) - 12)}"
        card = "\n".join(lines).rstrip() + "\n"
    return card


def with_generated_card(text: str) -> str:
    """Replace or insert ``[ CARD ]`` immediately before ``CROSS_REFS``/end."""
    source = without_card(text).rstrip() + "\n"
    card = generate_card(source)
    cross = re.search(r"^\[\s*CROSS_REFS\s*\]\s*$", source, re.MULTILINE)
    if cross:
        return source[: cross.start()].rstrip() + "\n\n" + card + "\n" + source[cross.start() :]
    end = re.search(r"^⟪(?:END_TEACHFRAME|TEACHFRAME_END)⟫\s*$", source, re.MULTILINE)
    if not end:
        raise TeachFrameError("missing TeachFrame end marker")
    return source[: end.start()].rstrip() + "\n\n" + card + "\n" + source[end.start() :]


def validate(text: str, *, require_schema: bool = True) -> TeachFrameValidation:
    meta = metadata(text)
    if require_schema:
        missing = [field for field in REQUIRED_METADATA if not meta.get(field)]
        if missing:
            raise TeachFrameError("missing TeachFrame metadata: " + ", ".join(missing))
        if meta.get("TYPE") != "TEACHABLE_FRAME":
            raise TeachFrameError("TYPE must be TEACHABLE_FRAME")
    audience = meta.get("AUDIENCE", "")
    retired_names = {"Euler", "Fibonacci", "Gauss", "Hypatia", "Ada", "Kirito"}
    named = sorted(name for name in retired_names if re.search(rf"\b{re.escape(name)}\b", audience))
    if named:
        raise TeachFrameError("AUDIENCE names retired agents: " + ", ".join(named))
    actual = section(text, "CARD")
    if not actual:
        raise TeachFrameError("missing generated [ CARD ]")
    expected = section(generate_card(text), "CARD")
    if actual != expected:
        raise TeachFrameError("hand-edited or stale [ CARD ]; regenerate it from the TeachFrame")
    card_text = "[ CARD ]\n" + actual.rstrip() + "\n"
    tokens = est_tokens(card_text)
    if tokens > CARD_TOKEN_LIMIT:
        raise TeachFrameError(f"generated [ CARD ] exceeds {CARD_TOKEN_LIMIT} tokens: {tokens}")
    if not card_text.isascii():
        raise TeachFrameError("generated [ CARD ] must be ASCII-safe")
    return TeachFrameValidation(name=frame_name(text), card_tokens=tokens)
