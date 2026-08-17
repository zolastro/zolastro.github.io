"""Fetch Google Scholar citation data through SerpApi's free tier.

This intentionally does not use scholarly or a proxy. The output keeps the
fields used by AcadHomepage's existing citation JavaScript:
  - citedby
  - publications[author_pub_id].num_citations
"""

import json
import os
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


API_URL = "https://serpapi.com/search.json"
PAGE_SIZE = 100


def fetch_page(author_id, api_key, start=0):
    query = urlencode(
        {
            "engine": "google_scholar_author",
            "author_id": author_id,
            "hl": "en",
            "num": PAGE_SIZE,
            "start": start,
            "api_key": api_key,
        }
    )
    request = Request(
        f"{API_URL}?{query}",
        headers={"User-Agent": "zolastro.github.io citation updater"},
    )

    try:
        with urlopen(request, timeout=90) as response:
            data = json.load(response)
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"SerpApi HTTP {exc.code}: {body[:500]}"
        ) from None
    except URLError as exc:
        raise RuntimeError(f"Could not reach SerpApi: {exc.reason}") from None

    if data.get("error"):
        raise RuntimeError(f"SerpApi error: {data['error']}")

    status = data.get("search_metadata", {}).get("status")
    if status not in (None, "Success"):
        raise RuntimeError(f"SerpApi search status was {status!r}")

    return data


def get_metric(table, row_number):
    """Return (all_time, recent) from a cited_by table row."""
    if row_number >= len(table):
        return 0, 0

    row = table[row_number]
    if not isinstance(row, dict) or not row:
        return 0, 0

    values = next(iter(row.values()))
    if not isinstance(values, dict):
        return 0, 0

    all_time = int(values.get("all", 0) or 0)
    recent = 0
    for key, value in values.items():
        if key != "all":
            recent = int(value or 0)
            break

    return all_time, recent


def convert_publication(article):
    citation_id = article.get("citation_id")
    if not citation_id:
        return None

    cited_by = article.get("cited_by") or {}

    return citation_id, {
        "author_pub_id": citation_id,
        "bib": {
            "title": article.get("title", ""),
            "author": article.get("authors", ""),
            "citation": article.get("publication", ""),
            "pub_year": article.get("year", ""),
        },
        "num_citations": int(cited_by.get("value", 0) or 0),
        "citedby_url": cited_by.get("link", ""),
    }


def main():
    author_id = os.environ.get("GOOGLE_SCHOLAR_ID", "").strip()
    api_key = os.environ.get("SERPAPI_KEY", "").strip()

    if not author_id:
        raise RuntimeError("GOOGLE_SCHOLAR_ID is not configured.")
    if not api_key:
        raise RuntimeError(
            "SERPAPI_KEY is not configured. Create the free SerpApi key "
            "and add it as a GitHub Actions repository secret."
        )

    first_page = fetch_page(author_id, api_key)
    pages = [first_page]

    page = first_page
    start = PAGE_SIZE
    while (page.get("serpapi_pagination") or {}).get("next"):
        page = fetch_page(author_id, api_key, start=start)
        articles = page.get("articles") or []
        if not articles:
            break
        pages.append(page)
        start += PAGE_SIZE

    articles = []
    seen_ids = set()
    for page in pages:
        for article in page.get("articles") or []:
            citation_id = article.get("citation_id")
            if citation_id and citation_id in seen_ids:
                continue
            if citation_id:
                seen_ids.add(citation_id)
            articles.append(article)

    author_info = first_page.get("author") or {}
    cited_by = first_page.get("cited_by") or {}
    table = cited_by.get("table") or []

    citedby, citedby5y = get_metric(table, 0)
    hindex, hindex5y = get_metric(table, 1)
    i10index, i10index5y = get_metric(table, 2)

    publications = {}
    for article in articles:
        converted = convert_publication(article)
        if converted:
            citation_id, publication = converted
            publications[citation_id] = publication

    cites_per_year = {}
    for point in cited_by.get("graph") or []:
        year = point.get("year")
        citations = point.get("citations")
        if year is not None and citations is not None:
            cites_per_year[str(year)] = int(citations)

    author = {
        "container_type": "Author",
        "source": "serpapi-google-scholar-author",
        "scholar_id": author_id,
        "name": author_info.get("name", ""),
        "affiliation": author_info.get("affiliations", ""),
        "email_domain": author_info.get("email", ""),
        "interests": [
            item.get("title")
            for item in author_info.get("interests") or []
            if item.get("title")
        ],
        "url_picture": author_info.get("thumbnail", ""),
        "citedby": citedby,
        "citedby5y": citedby5y,
        "hindex": hindex,
        "hindex5y": hindex5y,
        "i10index": i10index,
        "i10index5y": i10index5y,
        "cites_per_year": cites_per_year,
        "publications": publications,
        "updated": datetime.now(timezone.utc).isoformat(),
    }

    os.makedirs("results", exist_ok=True)

    with open("results/gs_data.json", "w", encoding="utf-8") as outfile:
        json.dump(author, outfile, ensure_ascii=False, indent=2)

    shieldio_data = {
        "schemaVersion": 1,
        "label": "citations",
        "message": str(author["citedby"]),
    }
    with open(
        "results/gs_data_shieldsio.json", "w", encoding="utf-8"
    ) as outfile:
        json.dump(shieldio_data, outfile, ensure_ascii=False, indent=2)

    print(
        f"Updated Scholar data: {author['citedby']} citations, "
        f"{len(publications)} publications."
    )


if __name__ == "__main__":
    main()
