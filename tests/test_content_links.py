from __future__ import annotations

from src.content_links import extract_html_links, module_external_links


def test_html_links_keep_context_but_do_not_return_canvas_links() -> None:
    links = extract_html_links(
        '<p><a href="https://external.example/book?token=secret">Reference book</a>'
        '<img src="https://images.example/diagram.png" alt="Force diagram">'
        '<a href="/courses/1/pages/internal">Internal</a></p>',
        canvas_base_url="https://canvas.example.edu",
        source="pages/week-one.html",
        title="Week one",
    )

    assert [link["link_text"] for link in links] == ["Reference book", "Force diagram"]
    assert "token" not in links[0]["url"]
    assert links[0]["source"] == "pages/week-one.html"


def test_module_external_links_keep_module_and_item_context() -> None:
    links = module_external_links(
        [
            {
                "id": 2,
                "name": "Reading",
                "items": [
                    {
                        "id": 3,
                        "type": "ExternalUrl",
                        "title": "Article",
                        "external_url": "https://external.example/article",
                        "position": 4,
                    }
                ],
            }
        ],
        canvas_base_url="https://canvas.example.edu",
    )

    assert links == [
        {
            "url": "https://external.example/article",
            "title": "Article",
            "module_id": 2,
            "module_name": "Reading",
            "module_item_id": 3,
            "position": 4,
            "source": "module_item",
        }
    ]
