"""
Unit utilities for building LLM prompt sections and alias maps from DB.
"""

import logging
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)

FALLBACK_UNITS: List[Tuple[str, List[str]]] = [
    ("kg", ["kilogram", "quilo", "quilos"]),
    ("litre", ["litro", "litros", "l"]),
    ("unit", ["unidade", "unidades", "pç", "pc"]),
]


def _load_active_units() -> List[Tuple[str, List[str]]]:
    """Return [(canonical, [aliases])] from DB. Falls back to hardcoded list."""
    try:
        from .models import Unit
        rows = Unit.objects.filter(is_active=True).order_by("sort_order", "name")
        result = [(row.name, list(row.aliases or [])) for row in rows]
        if result:
            return result
    except Exception:
        logger.debug("Could not load units from DB, using fallback")
    return list(FALLBACK_UNITS)


def get_units_for_prompt() -> str:
    """
    Build a concise, structured units section for the LLM prompt.

    Example output:
        UNITS (use ONLY these canonical names in the 'unit' field):
        - kg: matches kilogram, quilo, quilos
        - litre: matches litro, litros, l
        - unit: matches unidade, unidades, pç, pc
        If the text mentions a variant listed above, output the canonical name.
        Empty string if no unit applies.
    """
    units = _load_active_units()
    if not units:
        return ""

    lines = ["UNITS (use ONLY these canonical names in the 'unit' field):"]
    for canonical, aliases in units:
        if aliases:
            lines.append(f"- {canonical}: matches {', '.join(aliases)}")
        else:
            lines.append(f"- {canonical}")
    lines.append(
        "If the text mentions a variant listed above, output the canonical name. "
        "Empty string if no unit applies."
    )
    return "\n".join(lines)


def get_unit_alias_map() -> Dict[str, str]:
    """
    Build {alias_lower: canonical_name} from active Unit rows.

    Includes both the canonical name itself and all aliases.
    """
    units = _load_active_units()
    alias_map: Dict[str, str] = {}
    for canonical, aliases in units:
        alias_map[canonical.lower()] = canonical
        for alias in aliases:
            alias_map[alias.strip().lower()] = canonical
    return alias_map
