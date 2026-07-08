"""
Google Images crawler using Selenium + undetected-chromedriver.

Strategy:
1. Launch real Chrome via undetected-chromedriver
2. Load the Google Images search results page for the query
3. Scroll and click "Show more results" to load thumbnails, up to --max results
4. Extract each result's original source page URL from the "data-lpage"
   attribute Google embeds on each result card (present without needing to
   click into the thumbnail)
5. Visit each source page directly (not Google's cache) and locate the main
   image to download: og:image meta tag, else the largest declared <img> on
   the page
6. Download the image from the source site with a Referer header, converting
   it to .jpg on save

Usage:
    python crawler.py --query "flocked nails" --max 50
    python crawler.py --query "flocked nails" --max 50 --output my_folder
"""
import argparse
import io
import random
import re
import time
import urllib.parse
from pathlib import Path

import requests
import undetected_chromedriver as uc
from bs4 import BeautifulSoup
from PIL import Image
from selenium.common.exceptions import ElementClickInterceptedException, NoSuchElementException, NoSuchWindowException, WebDriverException
from selenium.webdriver.common.by import By

SEARCH_BASE = "https://www.google.com/search"
DEFAULT_OUTPUT = Path("output")
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def build_driver(headless: bool = False) -> uc.Chrome:
    options = uc.ChromeOptions()
    options.add_argument("--lang=en-US")
    options.add_argument("--window-size=1440,900")
    if headless:
        options.add_argument("--headless=new")
    return uc.Chrome(options=options, version_main=149)


def accept_cookies(driver: uc.Chrome):
    """Best-effort dismissal of Google's cookie-consent dialog."""
    try:
        btn = driver.find_element(
            By.XPATH,
            "//button[.//div[contains(text(),'Accept all')] or .//div[contains(text(),'接受全部')] "
            "or contains(text(),'Accept all') or contains(text(),'接受全部')]",
        )
        if btn.is_displayed():
            btn.click()
            time.sleep(1)
    except NoSuchElementException:
        pass


def collect_source_pages(driver: uc.Chrome) -> set[str]:
    """Return the set of source-page URLs from every result card's data-lpage attribute."""
    pages: set[str] = set()
    for el in driver.find_elements(By.CSS_SELECTOR, "[data-lpage]"):
        href = (el.get_attribute("data-lpage") or "").strip()
        if not href:
            continue
        parsed = urllib.parse.urlparse(href)
        if parsed.scheme in ("http", "https") and parsed.netloc:
            pages.add(href)
    return pages


def find_show_more_button(driver: uc.Chrome):
    """Return the 'Show more results' element, or None if not present."""
    try:
        btn = driver.find_element(
            By.XPATH,
            "//*[contains(text(),'Show more results') or contains(text(),'顯示更多結果') "
            "or contains(@value,'Show more results')]",
        )
        if btn.is_displayed():
            return btn
    except NoSuchElementException:
        pass
    return None


def scroll_and_collect_results(driver: uc.Chrome, max_results: int, pause: float = 1.5) -> set[str]:
    """
    Scroll through Google Images results, clicking 'Show more results' when it
    appears, and collect source-page URLs. Stops when >= max_results collected
    or no more results.
    """
    all_results: set[str] = set()
    stall_count = 0
    round_num = 0

    while len(all_results) < max_results:
        round_num += 1
        try:
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(pause)

            batch = collect_source_pages(driver)
            new = batch - all_results
            all_results |= batch

            btn = find_show_more_button(driver)
            if btn:
                stall_count = 0
                print(f"  round {round_num}: +{len(new)} results ({len(all_results)} total) — clicking Show more results")
                try:
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'})", btn)
                    time.sleep(0.5)
                    btn.click()
                    time.sleep(pause)
                except ElementClickInterceptedException:
                    driver.execute_script("arguments[0].click()", btn)
                    time.sleep(pause)
            else:
                if new:
                    print(f"  round {round_num}: +{len(new)} results ({len(all_results)} total)")
                    stall_count = 0
                else:
                    stall_count += 1
                    print(f"  round {round_num}: no new results, no Show more button ({stall_count}/5)")
                    if stall_count >= 5:
                        print("  → End of results.")
                        break
        except (NoSuchWindowException, WebDriverException) as e:
            print(f"  Browser window closed unexpectedly: {e}")
            break

    return all_results


def pick_image_from_page(html: str, base_url: str) -> str | None:
    """
    Return the best absolute image URL found on a source page: the og:image
    meta tag, else the largest declared <img> on the page.
    """
    soup = BeautifulSoup(html, "html.parser")

    og = soup.find("meta", property="og:image")
    if og and og.get("content"):
        return urllib.parse.urljoin(base_url, og["content"])

    candidates = []
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or img.get("data-lazy-src")
        if not src:
            continue
        candidates.append((urllib.parse.urljoin(base_url, src), img))

    def area(img_el):
        try:
            return int(img_el.get("width", 0)) * int(img_el.get("height", 0))
        except (TypeError, ValueError):
            return 0

    if candidates:
        candidates.sort(key=lambda c: area(c[1]), reverse=True)
        return candidates[0][0]

    return None


