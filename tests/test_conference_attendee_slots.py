import pytest
from fastapi import HTTPException

from app.conference.service import attendee_role_of, ensure_attendee_slot


def test_attendee_role_of_defaults():
    assert attendee_role_of(None, None) == "official"
    assert attendee_role_of(None, 4) == "delegate"
    assert attendee_role_of("official", 4) == "official"
    assert attendee_role_of("delegate", None) == "delegate"


def test_ensure_attendee_slot_respects_each_limit():
    ensure_attendee_slot("delegate", 5, 5, 2, 3)
    ensure_attendee_slot("official", 4, 5, 10, 10)
    with pytest.raises(HTTPException) as official_limit:
        ensure_attendee_slot("official", 5, 5, 0, 10)
    assert official_limit.value.status_code == 400
    with pytest.raises(HTTPException) as delegate_limit:
        ensure_attendee_slot("delegate", 0, 5, 10, 10)
    assert delegate_limit.value.status_code == 400
