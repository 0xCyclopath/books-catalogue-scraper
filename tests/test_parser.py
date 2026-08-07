from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
sys.path.insert(0, str(SRC_DIR))

from scraper import parse_catalogue_books, parse_next_page, parse_product_details
from validation import validate_book_row


class ParserTest(unittest.TestCase):
    def test_catalogue_and_product_details_are_parsed(self) -> None:
        catalogue_html = """
        <article class="product_pod">
            <p class="star-rating Three"></p>
            <h3><a href="sample-book/index.html" title="Sample Book">Sample</a></h3>
            <p class="price_color">£12.99</p>
        </article>
        <li class="next"><a href="page-2.html">next</a></li>
        """
        product_html = """
        <ul class="breadcrumb">
            <li><a href="../../index.html">Home</a></li>
            <li><a href="../category/books/mystery_3/index.html">Mystery</a></li>
            <li class="active">Sample Book</li>
        </ul>
        <div class="item active"><img src="../../media/cache/sample.jpg"></div>
        <p class="star-rating Four"></p>
        <p class="instock availability"> In stock (5 available) </p>
        """

        page_url = "http://books.toscrape.com/catalogue/page-1.html"
        books = parse_catalogue_books(catalogue_html, page_url, "2026-08-07T00:00:00+00:00")
        details = parse_product_details(
            product_html,
            "http://books.toscrape.com/catalogue/sample-book/index.html",
        )

        self.assertEqual(len(books), 1)
        self.assertEqual(books[0].title, "Sample Book")
        self.assertEqual(books[0].price, "£12.99")
        self.assertEqual(books[0].rating, "3")
        self.assertEqual(books[0].product_url, "http://books.toscrape.com/catalogue/sample-book/index.html")
        self.assertEqual(parse_next_page(catalogue_html, page_url), "http://books.toscrape.com/catalogue/page-2.html")
        self.assertEqual(details["category"], "Mystery")
        self.assertEqual(details["availability"], "In stock (5 available)")
        self.assertEqual(details["rating"], "4")
        self.assertEqual(details["image_url"], "http://books.toscrape.com/media/cache/sample.jpg")

    def test_validation_reports_missing_and_invalid_values(self) -> None:
        row = {
            "title": "",
            "category": "Mystery",
            "price": "12.99",
            "availability": "In stock",
            "rating": "9",
            "product_url": "not-a-url",
            "image_url": "http://books.toscrape.com/media/cache/sample.jpg",
            "scrape_timestamp": "not-a-date",
        }

        errors = validate_book_row(row)

        self.assertIn("missing title", errors)
        self.assertIn("price must start with £", errors)
        self.assertIn("rating must be between 1 and 5", errors)
        self.assertIn("product_url must be an HTTP URL", errors)
        self.assertIn("scrape_timestamp must be ISO formatted", errors)


if __name__ == "__main__":
    unittest.main()
