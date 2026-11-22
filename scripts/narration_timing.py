"""Duration limits shared by the workbench and its frozen voice engine."""
import math

MINUTE_TARGET = 60
MINUTE_MINIMUM = 55
MINUTE_LIMIT = 70
MAX_TEMPO = 1.15
TOLERANCE = 5


def within_target(seconds, target, *, legacy_minute=False):
    if not math.isfinite(seconds) or seconds <= 0:
        return False
    if target == MINUTE_TARGET:
        return (legacy_minute or seconds >= MINUTE_MINIMUM) and seconds <= MINUTE_LIMIT
    return target - TOLERANCE <= seconds <= target + TOLERANCE


def duration_rule(target):
    return f'{MINUTE_MINIMUM}–{MINUTE_LIMIT} 秒' if target == MINUTE_TARGET else f'{target - TOLERANCE}–{target + TOLERANCE} 秒'


def frame_duration(audio_seconds, lead, fps):
    return math.ceil((audio_seconds + lead) * fps) / fps


def minute_tempo(audio_seconds, lead, fps):
    if frame_duration(audio_seconds, lead, fps) <= MINUTE_LIMIT:
        return 1.0
    available = MINUTE_LIMIT - lead
    if available <= 0 or audio_seconds / available > MAX_TEMPO:
        return None
    # Allow one frame for codec/filter rounding, then measure the output again.
    return min(MAX_TEMPO, audio_seconds / (available - 1 / fps))
