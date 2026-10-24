"""Field-level validation and normalisation.

Shared by the REST API (via the pydantic schemas) and the voice agent tools, so a
value is judged identically whichever way it arrives.

Every validator takes the raw value and returns the normalised value, or raises
``ValueError`` with a message that is safe to read aloud to a caller - the voice
agent relays these messages verbatim when it re-prompts for a field.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any, Callable

from email_validator import EmailNotValidError, validate_email

SEX_VALUES = ("Male", "Female", "Other", "Decline to Answer")

REQUIRED_FIELDS = (
    "first_name",
    "last_name",
    "date_of_birth",
    "sex",
    "phone_number",
    "address_line_1",
    "city",
    "state",
    "zip_code",
)
OPTIONAL_FIELDS = (
    "email",
    "address_line_2",
    "insurance_provider",
    "insurance_member_id",
    "preferred_language",
    "emergency_contact_name",
    "emergency_contact_phone",
)
PATIENT_FIELDS = REQUIRED_FIELDS + OPTIONAL_FIELDS

FIELD_LABELS = {
    "first_name": "first name",
    "last_name": "last name",
    "date_of_birth": "date of birth",
    "sex": "sex",
    "phone_number": "phone number",
    "email": "email address",
    "address_line_1": "street address",
    "address_line_2": "apartment or unit",
    "city": "city",
    "state": "state",
    "zip_code": "ZIP code",
    "insurance_provider": "insurance provider",
    "insurance_member_id": "insurance member ID",
    "preferred_language": "preferred language",
    "emergency_contact_name": "emergency contact name",
    "emergency_contact_phone": "emergency contact phone number",
}

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
    "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming",
    # Territories commonly seen on U.S. patient forms.
    "PR": "Puerto Rico", "GU": "Guam", "VI": "Virgin Islands",
    "AS": "American Samoa", "MP": "Northern Mariana Islands",
}
_STATE_BY_NAME = {name.lower(): abbr for abbr, name in US_STATES.items()}

_SEX_ALIASES = {
    "male": "Male", "m": "Male", "man": "Male", "boy": "Male",
    "female": "Female", "f": "Female", "woman": "Female", "girl": "Female",
    "other": "Other", "non-binary": "Other", "nonbinary": "Other",
    "non binary": "Other",
    "decline to answer": "Decline to Answer", "decline": "Decline to Answer",
    "prefer not to say": "Decline to Answer", "prefer not to answer": "Decline to Answer",
    "rather not say": "Decline to Answer", "no answer": "Decline to Answer",
}

# Unicode letters only (no digits/underscore), optionally joined by space, hyphen or apostrophe.
_NAME_RE = re.compile(r"[^\W\d_]+(?:[ '\-][^\W\d_]+)*")
_CITY_RE = re.compile(r"[^\W\d_](?:[^\W\d_]|[ .'\-])*")
_ZIP_RE = re.compile(r"\d{5}(?:-?\d{4})?")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_TAG_CHARS_RE = re.compile(r"[<>]")


def is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _text(value: Any, field: str, *, max_len: int, min_len: int = 1) -> str:
    """Sanitise free text: drop control characters and angle brackets, collapse whitespace."""
    label = FIELD_LABELS[field]
    if is_blank(value):
        raise ValueError(f"The {label} is required.")
    text = str(value).replace("’", "'")
    text = _TAG_CHARS_RE.sub("", _CONTROL_RE.sub(" ", text))
    text = " ".join(text.split())
    if len(text) < min_len:
        raise ValueError(f"The {label} looks too short.")
    if len(text) > max_len:
        raise ValueError(f"The {label} can be at most {max_len} characters.")
    return text


def _person_name(field: str, max_len: int) -> Callable[[Any], str]:
    label = FIELD_LABELS[field]

    def validate(value: Any) -> str:
        text = _text(value, field, max_len=max_len)
        if not _NAME_RE.fullmatch(text):
            raise ValueError(
                f"The {label} can only contain letters, hyphens and apostrophes."
            )
        return text

    return validate


def validate_date_of_birth(value: Any, *, today: date | None = None) -> date:
    if isinstance(value, datetime):
        parsed: date | None = value.date()
    elif isinstance(value, date):
        parsed = value
    else:
        if is_blank(value):
            raise ValueError("The date of birth is required.")
        text = str(value).strip()
        parsed = None
        for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
            try:
                parsed = datetime.strptime(text, fmt).date()
                break
            except ValueError:
                continue
        if parsed is None:
            raise ValueError(
                "I couldn't read that as a real date. The date of birth needs a "
                "month, day and four-digit year, like 03/14/1985."
            )
    today = today or datetime.now(timezone.utc).date()
    if parsed > today:
        raise ValueError(
            "That date of birth is in the future. Please give a date that has already passed."
        )
    if parsed.year < today.year - 125:
        raise ValueError("That year doesn't look right. Please repeat the full date of birth.")
    return parsed


def validate_sex(value: Any) -> str:
    if is_blank(value):
        raise ValueError("The sex is required.")
    key = " ".join(str(value).strip().lower().split())
    if key in _SEX_ALIASES:
        return _SEX_ALIASES[key]
    raise ValueError(
        "The sex needs to be Male, Female, Other, or Decline to Answer."
    )


def _phone(field: str) -> Callable[[Any], str]:
    label = FIELD_LABELS[field]

    def validate(value: Any) -> str:
        if is_blank(value):
            raise ValueError(f"The {label} is required.")
        digits = re.sub(r"\D", "", str(value))
        if len(digits) == 11 and digits.startswith("1"):
            digits = digits[1:]
        if len(digits) != 10:
            raise ValueError(
                f"A U.S. phone number needs exactly 10 digits, but I only have {len(digits)}."
                if len(digits) < 10
                else f"A U.S. phone number needs exactly 10 digits, but I have {len(digits)}."
            )
        if digits[0] in "01" or digits[3] in "01":
            raise ValueError(
                f"That doesn't look like a valid U.S. {label}; the area code and the "
                "next three digits can't start with 0 or 1."
            )
        return digits

    return validate


def validate_email_address(value: Any) -> str:
    if is_blank(value):
        raise ValueError("The email address is required.")
    text = str(value).strip()
    if len(text) > 254:
        raise ValueError("That email address is too long.")
    try:
        return validate_email(text, check_deliverability=False).normalized.lower()
    except EmailNotValidError:
        raise ValueError(
            "That doesn't look like a valid email address. It needs a name, an at sign and a domain."
        ) from None


def validate_address_line_1(value: Any) -> str:
    return _text(value, "address_line_1", max_len=100, min_len=3)


def validate_address_line_2(value: Any) -> str:
    return _text(value, "address_line_2", max_len=100)


def validate_city(value: Any) -> str:
    text = _text(value, "city", max_len=100)
    if not _CITY_RE.fullmatch(text):
        raise ValueError("The city can only contain letters, spaces, periods, hyphens and apostrophes.")
    return text


def validate_state(value: Any) -> str:
    if is_blank(value):
        raise ValueError("The state is required.")
    text = " ".join(str(value).replace(".", "").strip().split())
    if text.upper() in US_STATES:
        return text.upper()
    if text.lower() in _STATE_BY_NAME:
        return _STATE_BY_NAME[text.lower()]
    raise ValueError("That isn't a U.S. state I recognise. Please say the state name or its two-letter code.")


def validate_zip_code(value: Any) -> str:
    if is_blank(value):
        raise ValueError("The ZIP code is required.")
    text = re.sub(r"\s+", "", str(value))
    if not _ZIP_RE.fullmatch(text):
        raise ValueError("A ZIP code needs 5 digits, or 9 digits for ZIP plus four.")
    digits = text.replace("-", "")
    return digits if len(digits) == 5 else f"{digits[:5]}-{digits[5:]}"


def validate_insurance_provider(value: Any) -> str:
    return _text(value, "insurance_provider", max_len=100)


def validate_insurance_member_id(value: Any) -> str:
    if is_blank(value):
        raise ValueError("The insurance member ID is required.")
    compact = re.sub(r"[\s\-]", "", str(value))
    if not compact.isascii() or not compact.isalnum():
        raise ValueError("An insurance member ID can only contain letters and numbers.")
    if len(compact) > 50:
        raise ValueError("That insurance member ID is too long.")
    return compact.upper()


def validate_preferred_language(value: Any) -> str:
    text = _text(value, "preferred_language", max_len=50)
    if not re.fullmatch(r"[^\W\d_]+(?:[ \-][^\W\d_]+)*", text):
        raise ValueError("The preferred language can only contain letters.")
    return text.title()


FIELD_VALIDATORS: dict[str, Callable[[Any], Any]] = {
    "first_name": _person_name("first_name", 50),
    "last_name": _person_name("last_name", 50),
    "date_of_birth": validate_date_of_birth,
    "sex": validate_sex,
    "phone_number": _phone("phone_number"),
    "email": validate_email_address,
    "address_line_1": validate_address_line_1,
    "address_line_2": validate_address_line_2,
    "city": validate_city,
    "state": validate_state,
    "zip_code": validate_zip_code,
    "insurance_provider": validate_insurance_provider,
    "insurance_member_id": validate_insurance_member_id,
    "preferred_language": validate_preferred_language,
    "emergency_contact_name": _person_name("emergency_contact_name", 100),
    "emergency_contact_phone": _phone("emergency_contact_phone"),
}


def required_validator(field: str) -> Callable[[Any], Any]:
    """Validator for a required field: blank is an error."""
    return FIELD_VALIDATORS[field]


def optional_validator(field: str, default: Any = None) -> Callable[[Any], Any]:
    """Validator for an optional field: blank means "not provided" (or the default)."""
    validate = FIELD_VALIDATORS[field]

    def check(value: Any) -> Any:
        return default if is_blank(value) else validate(value)

    return check


def validate_field(field: str, value: Any) -> Any:
    """Validate one named field (blank is an error, even for optional fields)."""
    if field not in FIELD_VALIDATORS:
        raise KeyError(field)
    return FIELD_VALIDATORS[field](value)


def normalize_spoken_email(value: Any) -> Any:
    """Turn 'jane dot doe at gmail dot com' (speech-to-text output) into 'jane.doe@gmail.com'."""
    if not isinstance(value, str) or "@" in value:
        return value
    text = f" {value.strip().lower()} "
    text = text.replace(" at ", "@").replace(" dot ", ".").replace(" underscore ", "_")
    text = text.replace(" dash ", "-").replace(" hyphen ", "-")
    return re.sub(r"\s+", "", text)