def url_to_filename(url: str) -> str:
    """Return a sanitized filename for url, always with a .jpg extension."""
    parsed = urllib.parse.urlparse(url)
    stem = Path(parsed.path.rstrip("/").split("/")[-1]).stem
    stem = re.sub(r"[^a-zA-Z0-9_\-]", "_", stem) or "image"
    host = re.sub(r"[^a-zA-Z0-9_\-]", "_", parsed.netloc)
    q = re.sub(r"[^a-zA-Z0-9_\-]", "_", parsed.query)[:12]
    name = "_".join(p for p in (host, stem, q) if p)[:180]
    return f"{name}.jpg"


def save_as_jpg(content: bytes, dest: Path, min_pixels: int = 0) -> bool:
    """
    Decode arbitrary image bytes and save them as a JPEG at dest.
    Returns False without saving if the image's total pixel count is below
    min_pixels.
    """
    with Image.open(io.BytesIO(content)) as im:
        if im.width * im.height < min_pixels:
            return False
        im = im.convert("RGB")
        im.save(dest, "JPEG", quality=90)
        return True


def crawl(
    query: str,
    max_images: int = 50,
    headless: bool = False,
    output_dir: Path = DEFAULT_OUTPUT,
    min_pixels: int = 0,
):
    output_dir = output_dir / re.sub(r"[^a-zA-Z0-9]+", "_", query).strip("_")

    params = urllib.parse.urlencode({"q": query, "tbm": "isch"})
    start_url = f"{SEARCH_BASE}?{params}"

    print(f"Query : {query!r}")
    print(f"Output: {output_dir}/\n")

    driver = build_driver(headless=headless)
    try:
        driver.get(start_url)
        accept_cookies(driver)

        print(f"Phase 1: collecting up to {max_images} results...")
        results = scroll_and_collect_results(driver, max_results=max_images)
        print(f"Total results found: {len(results)}")
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    if not results:
        print("No results found.")
        return []

    items = sorted(results)[:max_images]

    print("\nPhase 2: visiting source pages and downloading...")
    output_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    downloaded_urls: list[str] = []

    for i, imgrefurl in enumerate(items, 1):
        try:
            page = session.get(imgrefurl, headers={"User-Agent": USER_AGENT}, timeout=15)
            page.raise_for_status()
        except Exception as e:
            print(f"  [{i}/{len(items)}] SKIP (source page unreachable): {imgrefurl} ({e})")
            continue

        chosen = pick_image_from_page(page.text, imgrefurl)
        if not chosen:
            print(f"  [{i}/{len(items)}] SKIP (no image found on page): {imgrefurl}")
            continue

        parsed_img = urllib.parse.urlparse(chosen)
        if parsed_img.scheme not in ("http", "https") or not parsed_img.netloc:
            print(f"  [{i}/{len(items)}] SKIP (non-http image URL): {chosen}")
            continue

        filename = url_to_filename(chosen)
        dest = output_dir / filename
        if dest.exists():
            print(f"  [{i}/{len(items)}] skip (exists): {filename}")
        else:
            try:
                resp = session.get(chosen, headers={"User-Agent": USER_AGENT, "Referer": imgrefurl}, timeout=20)
                resp.raise_for_status()
                if not save_as_jpg(resp.content, dest, min_pixels=min_pixels):
                    print(f"  [{i}/{len(items)}] SKIP (below {min_pixels} px): {filename}")
                    continue
                print(f"  [{i}/{len(items)}] OK  {filename}")
            except Exception as e:
                print(f"  [{i}/{len(items)}] ERR {filename}: {e}")
                continue

        downloaded_urls.append(chosen)
        if i < len(items):
            time.sleep(random.uniform(0.5, 1.5))

    if downloaded_urls:
        urls_file = output_dir / "urls.txt"
        urls_file.write_text("\n".join(downloaded_urls) + "\n", encoding="utf-8")
        print(f"\nURLs saved → {urls_file}")

    print(f"Done. {len(downloaded_urls)} image(s) processed → {output_dir}/")
    return downloaded_urls


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Crawl Google Images search results and download from source pages")
    parser.add_argument("--query", required=True, help="Text search query")
    parser.add_argument("--max", type=int, default=50, help="Max images to download (default: 50)")
    parser.add_argument("--headless", action="store_true", help="Run Chrome in headless mode")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Output directory")
    parser.add_argument("--min-pixels", type=int, default=0, help="Skip images with width*height below this value")
    args = parser.parse_args()

    crawl(args.query, max_images=args.max, headless=args.headless, output_dir=args.output, min_pixels=args.min_pixels)
