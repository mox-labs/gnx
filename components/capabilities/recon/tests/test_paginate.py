"""How a paginated crawl stops (rulings P1 and P2): pure, so tested here with no requester, no
survey and no file. Each stop is recorded: the end, or ``truncated`` with what cut it."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from recon.domain.paginate import (
    DEFAULT_PAGES,
    EMPTY_PAGES,
    Crawl,
    Next,
    PaginateSpec,
    Stop,
    crawl_floor_s,
    next_link,
)


def _run(spec: PaginateSpec, pages: list[tuple[int, Any]], *, first: Any = None) -> list[Any]:
    """The steps a crawl takes over ``pages`` (records on the page, the next token it gave),
    until it stops; the last is the Stop."""
    crawl = Crawl(spec, first=first)
    steps: list[Any] = []
    for records, found in pages:
        step = crawl.after(records, found)
        steps.append(step)
        if isinstance(step, Stop):
            return steps
    raise AssertionError(f"the crawl did not stop within the script: {steps}")


class TestTheSpec:
    def test_pages_defaults_to_a_cap_and_is_a_count(self) -> None:
        spec = PaginateSpec(type="header")
        assert spec.pages == DEFAULT_PAGES == 100
        with pytest.raises(ValidationError):
            PaginateSpec(type="header", pages=0)

    @pytest.mark.parametrize(
        ("given", "says"),
        [
            ({"type": "link"}, "next"),
            ({"type": "cursor", "param": "cursor"}, "next"),
            ({"type": "cursor", "next": "meta.next"}, "param"),
            ({"type": "offset"}, "param"),
            ({"type": "header", "next": "a"}, "next"),
            ({"type": "header", "param": "p"}, "param"),
            ({"type": "link", "next": "a", "param": "p"}, "param"),
            ({"type": "offset", "param": "o", "next": "a"}, "next"),
        ],
    )
    def test_each_type_takes_its_own_keys_and_needs_them(
        self, given: dict[str, Any], says: str
    ) -> None:
        with pytest.raises(ValidationError, match=says):
            PaginateSpec(**given)

    def test_an_unknown_type_or_key_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            PaginateSpec(type="page")  # type: ignore[arg-type]
        with pytest.raises(ValidationError):
            PaginateSpec(type="header", size=10)  # type: ignore[call-arg]


class TestStopping:
    def test_no_next_token_is_the_end(self) -> None:
        spec = PaginateSpec(type="cursor", next="next", param="c")
        steps = _run(spec, [(2, "a"), (2, "b"), (1, None)])

        assert steps == [Next("a"), Next("b"), Stop(None)]

    def test_an_empty_token_is_no_token(self) -> None:
        spec = PaginateSpec(type="cursor", next="next", param="c")
        assert _run(spec, [(2, "")]) == [Stop(None)]

    def test_reaching_the_cap_with_more_to_read_truncates(self) -> None:
        spec = PaginateSpec(type="cursor", next="next", param="c", pages=3)
        steps = _run(spec, [(1, "a"), (1, "b"), (1, "c")])

        assert steps == [Next("a"), Next("b"), Stop("pages")]

    def test_reaching_the_cap_on_the_last_page_is_the_end(self) -> None:
        spec = PaginateSpec(type="cursor", next="next", param="c", pages=2)
        assert _run(spec, [(1, "a"), (1, None)]) == [Next("a"), Stop(None)]

    def test_a_token_seen_before_stops_as_a_loop_not_only_the_last(self) -> None:
        """A 3-cycle defeats a check against the previous token alone (ixian P2)."""
        spec = PaginateSpec(type="cursor", next="next", param="c")
        steps = _run(spec, [(1, "a"), (1, "b"), (1, "c"), (1, "a")])

        assert steps == [Next("a"), Next("b"), Next("c"), Stop("loop")]

    def test_a_repeat_of_the_previous_token_is_a_loop(self) -> None:
        spec = PaginateSpec(type="link", next="links.next")
        assert _run(spec, [(1, "u2"), (1, "u2")]) == [Next("u2"), Stop("loop")]

    def test_a_next_link_back_to_the_first_page_is_a_loop(self) -> None:
        spec = PaginateSpec(type="link", next="links.next")
        steps = _run(spec, [(1, "u2"), (1, "u1")], first="u1")

        assert steps == [Next("u2"), Stop("loop")], "the first URL is a token seen too"

    def test_a_token_of_another_type_that_prints_alike_is_another_token(self) -> None:
        spec = PaginateSpec(type="cursor", next="next", param="c")
        assert _run(spec, [(1, 2), (1, "2"), (1, None)]) == [Next(2), Next("2"), Stop(None)]

    def test_an_offset_crawl_ends_at_its_first_empty_page(self) -> None:
        spec = PaginateSpec(type="offset", param="offset")
        steps = _run(spec, [(3, None), (3, None), (0, None)])

        assert steps == [Next(3), Next(6), Stop(None)], "the offset is the records read so far"

    def test_a_linked_crawl_reads_past_empty_pages_that_still_link_on(self) -> None:
        spec = PaginateSpec(type="link", next="next")
        steps = _run(spec, [(2, "u2"), (0, "u3"), (0, "u4"), (2, None)])

        assert steps == [Next("u2"), Next("u3"), Next("u4"), Stop(None)]

    def test_empty_pages_in_a_row_that_link_on_stop_it_truncated(self) -> None:
        spec = PaginateSpec(type="link", next="next")
        pages = [(2, "u2")] + [(0, f"u{n}") for n in range(3, 3 + EMPTY_PAGES)]
        steps = _run(spec, pages)

        assert steps[-1] == Stop("empty") and len(steps) == 1 + EMPTY_PAGES
        assert EMPTY_PAGES == 3

    def test_records_between_empty_pages_start_the_count_again(self) -> None:
        spec = PaginateSpec(type="cursor", next="n", param="c")
        pages = [(0, "a"), (0, "b"), (1, "c"), (0, "d"), (0, "e"), (1, None)]
        assert _run(spec, pages)[-1] == Stop(None)


class TestTheLinkHeader:
    @pytest.mark.parametrize(
        ("header", "found"),
        [
            ('<https://a.test/x?page=2>; rel="next", <https://a.test/x?page=9>; rel="last"',
             "https://a.test/x?page=2"),
            ('<https://a.test/x?page=9>; rel="last", <https://a.test/x?page=2>; rel=next',
             "https://a.test/x?page=2"),
            ('<https://a.test/p2>; rel="prev next"', "https://a.test/p2"),
            ('<https://a.test/p2>; title="a, b"; REL="Next"', "https://a.test/p2"),
            ('<https://a.test/p9>; rel="last"', None),
            ("", None),
            ("garbage", None),
        ],
    )  # fmt: skip
    def test_the_next_relation_is_found(self, header: str, found: str | None) -> None:
        assert next_link(header) == found


class TestTheFloor:
    def test_the_floor_is_the_pages_past_the_burst_at_the_rate(self) -> None:
        assert crawl_floor_s(100, burst=3, rps=10.0) == pytest.approx(9.7)
        assert crawl_floor_s(2, burst=3, rps=10.0) == 0.0
