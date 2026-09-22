"""Extract external references from Canvas module and HTML content."""

from __future__ import annotations

from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit

from .privacy import redact_sensitive_query


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[dict[str, str]] = []
        self._anchor_indexes: list[int] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        values = dict(attrs)
        attribute = "href" if tag.casefold() in {"a", "link"} else "src"
        target = values.get(attribute)
        if target:
            link = {"tag": tag, "attribute": attribute, "url": target}
            context = values.get("title") or values.get("alt")
            if context:
                link["link_text"] = context.strip()
            self.links.append(link)
            if tag.casefold() == "a":
                self._anchor_indexes.append(len(self.links) - 1)

    def handle_data(self, data: str) -> None:
        if not self._anchor_indexes or not data.strip():
            return
        link = self.links[self._anchor_indexes[-1]]
        existing = link.get("link_text", "")
        link["link_text"] = f"{existing} {data.strip()}".strip()

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "a" and self._anchor_indexes:
            self._anchor_indexes.pop()


def extract_html_links(
    html: str | None,
    *,
    canvas_base_url: str,
    source: str,
    title: str | None = None,
) -> list[dict[str, Any]]:
    """Return external HTTP(S) links without visiting them."""

    if not html:
        return []
    parser = _LinkParser()
    try:
        parser.feed(str(html))
    except (TypeError, ValueError):
        return []

    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    canvas_host = (urlsplit(canvas_base_url).hostname or "").casefold()
    for link in parser.links:
        absolute = urljoin(canvas_base_url.rstrip("/") + "/", link["url"])
        parsed = urlsplit(absolute)
        if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
            continue
        if parsed.hostname.casefold() == canvas_host or absolute in seen:
            continue
        absolute = redact_sensitive_query(absolute)
        seen.add(absolute)
        results.append(
            {
                "url": absolute,
                "source": source,
                "source_title": title,
                "html_tag": link["tag"],
                "link_text": link.get("link_text"),
            }
        )
    return results


def module_external_links(
    modules: list[dict[str, Any]],
    *,
    canvas_base_url: str,
) -> list[dict[str, Any]]:
    """Return external module-item links with structural context."""

    canvas_host = (urlsplit(canvas_base_url).hostname or "").casefold()
    results: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for module in modules:
        for item in module.get("items", []):
            if not isinstance(item, dict):
                continue
            candidate = item.get("external_url")
            if not candidate and str(item.get("type", "")).casefold() == "externalurl":
                candidate = item.get("url")
            if not isinstance(candidate, str):
                continue
            parsed = urlsplit(candidate)
            if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
                continue
            if parsed.hostname.casefold() == canvas_host:
                continue
            candidate = redact_sensitive_query(candidate)
            key = (candidate, str(item.get("id")))
            if key in seen:
                continue
            seen.add(key)
            results.append(
                {
                    "url": candidate,
                    "title": item.get("title"),
                    "module_id": module.get("id"),
                    "module_name": module.get("name"),
                    "module_item_id": item.get("id"),
                    "position": item.get("position"),
                    "source": "module_item",
                }
            )
    return results


__all__ = ["extract_html_links", "module_external_links"]
