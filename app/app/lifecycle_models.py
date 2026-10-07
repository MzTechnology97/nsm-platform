"""Vendor lifecycle catalog (LIFE-01): one record per vendor model with its source."""
# The table lives in app.models so that Device's foreign key always resolves.
from app.models import LifecycleRecord  # noqa: F401

# How a Device got (or did not get) its lifecycle data.
MATCH_LABELS = {
    "catalog": "Da catalogo",
    "manual": "Impostato manualmente",
    "ambiguous": "Corrispondenza ambigua",
    "no_record": "Modello non in catalogo",
    "no_model": "Modello non rilevato",
}
