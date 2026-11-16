import pytest
from fastapi import HTTPException
from app.main import _approval_ranges_overlap, _validate_approval_steps

def test_approval_steps_accept_sequential_roles():
    assert _validate_approval_steps([["reviewer"], ["admin"]]) == [["reviewer"], ["admin"]]
    assert _validate_approval_steps([["reviewer", "admin"]]) == [["reviewer", "admin"]]

@pytest.mark.parametrize("steps", [
    [],
    [[]],
    [["analyst"]],
    [["reviewer", "reviewer"]],
    [["reviewer", "admin", "reviewer"]],
    [["reviewer"]] * 6,
])
def test_approval_steps_reject_invalid_role_sequences(steps):
    with pytest.raises(HTTPException) as exc:
        _validate_approval_steps(steps)
    assert exc.value.status_code == 400

def test_approval_policy_amount_ranges_are_half_open():
    assert _approval_ranges_overlap(0, 100, 99, 200)
    assert not _approval_ranges_overlap(0, 100, 100, 200)
    assert _approval_ranges_overlap(100, None, 150, 200)
    assert not _approval_ranges_overlap(0, 100, 100, None)
