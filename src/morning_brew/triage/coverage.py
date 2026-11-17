"""Deterministic coverage check over the model's risk booleans (FR-056).

A model that says "all clear" on a caveat whose text carries a risk cue
cannot cause an upgrade: every cue present must have its boolean set, and
any risk boolean set blocks the upgrade. ``boilerplate_only`` is advisory and
never overrides a flagged risk (coordinator ruling R42).

Documented limits of the closed cue lexicon (these rely on the model's
booleans, not on this check):
- look-alike letters inside ASCII ("MlGRATE" with a lowercase l);
- non-English stems ("migracion" with an accent has no ``migrat``);
- paraphrases with no cue word ("convert your database with x upgrade-db").
"""

import re
import unicodedata

from morning_brew.textsafe import strip_control
from morning_brew.triage.schemas import RiskAssessment
from morning_brew.triage.suspicious import INVISIBLE_CHARS

_FLAGS = re.IGNORECASE

MIGRATION_CUES = re.compile(r"migrat", _FLAGS)
DATA_FORMAT_CUES = re.compile(r"data\s+format|schema|incompatib|reindex|rewrit", _FLAGS)
RESTART_CUES = re.compile(r"restart|reload|reboot|launchctl|brew\s+services", _FLAGS)
MANUAL_STEP_CUES = re.compile(
    r"you\s+must|you\s+need\s+to|manually|run\s+the\s+following|please\s+run",
    _FLAGS,
)

_CUES = (
    (MIGRATION_CUES, "mentions_migration"),
    (DATA_FORMAT_CUES, "mentions_data_format"),
    (RESTART_CUES, "mentions_restart"),
    (MANUAL_STEP_CUES, "mentions_manual_step"),
)
# Punctuation splicing a word ("mi-grate", "re.start") is closed up.
_SPLICE = re.compile(r"(?<=[A-Za-z])[-_.*`'\u00b7](?=[A-Za-z])")
_RISK_FIELDS = tuple(field for _, field in _CUES)


def _views(caveat: str) -> tuple[str, ...]:
    """The raw text plus the forms a model may read it as.

    Cues hidden by control or invisible characters, full-width look-alikes,
    or punctuation splicing a word still count.
    """
    visible = INVISIBLE_CHARS.sub("", strip_control(caveat))
    folded = unicodedata.normalize("NFKC", visible)
    return (caveat, visible, folded, _SPLICE.sub("", folded))


def coverage_ok(caveat: str | None, a: RiskAssessment) -> bool:
    if not caveat:
        return True
    views = _views(caveat)
    for pattern, field in _CUES:
        if any(pattern.search(v) for v in views) and not getattr(a, field):
            return False
    return True


def accepts_upgrade(caveat: str | None, a: RiskAssessment) -> bool:
    if a.verdict != "upgrade" or not coverage_ok(caveat, a):
        return False
    return not any(getattr(a, f) for f in _RISK_FIELDS)
