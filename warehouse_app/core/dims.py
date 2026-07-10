# Owns: pure parsing of physical and carton dimensions from structured data.
# Must not: import from adapters, services, infrastructure, or config.
# May import: standard library only.
from __future__ import annotations

import re

# All dimension keys returned by every parser in this module.
_ALL_KEYS: tuple[str, ...] = (
    "width_in",
    "height_in",
    "depth_in",
    "carton_w_in",
    "carton_h_in",
    "carton_d_in",
    "gross_weight_lb",
)

# Matches concatenated dimension strings from external catalog JSON, e.g.:
# "38.625 Height (In)27 Width (In)32.9375 Depth (In)"
_DIM_STR_RE = re.compile(
    r"([\d.]+)\s*Height\s*\(In\)"
    r"([\d.]+)\s*Width\s*\(In\)"
    r"([\d.]+)\s*Depth\s*\(In\)",
    re.IGNORECASE,
)

# Fraction-aware dimension pattern: matches "42", "42.5", "42 7/8", "42-7/8".
_FRACTION_RE = re.compile(
    r"""
    (\d+)               # integer part
    (?:                 # optional fraction
        \s*[-\s]\s*     # separator: hyphen or space
        (\d+)/(\d+)     # numerator/denominator
    )?
    \s*(?:in|\")?       # optional unit
    """,
    re.VERBOSE,
)


def _empty_dims() -> dict[str, float | None]:
    """Return a dict with all seven dimension keys initialised to None."""
    return {k: None for k in _ALL_KEYS}


def _spec_rows(data: dict) -> list[dict]:
    """Return SpecificationTable rows from the top-level experience block."""
    exp = data.get("experience") or {}
    for exp_type in (exp.get("experiences") or {}).values():
        for widget in (exp_type.get("widgets") or {}).values():
            if widget.get("widgetType") == "SpecificationTable":
                return widget.get("rows", [])
    return []


def _parse_dim_text(text: str) -> float | None:
    """Parse a raw dimension string into a float (inches).

    Handles plain integers, decimals, and mixed-number fractions:
        "42"       → 42.0
        "42.5"     → 42.5
        "42 7/8"   → 42.875
        "42-7/8"   → 42.875

    Returns None if the string cannot be parsed.
    """
    text = text.strip()
    try:
        return float(text)
    except ValueError:
        pass
    m = _FRACTION_RE.match(text)
    if not m:
        return None
    whole = float(m.group(1))
    if m.group(2) and m.group(3):
        whole += int(m.group(2)) / int(m.group(3))
    return whole


def parse_catalog_dims(data: dict) -> dict[str, float | None]:
    """Parse physical and carton dimensions from an external catalog product JSON.

    Reads the SpecificationTable widget in the experience block to extract:
      - Product dimensions  (width_in, height_in, depth_in)
      - Carton dimensions   (carton_w_in, carton_h_in, carton_d_in)
      - Gross shipping weight (gross_weight_lb)

    All seven keys are always present in the returned dict; keys not found in
    the data are set to None.
    """
    result = _empty_dims()
    rows = _spec_rows(data)
    if not rows:
        return result

    for row in rows:
        caption = row.get("caption", "")
        cells = row.get("cells", [])

        # Product dims appear as a single concatenated caption string.
        m = _DIM_STR_RE.search(caption)
        if m:
            result["height_in"] = float(m.group(1))
            result["width_in"]  = float(m.group(2))
            result["depth_in"]  = float(m.group(3))
            continue

        # Carton / shipping dims: caption like "Dimensions|carton width",
        # numeric value in cells[1].
        if len(cells) < 2:
            continue
        cap_low = caption.lower()
        val_text = cells[1].get("text", "").replace(",", "")
        try:
            val = float(val_text)
        except ValueError:
            continue

        if "carton width" in cap_low:
            result["carton_w_in"] = float(int(val))
        elif "carton height" in cap_low:
            result["carton_h_in"] = float(int(val))
        elif "carton depth" in cap_low:
            result["carton_d_in"] = float(int(val))
        elif "gross weight" in cap_low:
            result["gross_weight_lb"] = float(int(val))

    return result


def parse_source_page_dims(html: str) -> dict[str, float | None]:
    """Parse Width, Height, and Depth from a product page HTML fragment.

    Recognises bold-label dimension entries such as:
        <b>Width</b>: 42.0 in
        <b>Height</b>: 42 7/8 in
        <b>Depth</b>: 32-7/8 in

    Fraction notation is fully supported (e.g. "42 7/8" → 42.875).
    Carton-dimension and weight keys are always None; this parser covers
    physical product dimensions only.

    All seven keys are always present in the returned dict.
    """
    result = _empty_dims()
    for label, key in [
        ("Width",  "width_in"),
        ("Height", "height_in"),
        ("Depth",  "depth_in"),
    ]:
        m = re.search(
            rf'<b>{re.escape(label)}</b>\s*:\s*([\d]+(?:[\s\-][\d]+/[\d]+)?)',
            html,
            re.IGNORECASE,
        )
        if m:
            val = _parse_dim_text(m.group(1))
            if val is not None and val > 0:
                result[key] = val
    return result
