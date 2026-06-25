"""
Magnific image crawler using Selenium + undetected-chromedriver.

Strategy:
1. Launch real Chrome via undetected-chromedriver (bypasses Akamai bot detection)
2. Load the search page and wait for images to render
3. Scroll to bottom; click "Load More" button when it appears; repeat
4. Collect detail-page links from the search result cards
5. Visit each detail page; extract the signed srcset URL from
   div[data-cy="resource-detail-preview"] img (token avoids watermark)
6. Download each image immediately after extracting its signed URL
   (tokens expire ~1 hour, so download while fresh)

Usage:
    python crawler.py --query "hand near face" --max 50
    python crawler.py --query "nail art" --max 100 --output my_folder
    python crawler.py --image /path/to/photo.jpg --max 60
    python crawler.py --query "reflective nails" --image /path/to/photo.jpg --max 60
"""
import argparse
import random
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

import undetected_chromedriver as uc
from selenium.common.exceptions import ElementClickInterceptedException, NoSuchElementException, NoSuchWindowException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

SEARCH_BASE = "https://www.magnific.com/zh/search"
DEFAULT_OUTPUT = Path("output")
IMG_DOMAIN = "img.magnific.com"

LOAD_MORE_SELECTOR = "button[data-testid='load-more-button']"
SCROLL_CONTAINER_SELECTOR = "div.sm\\:overflow-y-auto"
DETAIL_PREVIEW_SELECTOR = "div[data-cy='resource-detail-preview'] img"


def build_driver(headless: bool = False) -> uc.Chrome:
    options = uc.ChromeOptions()
    options.add_argument("--lang=zh-TW")
    options.add_argument("--window-size=1440,900")
    if headless:
        options.add_argument("--headless=new")
    return uc.Chrome(options=options)


def _pick_best_srcset_url(srcset: str) -> str | None:
    """Return the URL with the largest w descriptor from a srcset string."""
    best_url, best_w = None, 0
    for part in srcset.split(","):
        part = part.strip()
        if not part:
            continue
        tokens = part.split()
        if len(tokens) < 2:
            continue
        url, descriptor = tokens[0], tokens[1]
        if descriptor.endswith("w"):
            try:
                w = int(descriptor[:-1])
                if w > best_w:
                    best_w, best_url = w, url
            except ValueError:
                pass
    return best_url


def collect_detail_links(driver: uc.Chrome) -> set[str]:
    """Return all unique detail-page hrefs visible in the current search results."""
    elements = driver.find_elements(By.CSS_SELECTOR, f"img[src*='{IMG_DOMAIN}']")
    links: set[str] = set()
    for el in elements:
        try:
            anchor = el.find_element(By.XPATH, "./ancestor::a[1]")
            href = anchor.get_attribute("href") or ""
            if href:
                links.add(href)
        except NoSuchElementException:
            pass
    return links


def get_signed_img_url(driver: uc.Chrome, detail_url: str) -> str | None:
    """
    Navigate to a detail page and return the signed image URL with the largest
    w descriptor from div[data-cy="resource-detail-preview"] img srcset.
    Returns None if a signed URL cannot be found.
    """
    driver.get(detail_url)
    try:
        img_el = WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, DETAIL_PREVIEW_SELECTOR))
        )
        srcset = img_el.get_attribute("srcset") or ""
        if srcset:
            url = _pick_best_srcset_url(srcset)
            if url:
                return url
        # Fallback: use src if it already carries a signed query string
        src = img_el.get_attribute("src") or ""
        parsed = urllib.parse.urlparse(src)
        if parsed.netloc == IMG_DOMAIN and parsed.query:
            return src
    except Exception:
        pass
    return None


def find_load_more_button(driver: uc.Chrome):
    """Return the Load More button element, or None if not present."""
    try:
        btn = driver.find_element(By.CSS_SELECTOR, LOAD_MORE_SELECTOR)
        if btn.is_displayed() and btn.is_enabled():
            return btn
    except NoSuchElementException:
        pass
    return None


def scroll_and_collect_links(driver: uc.Chrome, max_links: int, pause: float = 1.5) -> set[str]:
    """
    Scroll gradually through the search results to trigger lazy loading,
    click 'Load More' when it appears, and collect detail-page links.
    Stops when >= max_links collected or no more results.
    """
    all_links: set[str] = set()
    stall_count = 0
    round_num = 0

    while len(all_links) < max_links:
        round_num += 1

        try:
            container = driver.find_element(By.CSS_SELECTOR, SCROLL_CONTAINER_SELECTOR)
            step = driver.execute_script("return arguments[0].clientHeight", container)
            scroll_h = driver.execute_script("return arguments[0].scrollHeight", container)
            current_y = driver.execute_script("return arguments[0].scrollTop", container)
            while current_y < scroll_h:
                current_y = min(current_y + step, scroll_h)
                driver.execute_script("arguments[0].scrollTop = arguments[1]", container, current_y)
                time.sleep(0.4)
                scroll_h = driver.execute_script("return arguments[0].scrollHeight", container)

            time.sleep(pause)

            batch = collect_detail_links(driver)
            new = batch - all_links
            all_links |= batch

            btn = find_load_more_button(driver)
            if btn:
                stall_count = 0
                print(f"  round {round_num}: +{len(new)} links ({len(all_links)} total) — clicking Load More")
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
                    print(f"  round {round_num}: +{len(new)} links ({len(all_links)} total)")
                    stall_count = 0
                else:
                    stall_count += 1
                    print(f"  round {round_num}: no new links, no Load More button ({stall_count}/5)")
                    if stall_count >= 5:
                        print("  → End of results.")
                        break
        except (NoSuchWindowException, WebDriverException) as e:
            print(f"  Browser window closed unexpectedly: {e}")
            break

    return all_links


