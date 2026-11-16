"""The stages of an extraction, in the order users see them (IDP-00 ch. 7.5, IDP-03 ch. 2.6)."""

from enum import StrEnum


class Stage(StrEnum):
    """Names used in progress messages and in the stage timings of each run."""

    SCAN = "SCAN"
    QUALITY_CHECK = "QUALITY_CHECK"
    PREPARE = "PREPARE"
    CLASSIFY = "CLASSIFY"
    READ = "READ"
    VERIFY = "VERIFY"
    AI_ASSIST = "AI_ASSIST"
    ASSEMBLE = "ASSEMBLE"
