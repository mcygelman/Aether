from memory_fabric.clock import FixedClock, add_days, days_between, parse_iso, to_iso


def test_round_trip_and_z_suffix():
    assert to_iso(parse_iso("2026-10-06T09:00:00Z")) == "2026-10-06T09:00:00Z"
    assert to_iso(parse_iso("2026-10-06T05:00:00-04:00")) == "2026-10-06T09:00:00Z"


def test_days_between_is_fractional_and_signed():
    assert days_between("2026-10-06T00:00:00Z", "2026-10-07T12:00:00Z") == 1.5
    assert days_between("2026-10-07T00:00:00Z", "2026-10-06T00:00:00Z") == -1.0


def test_fixed_clock_moves_only_when_told():
    clock = FixedClock("2026-10-06T09:00:00Z")
    assert clock.now() == "2026-10-06T09:00:00Z"
    assert clock.advance(2) == add_days("2026-10-06T09:00:00Z", 2)
    clock.set("2027-01-01T00:00:00Z")
    assert clock.now() == "2027-01-01T00:00:00Z"
