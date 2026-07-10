# Owns: pure model classification — product_class, width, floor_only, size tiers.
# Must not: import from adapters, services, infrastructure, or config.
# May import: standard library only.

from __future__ import annotations

import re

# ── Width extraction ──────────────────────────────────────────────────────────
# Recognises known appliance widths (inches) embedded in a model number.
_WIDTH_RE = re.compile(r"(60|48|42|36|33|30|27|24|21|18)")


def extract_width(model_number: str) -> int | None:
    """Return the first recognised appliance width (inches) in the model string."""
    m = _WIDTH_RE.search(model_number.upper())
    return int(m.group(1)) if m else None


# ── Product classification ────────────────────────────────────────────────────

_SMALL_CAT_KW     = ("hood", "vent", "microwave", "parts", "panel", "accessor", "trim", "hardware")
_SMALL_TYPE_KW    = ("otr", "chimney", "insert", "island", "under cabinet", "liner", "power pack")
_BUILTIN_TYPE_KW  = ("built in", "built-in", "column", "integrated", "top mount", "wine", "beverage")
_FREESTAND_TYPE_KW = ("french door", "side by side", "side-by-side", "bottom mount",
                      "bottom freezer", "freestanding ref")
_SLIDEIN_TYPE_KW  = ("slide-in", "slide in", "slidein", "drop-in", "drop in")
_FLOOR_ROWS = frozenset({"C", "13"})  # floor-level rows that only hold large floor items


def classify_model(
    model_number: str,
    category: str,
    product_type: str,
    primary_row: str,
) -> tuple[str, int | None, bool]:
    """Return (product_class, width_in, floor_only) from model metadata.

    product_class: BULK | SMALL | FREESTANDING_REFER | BUILTIN_REFER |
                   LARGE_RANGE | SLIDEIN_RANGE | LAUNDRY | DISHWASHER | OTHER
    width_in: extracted from model_number, or None.
    floor_only: True only for LARGE_RANGE (width >= 36" or in a floor-level row).
    """
    row   = primary_row.upper()
    cat   = category.lower()
    typ   = product_type.lower()
    width = extract_width(model_number)

    if row == "BS":
        return "BULK", width, False

    if any(k in cat for k in _SMALL_CAT_KW) or any(k in typ for k in _SMALL_TYPE_KW):
        return "SMALL", width, False
    if "accessor" in cat or "accessor" in typ:
        return "SMALL", width, False

    if "dishwasher" in cat or "undercounter" in typ:
        return "DISHWASHER", width, False

    if "laundry" in cat or "washer" in cat or "dryer" in cat:
        return "LAUNDRY", width, False

    if "refriger" in cat or "refer" in cat:
        if any(k in typ for k in _BUILTIN_TYPE_KW):
            return "BUILTIN_REFER", width, False
        if any(k in typ for k in _FREESTAND_TYPE_KW):
            return "FREESTANDING_REFER", width, False
        return "BUILTIN_REFER", width, False

    if "cooking" in cat or "range" in cat or "wall oven" in cat:
        if any(k in typ for k in ("cooktop", "wall oven", "warming")):
            return "OTHER", width, False
        if any(k in typ for k in _SLIDEIN_TYPE_KW):
            return "SLIDEIN_RANGE", width, False
        if width is not None and width >= 36:
            return "LARGE_RANGE", width, True
        if row in _FLOOR_ROWS:
            return "LARGE_RANGE", width, True
        return "SLIDEIN_RANGE", width, False

    return "OTHER", width, False


# ── Size tier derivation ──────────────────────────────────────────────────────

# Bin slot heights in METRES (from warehouse layout levelHeights).
_BIN_TIER_BREAKPOINTS: tuple[tuple[float, int], ...] = (
    (1.0, 1),  # h < 1.0  → XS
    (1.5, 2),  # h < 1.5  → SM
    (2.0, 3),  # h < 2.0  → MD
    (2.5, 4),  # h < 2.5  → LG
)
_BIN_TIER_MAX = 5  # h ≥ 2.5 → XL


def tier_from_bin_height_m(height_m: float) -> int:
    """Derive size_tier from a bin's physical slot height (metres)."""
    for threshold, tier in _BIN_TIER_BREAKPOINTS:
        if height_m < threshold:
            return tier
    return _BIN_TIER_MAX


# Product heights in INCHES (from external dim catalogs).
_PRODUCT_TIER_BREAKPOINTS: tuple[tuple[float, int], ...] = (
    (12.0, 1),  # h < 12"  → XS
    (24.0, 2),  # h < 24"  → SM
    (52.0, 3),  # h < 52"  → MD
    (68.0, 4),  # h < 68"  → LG
)
_PRODUCT_TIER_MAX = 5  # h ≥ 68" → XL


def tier_from_product_height_in(height_in: float) -> int:
    """Derive size_tier from a product's stated height (inches)."""
    for threshold, tier in _PRODUCT_TIER_BREAKPOINTS:
        if height_in < threshold:
            return tier
    return _PRODUCT_TIER_MAX
