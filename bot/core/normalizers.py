"""
bot/core/normalizers.py

Shared text-normalization helpers used across handlers.

FIX PHASE5-HIGH-51: Persian-digit normalization was only applied in
transfer.py (via a local _DIGIT_TRANS). start.py and profile_edit.py
did NOT normalize Persian/Arabic digits before int(), causing:
  - Onboarding stall: int("۲۵") → ValueError → user stuck.
  - Profile-edit crash: "۲۵".isdigit() returns True (Python treats Persian
    digits as digits), so the guard passes, then int("۲۵") raises
    unhandled ValueError → user gets no reply.

Now all three handlers import `normalize_digits` from this module.
"""

# Persian (۰-۹) and Arabic (٠-٩) digit sets.
_PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
_ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"

# str.maketrans maps each character in the first arg to the corresponding
# character in the second arg. We map both Persian AND Arabic digits to
# ASCII digits, so "۲۵" → "25" and "٢٥" → "25".
DIGIT_TRANS = str.maketrans(
    _PERSIAN_DIGITS + _ARABIC_DIGITS,
    "0123456789" + "0123456789",
)


def normalize_digits(text: str) -> str:
    """Convert Persian/Arabic digits in `text` to ASCII digits.

    Examples:
        normalize_digits("۲۵")        → "25"
        normalize_digits("سن: ۳۰")    → "سن: 30"
        normalize_digits("1۰۰۰")      → "1000"   (mixed Persian + ASCII)
    """
    if not text:
        return text
    return text.translate(DIGIT_TRANS)


def parse_int_normalized(text: str, default: int | None = None) -> int | None:
    """Parse an integer from text that may contain Persian/Arabic digits.

    Returns `default` if parsing fails (instead of raising ValueError).
    """
    if not text:
        return default
    normalized = normalize_digits(text).strip()
    try:
        return int(normalized)
    except ValueError:
        return default
