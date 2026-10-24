from datetime import date

import pytest

from app import validators as v


def test_phone_normalises_formats():
    assert v.validate_field("phone_number", "(212) 555-0134") == "2125550134"
    assert v.validate_field("phone_number", "+1 212 555 0134") == "2125550134"


@pytest.mark.parametrize("bad", ["555", "12345678901234", "abcdefghij", "0125550134", "2120550134"])
def test_phone_rejects_bad_numbers(bad):
    with pytest.raises(ValueError):
        v.validate_field("phone_number", bad)


def test_short_phone_message_is_specific():
    with pytest.raises(ValueError, match="10 digits.*3"):
        v.validate_field("phone_number", "555")


def test_dob_formats_and_future_rejected():
    assert v.validate_date_of_birth("03/14/1985") == date(1985, 3, 14)
    assert v.validate_date_of_birth("1985-03-14") == date(1985, 3, 14)
    with pytest.raises(ValueError, match="future"):
        v.validate_date_of_birth("01/01/2999")
    with pytest.raises(ValueError):
        v.validate_date_of_birth("02/30/1990")
    with pytest.raises(ValueError):
        v.validate_date_of_birth("March 14th")
    with pytest.raises(ValueError, match="year"):
        v.validate_date_of_birth("01/01/1850")


def test_names_allow_hyphen_and_apostrophe_but_not_digits():
    assert v.validate_field("last_name", "O'Brien") == "O'Brien"
    assert v.validate_field("first_name", "Mary-Jane") == "Mary-Jane"
    for bad in ("R2D2", "", "   ", "x" * 51):
        with pytest.raises(ValueError):
            v.validate_field("first_name", bad)


def test_html_is_stripped_from_free_text():
    assert v.validate_field("address_line_1", "12 <b>Oak</b> St") == "12 bOak/b St"


def test_state_accepts_code_or_name():
    assert v.validate_field("state", "ny") == "NY"
    assert v.validate_field("state", "California") == "CA"
    with pytest.raises(ValueError):
        v.validate_field("state", "XX")


def test_zip_formats():
    assert v.validate_field("zip_code", "11201") == "11201"
    assert v.validate_field("zip_code", "112011234") == "11201-1234"
    with pytest.raises(ValueError):
        v.validate_field("zip_code", "1120")


def test_sex_aliases():
    assert v.validate_field("sex", "female") == "Female"
    assert v.validate_field("sex", "prefer not to say") == "Decline to Answer"
    with pytest.raises(ValueError):
        v.validate_field("sex", "purple")


def test_spoken_email():
    assert v.normalize_spoken_email("jane dot doe at gmail dot com") == "jane.doe@gmail.com"
    assert v.validate_field("email", "Jane.Doe@Gmail.com") == "jane.doe@gmail.com"
    with pytest.raises(ValueError):
        v.validate_field("email", "not-an-email")


def test_member_id_alphanumeric():
    assert v.validate_field("insurance_member_id", "xjk-482 910") == "XJK482910"
    with pytest.raises(ValueError):
        v.validate_field("insurance_member_id", "abc!123")
