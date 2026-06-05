from paperradar.sources.rss import extract_doi, extract_feed_links


def test_extract_doi() -> None:
    assert extract_doi("https://doi.org/10.1234/ABC.DEF") == "10.1234/ABC.DEF"


def test_extract_feed_links() -> None:
    html = '<link rel="alternate" type="application/rss+xml" href="/feed.xml"><a href="/rss">RSS</a>'
    links = extract_feed_links(html)
    assert ("/feed.xml", "alternate feed link") in links
    assert any(link[0] == "/rss" for link in links)
