from __future__ import annotations

import argparse
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from export import FIELDNAMES, write_rows_to_csv, write_rows_to_excel
from validation import validate_book_row


START_URL = "https://books.toscrape.com/catalogue/page-1.html"
OUTPUT_CSV_FILE = Path("output/books.csv")
OUTPUT_EXCEL_FILE = Path("output/books.xlsx")
REQUEST_TIMEOUT = 15
DEFAULT_DELAY_SECONDS = 0.1
DEFAULT_RETRIES = 3
DEFAULT_BACKOFF_FACTOR = 0.5
RATING_VALUES = {
    "One": "1",
    "Two": "2",
    "Three": "3",
    "Four": "4",
    "Five": "5",
}
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Book:
    title: str
    price: str
    category: str
    availability: str
    rating: str
    product_url: str
    image_url: str
    scrape_timestamp: str


@dataclass(frozen=True)
class RequestSettings:
    timeout: int = REQUEST_TIMEOUT
    delay_seconds: float = DEFAULT_DELAY_SECONDS


def create_session(retries: int = DEFAULT_RETRIES, backoff_factor: float = DEFAULT_BACKOFF_FACTOR) -> requests.Session:
    retry_strategy = Retry(
        total=retries,
        connect=retries,
        read=retries,
        status=retries,
        backoff_factor=backoff_factor,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session = requests.Session()
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": "books-catalogue-scraper/1.0"})
    return session


def fetch_page(session: requests.Session, url: str, settings: RequestSettings) -> str:
    if settings.delay_seconds > 0:
        time.sleep(settings.delay_seconds)

    logger.debug("Fetching %s", url)
    response = session.get(url, timeout=settings.timeout)
    response.raise_for_status()
    response.encoding = "utf-8"
    return response.text


def parse_catalogue_books(html: str, page_url: str, scrape_timestamp: str) -> list[Book]:
    soup = BeautifulSoup(html, "html.parser")
    books: list[Book] = []

    for product in soup.select("article.product_pod"):
        title_link = product.select_one("h3 a")
        price = product.select_one(".price_color")

        if title_link is None:
            continue

        title = title_link.get("title") or title_link.get_text(strip=True)
        relative_url = title_link.get("href")

        if not title or not relative_url:
            continue

        books.append(
            Book(
                title=title.strip(),
                price=price.get_text(strip=True) if price else "",
                category="",
                availability="",
                rating=parse_rating(product),
                product_url=urljoin(page_url, relative_url),
                image_url="",
                scrape_timestamp=scrape_timestamp,
            )
        )

    return books


def parse_rating(element: Tag) -> str:
    rating = element
    if "star-rating" not in rating.get("class", []):
        rating = element.select_one(".star-rating")

    if rating is None:
        return ""

    for class_name in rating.get("class", []):
        if class_name in RATING_VALUES:
            return RATING_VALUES[class_name]

    return ""


def parse_product_details(html: str, product_url: str) -> dict[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    breadcrumb_links = soup.select("ul.breadcrumb li a")
    availability = soup.select_one("p.instock.availability")
    rating = soup.select_one("p.star-rating")
    image = soup.select_one(".item.active img")

    return {
        "category": breadcrumb_links[-1].get_text(strip=True) if breadcrumb_links else "",
        "availability": availability.get_text(" ", strip=True) if availability else "",
        "rating": parse_rating(rating) if rating else "",
        "image_url": urljoin(product_url, image.get("src")) if image and image.get("src") else "",
    }


def enrich_book_details(book: Book, session: requests.Session, settings: RequestSettings) -> Book:
    details = parse_product_details(fetch_page(session, book.product_url, settings), book.product_url)

    return Book(
        title=book.title,
        price=book.price,
        category=details["category"],
        availability=details["availability"],
        rating=details["rating"] or book.rating,
        product_url=book.product_url,
        image_url=details["image_url"],
        scrape_timestamp=book.scrape_timestamp,
    )


def parse_next_page(html: str, page_url: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    next_link = soup.select_one("li.next a")

    if next_link is None:
        return None

    href = next_link.get("href")
    if not href:
        return None

    return urljoin(page_url, href)


def book_to_row(book: Book) -> dict[str, str]:
    return {
        "title": book.title,
        "category": book.category,
        "price": book.price,
        "availability": book.availability,
        "rating": book.rating,
        "product_url": book.product_url,
        "image_url": book.image_url,
        "scrape_timestamp": book.scrape_timestamp,
    }


def deduplicate_books(books: Iterable[Book]) -> tuple[list[Book], int]:
    unique_books: list[Book] = []
    seen_urls: set[str] = set()
    duplicate_count = 0

    for book in books:
        if book.product_url in seen_urls:
            duplicate_count += 1
            logger.warning("Duplicate product skipped during export: %s", book.product_url)
            continue

        seen_urls.add(book.product_url)
        unique_books.append(book)

    return unique_books, duplicate_count


def validate_book(book: Book) -> list[str]:
    return validate_book_row(book_to_row(book))


def scrape_catalogue(
    start_url: str = START_URL,
    max_pages: int | None = None,
    max_books: int | None = None,
    session: requests.Session | None = None,
    request_settings: RequestSettings | None = None,
) -> tuple[list[Book], dict[str, int]]:
    session = session or create_session()
    request_settings = request_settings or RequestSettings()
    page_url: str | None = start_url
    seen_urls: set[str] = set()
    books: list[Book] = []
    scrape_timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    metrics = {
        "pages_processed": 0,
        "records_extracted": 0,
        "duplicate_records": 0,
        "catalogue_pages_failed": 0,
        "product_pages_failed": 0,
        "invalid_records": 0,
        "records_complete": 0,
        "records_partial": 0,
    }

    logger.info("Starting scrape")

    while page_url:
        if max_pages is not None and metrics["pages_processed"] >= max_pages:
            logger.info("Reached page limit: %s", max_pages)
            break

        try:
            html = fetch_page(session, page_url, request_settings)
        except requests.RequestException as exc:
            metrics["catalogue_pages_failed"] += 1
            logger.error("Failed to fetch catalogue page: %s (%s)", page_url, exc)
            break

        metrics["pages_processed"] += 1
        logger.info("Processed catalogue page %s: %s", metrics["pages_processed"], page_url)

        for book in parse_catalogue_books(html, page_url, scrape_timestamp):
            if max_books is not None and len(books) >= max_books:
                logger.info("Reached book limit: %s", max_books)
                break

            if book.product_url in seen_urls:
                metrics["duplicate_records"] += 1
                logger.warning("Duplicate product skipped: %s", book.product_url)
                continue

            seen_urls.add(book.product_url)

            try:
                book = enrich_book_details(book, session, request_settings)
            except requests.RequestException as exc:
                metrics["product_pages_failed"] += 1
                logger.error("Failed to fetch product page: %s (%s)", book.product_url, exc)

            validation_errors = validate_book(book)
            if validation_errors:
                metrics["invalid_records"] += 1
                metrics["records_partial"] += 1
                logger.warning(
                    "Validation issue for %s: %s",
                    book.product_url,
                    "; ".join(validation_errors),
                )
            else:
                metrics["records_complete"] += 1

            books.append(book)

        if max_books is not None and len(books) >= max_books:
            break

        page_url = parse_next_page(html, page_url)

    metrics["records_extracted"] = len(books)
    logger.info("Finished scrape")
    return books, metrics


def count_missing_values(rows: Iterable[dict[str, str]]) -> int:
    return sum(1 for row in rows for field in FIELDNAMES if not row.get(field))


def print_summary(metrics: dict[str, int | float], csv_file: Path, excel_file: Path | None = None) -> None:
    print(f"Pages processed: {metrics['pages_processed']}")
    print(f"Records extracted: {metrics['records_extracted']}")
    print(f"Records complete: {metrics['records_complete']}")
    print(f"Records partial: {metrics['records_partial']}")
    print(f"Duplicate records: {metrics['duplicate_records']}")
    print(f"Catalogue pages failed: {metrics['catalogue_pages_failed']}")
    print(f"Product pages failed: {metrics['product_pages_failed']}")
    print(f"Invalid records: {metrics['invalid_records']}")
    print(f"Missing values: {metrics['missing_values']}")
    print(f"Execution time: {metrics['execution_time_seconds']:.2f}s")
    print(f"Output file: {csv_file}")
    if excel_file is not None:
        print(f"Excel file: {excel_file}")


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc

    if parsed < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")

    return parsed


def non_negative_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a non-negative integer") from exc

    if parsed < 0:
        raise argparse.ArgumentTypeError("must be a non-negative integer")

    return parsed


def non_negative_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a non-negative number") from exc

    if parsed < 0:
        raise argparse.ArgumentTypeError("must be a non-negative number")

    return parsed


def setup_logging(log_level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, log_level),
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape book catalogue data.")
    parser.add_argument(
        "--max-pages",
        type=positive_int,
        help="maximum number of catalogue pages to process",
    )
    parser.add_argument(
        "--max-books",
        type=positive_int,
        help="maximum number of books to extract",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=OUTPUT_CSV_FILE,
        help=f"CSV output path (default: {OUTPUT_CSV_FILE})",
    )
    parser.add_argument(
        "--excel-output",
        type=Path,
        default=OUTPUT_EXCEL_FILE,
        help=f"Excel output path (default: {OUTPUT_EXCEL_FILE})",
    )
    parser.add_argument(
        "--no-excel",
        action="store_true",
        help="skip Excel export",
    )
    parser.add_argument(
        "--delay",
        type=non_negative_float,
        default=DEFAULT_DELAY_SECONDS,
        help=f"delay between requests in seconds (default: {DEFAULT_DELAY_SECONDS})",
    )
    parser.add_argument(
        "--retries",
        type=non_negative_int,
        default=DEFAULT_RETRIES,
        help=f"retry attempts for failed requests (default: {DEFAULT_RETRIES})",
    )
    parser.add_argument(
        "--backoff-factor",
        type=non_negative_float,
        default=DEFAULT_BACKOFF_FACTOR,
        help=f"retry backoff factor in seconds (default: {DEFAULT_BACKOFF_FACTOR})",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="logging level (default: INFO)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    setup_logging(args.log_level)
    started_at = time.perf_counter()

    session = create_session(retries=args.retries, backoff_factor=args.backoff_factor)
    request_settings = RequestSettings(delay_seconds=args.delay)
    books, metrics = scrape_catalogue(
        max_pages=args.max_pages,
        max_books=args.max_books,
        session=session,
        request_settings=request_settings,
    )
    books, duplicate_count = deduplicate_books(books)
    metrics["duplicate_records"] += duplicate_count
    metrics["records_extracted"] = len(books)

    rows = [book_to_row(book) for book in books]
    write_rows_to_csv(rows, args.output)

    excel_file = None
    if not args.no_excel:
        excel_file = args.excel_output
        write_rows_to_excel(rows, excel_file)

    metrics["missing_values"] = count_missing_values(rows)
    metrics["execution_time_seconds"] = round(time.perf_counter() - started_at, 2)
    print_summary(metrics, args.output, excel_file)


if __name__ == "__main__":
    main()