def url_to_filename(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    name = parsed.path.rstrip("/").split("/")[-1]
    name = re.sub(r"[^a-zA-Z0-9_\-\.]", "_", name)
    return name or "image.jpg"


def upload_image(driver: uc.Chrome, image_path: Path):
    """Click the image-search button, upload the file, click 搜索相似圖像."""
    wait = WebDriverWait(driver, 15)

    btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button[data-cy='search-by-image']")))
    btn.click()

    file_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='file']")))
    file_input.send_keys(str(image_path.resolve()))
    print(f"  Image uploaded: {image_path.name}")

    submit = wait.until(EC.element_to_be_clickable(
        (By.XPATH, "//button[@type='submit'][.//span[contains(text(),'搜索相似圖像')]]")
    ))
    submit.click()
    time.sleep(3)


def submit_search(driver: uc.Chrome, query: str | None):
    """Optionally update the text field, then click the submit button."""
    wait = WebDriverWait(driver, 10)
    if query is not None:
        text_input = driver.find_element(By.CSS_SELECTOR, "input[data-cy='search-pageSearch']")
        text_input.clear()
        text_input.send_keys(query)

    submit_btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button[type='submit']")))
    submit_btn.click()


def crawl(
    query: str | None = None,
    image: Path | None = None,
    max_images: int = 50,
    headless: bool = False,
    output_dir: Path = DEFAULT_OUTPUT,
):
    if not query and not image:
        raise ValueError("Provide at least one of: query, --image")

    parts = []
    if query:
        parts.append(re.sub(r"[^a-zA-Z0-9]+", "_", query).strip("_"))
    if image:
        parts.append(re.sub(r"[^a-zA-Z0-9]+", "_", image.stem).strip("_"))
    output_dir = output_dir / "_".join(parts)

    if query and not image:
        params = urllib.parse.urlencode({
            "format": "search",
            "last_filter": "query",
            "last_value": query,
            "query": query,
        })
        start_url = f"{SEARCH_BASE}?{params}"
    else:
        start_url = SEARCH_BASE

    print(f"Query : {query!r}")
    print(f"Image : {image}")
    print(f"Output: {output_dir}/\n")

    driver = build_driver(headless=headless)
    try:
        driver.get(start_url)

        if image:
            WebDriverWait(driver, 40).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "button[data-cy='search-by-image']"))
            )
            upload_image(driver, image)
            if query:
                submit_search(driver, query)

        print("Waiting for images to load...")
        try:
            WebDriverWait(driver, 40).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, f"img[src*='{IMG_DOMAIN}']"))
            )
        except Exception:
            try:
                print(f"  Warning: timed out — current URL: {driver.current_url}")
                print(f"  Page title: {driver.title}")
            except Exception:
                print("  Warning: timed out and window is no longer accessible")
                return []

        # Phase 1: collect detail-page links from search results
        print(f"\nPhase 1: collecting up to {max_images} detail-page links...")
        detail_links = scroll_and_collect_links(driver, max_links=max_images, pause=1.5)
        print(f"Total detail-page links found: {len(detail_links)}")

        if not detail_links:
            print("No detail links found.")
            return []

        # Phase 2: visit each detail page, extract signed URL, download immediately
        # (tokens expire ~1 hour; download while fresh)
        print(f"\nPhase 2: visiting detail pages and downloading...")
        output_dir.mkdir(parents=True, exist_ok=True)
        downloaded_urls: list[str] = []
        links_list = sorted(detail_links)[:max_images]

        for i, link in enumerate(links_list, 1):
            signed = get_signed_img_url(driver, link)
            if not signed:
                print(f"  [{i}/{len(links_list)}] SKIP (no signed URL): {link}")
                if i < len(links_list):
                    time.sleep(random.uniform(1.0, 2.5))
                continue

            filename = url_to_filename(signed)
            dest = output_dir / filename
            if dest.exists():
                print(f"  [{i}/{len(links_list)}] skip (exists): {filename}")
            else:
                try:
                    req = urllib.request.Request(signed, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req, timeout=20) as resp:
                        dest.write_bytes(resp.read())
                    print(f"  [{i}/{len(links_list)}] OK  {filename}")
                except Exception as e:
                    print(f"  [{i}/{len(links_list)}] ERR {filename}: {e}")

            downloaded_urls.append(signed)

            if i < len(links_list):
                time.sleep(random.uniform(1.0, 2.5))

    finally:
        try:
            driver.quit()
        except Exception:
            pass

    if downloaded_urls:
        urls_file = output_dir / "urls.txt"
        urls_file.write_text("\n".join(downloaded_urls) + "\n", encoding="utf-8")
        print(f"\nURLs saved → {urls_file}")

    print(f"Done. {len(downloaded_urls)} image(s) processed → {output_dir}/")
    return downloaded_urls


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Crawl Magnific search result images")
    parser.add_argument("--query", default="", help="Text search query")
    parser.add_argument("--image", type=Path, default=None, help="Path to reference image for visual search")
    parser.add_argument("--max", type=int, default=50, help="Max images to download (default: 50)")
    parser.add_argument("--headless", action="store_true", help="Run Chrome in headless mode")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Output directory")
    args = parser.parse_args()

    if not args.query and not args.image:
        parser.error("Provide at least one of: --query or --image")

    crawl(args.query or None, image=args.image, max_images=args.max, headless=args.headless, output_dir=args.output)
