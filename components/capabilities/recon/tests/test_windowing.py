"""Which window each arrival of a stream falls in (rulings B3, B4, B7): a pure function of the
arrival's ordinal, its clock stamp and the window spec, so it is tested here with no clock, no
thread and no file."""

from __future__ import annotations

import math
from typing import get_args

import pytest
from pydantic import ValidationError

from recon.domain.stream import (
    SealReason,
    WindowEnd,
    Windowing,
    WindowSpec,
    window_id,
    window_index,
)


def _assign(
    spec: WindowSpec, arrivals: list[tuple[float, int]], *, until: float | None = None
) -> tuple[list[int | None], list[WindowEnd]]:
    """The window of each arrival (stamp, bytes), and every window end in order, from a stream
    opened at 0."""
    windows = Windowing(spec, 0.0, until=until)
    placed: list[int | None] = []
    ends: list[WindowEnd] = []
    for stamp, size in arrivals:
        before, window, after = windows.arrive(stamp, size)
        ends += before
        placed.append(window)
        ends += after
    return placed, ends


class TestTimeWindowsAreHalfOpen:
    def test_the_boundaries_fall_on_the_open_side(self) -> None:
        stamps = [0.0, 59.999, 60.0, 60.001]
        placed, ends = _assign(WindowSpec(seconds=60), [(s, 1) for s in stamps])

        assert placed == [0, 0, 1, 1]
        assert ends == [WindowEnd(0, "seconds", 0.0, 60.0)], (
            "window 0 is [0, 60): it ends when the arrival at 60.0 comes, at 60.0"
        )

    def test_a_quiet_stretch_ends_each_window_it_spans_on_its_grid(self) -> None:
        windows = Windowing(WindowSpec(seconds=10), 0.0)

        assert windows.advance(9.999) == []
        ends = windows.advance(35.0)

        assert [(e.index, e.opened, e.closed) for e in ends] == [
            (0, 0.0, 10.0),
            (1, 10.0, 20.0),
            (2, 20.0, 30.0),
        ]
        assert (windows.index, windows.opened, windows.close) == (3, 30.0, 40.0)

    def test_until_ends_the_window_it_cuts_and_every_later_arrival_is_in_none(self) -> None:
        placed, ends = _assign(WindowSpec(seconds=60), [(10.0, 1), (70.0, 1), (95.0, 1)], until=90)

        assert placed == [0, 1, None]
        assert [(e.index, e.reason, e.closed) for e in ends] == [
            (0, "seconds", 60.0),
            (1, "until", 90.0),
        ]


class TestCountWindows:
    @pytest.mark.parametrize(("n", "k"), [(10, 3), (9, 3), (1, 5), (7, 1)])
    def test_n_records_k_a_window_make_the_ceiling_of_n_over_k_windows(
        self, n: int, k: int
    ) -> None:
        placed, ends = _assign(WindowSpec(records=k), [(float(i), 10) for i in range(n)])

        assert len(set(placed)) == math.ceil(n / k)
        assert placed == [i // k for i in range(n)]
        assert [e.reason for e in ends] == ["records"] * (n // k)

    def test_a_window_that_reaches_its_bytes_ends_after_that_record(self) -> None:
        placed, ends = _assign(WindowSpec(bytes=100), [(0.0, 60), (1.0, 40), (2.0, 10)])

        assert placed == [0, 0, 1]
        assert [(e.index, e.reason, e.closed) for e in ends] == [(0, "bytes", 1.0)]

    def test_the_window_after_a_count_end_opens_where_it_ended_and_times_from_there(
        self,
    ) -> None:
        placed, ends = _assign(
            WindowSpec(seconds=60, records=2), [(5.0, 1), (7.0, 1), (66.0, 1), (67.0, 1)]
        )

        assert placed == [0, 0, 1, 2], "window 1 is [7, 67): the arrival at 67 opens window 2"
        assert [(e.index, e.reason, e.opened, e.closed) for e in ends] == [
            (0, "records", 0.0, 7.0),
            (1, "seconds", 7.0, 67.0),
        ]


class TestTheAssignmentIsAPureFunction:
    def test_the_same_arrivals_give_the_same_windows(self) -> None:
        arrivals = [(i * 0.7, 10 + i % 7) for i in range(200)]
        spec = WindowSpec(seconds=13, records=17, bytes=150)

        assert _assign(spec, arrivals) == _assign(spec, arrivals)

    def test_moving_one_arrival_across_a_boundary_changes_the_windows(self) -> None:
        spec = WindowSpec(seconds=10)
        arrivals = [(1.0, 1), (9.5, 1), (12.0, 1)]
        moved = [(1.0, 1), (10.5, 1), (12.0, 1)]

        assert _assign(spec, arrivals)[0] != _assign(spec, moved)[0]


class TestTheSpec:
    @pytest.mark.parametrize(
        ("bound", "arrivals"),
        [
            ({"seconds": 10}, [(0.0, 1), (10.0, 1)]),
            ({"records": 1}, [(0.0, 1)]),
            ({"bytes": 10}, [(0.0, 10)]),
        ],
    )
    def test_a_window_its_own_bound_ends_is_closed_by_that_bound_s_key(
        self, bound: dict[str, int], arrivals: list[tuple[float, int]]
    ) -> None:
        """``closed_by`` names the bound by the key ``window:`` sets it with, so a reader
        finds why a window closed under the word it wrote."""
        [key] = bound
        _, ends = _assign(WindowSpec.model_validate(bound), arrivals)

        assert ends[0].reason == key
        assert set(WindowSpec.model_fields) <= set(get_args(SealReason))

    def test_a_window_needs_one_bound(self) -> None:
        with pytest.raises(ValidationError, match="at least one of seconds, records, bytes"):
            WindowSpec()

    @pytest.mark.parametrize(
        "bad", [{"seconds": 0}, {"seconds": float("inf")}, {"records": 0}, {"bytes": -1}]
    )
    def test_each_bound_is_above_zero_and_finite(self, bad: dict[str, float]) -> None:
        with pytest.raises(ValidationError):
            WindowSpec.model_validate(bad)


class TestWindowIds:
    def test_an_id_holds_the_index_and_sorts_by_it(self) -> None:
        from datetime import UTC, datetime

        at = datetime(2026, 10, 24, 1, 2, 3, 456789, tzinfo=UTC)
        ids = [window_id(i, at) for i in (0, 9, 10, 123)]

        assert ids[0] == "00000000-2026-10-24-010203-456789"
        assert [window_index(i) for i in ids] == [0, 9, 10, 123]
        assert sorted(ids) == ids
        assert window_index("notes") is None
        assert window_index("2026-10-24-010203-456789") is None, "a survey's archive id"
