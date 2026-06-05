from datetime import date

from paperradar.sources.arxiv import announcement_window, in_window


def test_arxiv_announcement_window_for_monday() -> None:
    start, end = announcement_window({"announcement_date": "2026-06-01"})
    assert start.date() == date(2026, 5, 28)
    assert end.date() == date(2026, 5, 29)
    assert in_window("2026-05-28T18:30:00Z", (start, end))
    assert not in_window("2026-05-29T19:00:00Z", (start, end))
