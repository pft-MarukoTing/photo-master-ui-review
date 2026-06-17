"""
Magnific image crawler using Selenium + undetected-chromedriver.

Strategy:
1. Launch real Chrome via undetected-chromedriver (bypasses Akamai bot detection)
2. Load the search page and wait for images to render
3. Scroll to bottom; click "Load More" button when it appears; repeat
4. Extract img.magnific.com URLs from rendered DOM
5. Download images (base URLs have no signed token required)

Usage:
    python crawler.py --query "hand near face" --max 50
    python crawler.py --query "nail art" --max 100 --output my_folder
    python crawler.py --image /path/to/photo.jpg --max 60
    python crawler.py --query "reflective nails" --image /path/to/photo.jpg --max 60
"""
import argparse
import re
import time
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

# Selector for the "Load More" button
LOAD_MORE_SELECTOR = "button[data-testid='load-more-button']"
# The search results live inside a scrollable div, not window/body
SCROLL_CONTAINER_SELECTOR = "div.sm\\:overflow-y-auto"


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
                    best_w, best_url = w, url.split("?")[0] + f"?w={w}"
            except ValueError:
                pass
    return best_url


def collect_img_urls(driver: uc.Chrome) -> set[str]:
    elements = driver.find_elements(By.TAG_NAME, "img")
    urls: set[str] = set()
    for el in elements:
        src = el.get_attribute("src") or ""
        if IMG_DOMAIN not in src:
            continue
        srcset = el.get_attribute("srcset") or ""
        best = _pick_best_srcset_url(srcset) if srcset else None
        urls.add(best or f"{src.split('?')[0]}?w=2000")
    return urls


def find_load_more_button(driver: uc.Chrome):
    """Return the Load More button element, or None if not present."""
    try:
        btn = driver.find_element(By.CSS_SELECTOR, LOAD_MORE_SELECTOR)
        if btn.is_displayed() and btn.is_enabled():
            return btn
    except NoSuchElementException:
        pass
    return None


def scroll_and_collect(driver: uc.Chrome, max_images: int, pause: float = 1.5) -> set[str]:
    """
    Scroll gradually through the page to trigger lazy loading, click 'Load More'
    when it appears, repeat. Stops when >= max_images collected or no more results.
    """
    all_urls: set[str] = set()
    stall_count = 0
    round_num = 0

    while len(all_urls) < max_images:
        round_num += 1

        try:
            # Scroll gradually through the inner results container (not window/body —
            # this Next.js app uses a fixed-height div with overflow-y:auto).
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

            # Collect images visible now
            batch = collect_img_urls(driver)
            new = batch - all_urls
            all_urls |= batch

            # Try to click "Load More"
            btn = find_load_more_button(driver)
            if btn:
                stall_count = 0
                print(f"  round {round_num}: +{len(new)} images ({len(all_urls)} total) — clicking Load More")
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
                    print(f"  round {round_num}: +{len(new)} images ({len(all_urls)} total)")
                    stall_count = 0
                else:
                    stall_count += 1
                    print(f"  round {round_num}: no new images, no Load More button ({stall_count}/5)")
                    if stall_count >= 5:
                        print("  → End of results.")
                        break
        except (NoSuchWindowException, WebDriverException) as e:
            print(f"  Browser window closed unexpectedly: {e}")
            break

    return all_urls


def url_to_filename(url: str) -> str:
    name = url.split("?")[0].rstrip("/").split("/")[-1]
    name = re.sub(r"[^a-zA-Z0-9_\-\.]", "_", name)
    return name or "image.jpg"


def download_images(urls: list[str], output_dir: Path, max_images: int):
    output_dir.mkdir(parents=True, exist_ok=True)
    targets = urls[:max_images]
    print(f"\nDownloading {len(targets)} images → {output_dir}/")
    for i, url in enumerate(targets, 1):
        filename = url_to_filename(url)
        dest = output_dir / filename
        if dest.exists():
            print(f"  [{i}/{len(targets)}] skip (exists): {filename}")
            continue
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                dest.write_bytes(resp.read())
            print(f"  [{i}/{len(targets)}] OK  {filename}")
        except Exception as e:
            print(f"  [{i}/{len(targets)}] ERR {filename}: {e}")


def upload_image(driver: uc.Chrome, image_path: Path):
    """Click the image-search button, upload the file, click 搜索相似圖像."""
    wait = WebDriverWait(driver, 15)

    # Open the image-upload panel
    btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button[data-cy='search-by-image']")))
    btn.click()

    # Send file path to the hidden file input (no need to make it visible)
    file_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='file']")))
    file_input.send_keys(str(image_path.resolve()))
    print(f"  Image uploaded: {image_path.name}")

    # Click the modal submit button (distinct from the search-bar magnifying glass)
    submit = wait.until(EC.element_to_be_clickable(
        (By.XPATH, "//button[@type='submit'][.//span[contains(text(),'搜索相似圖像')]]")
    ))
    submit.click()
    # Give Akamai time to process the navigation triggered by submit
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

    # Build subfolder name from query and/or image stem
    parts = []
    if query:
        parts.append(re.sub(r"[^a-zA-Z0-9]+", "_", query).strip("_"))
    if image:
        parts.append(re.sub(r"[^a-zA-Z0-9]+", "_", image.stem).strip("_"))
    output_dir = output_dir / "_".join(parts)

    # Start URL: use text query if provided, otherwise just the search base
    if query and not image:
        start_url = f"{SEARCH_BASE}?format=search&last_filter=query&last_value={query.replace(' ', '+')}&query={query.replace(' ', '+')}"
    else:
        start_url = SEARCH_BASE

    print(f"Query : {query!r}")
    print(f"Image : {image}")
    print(f"Output: {output_dir}/\n")

    driver = build_driver(headless=headless)
    try:
        driver.get(start_url)

        if image:
            # Wait for the page chrome to load before interacting with the search bar
            WebDriverWait(driver, 40).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "button[data-cy='search-by-image']"))
            )
            upload_image(driver, image)
            # If a text query is also provided, update the search bar and re-submit
            if query:
                submit_search(driver, query)

        # Wait for at least one search result image to appear
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

        img_urls = scroll_and_collect(driver, max_images=max_images, pause=1.5)
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    print(f"\nTotal unique image URLs found: {len(img_urls)}")
    if not img_urls:
        print("No images found. The page may have blocked access or changed structure.")
        return []

    sorted_urls = sorted(img_urls)
    download_images(sorted_urls, output_dir, max_images)
    return sorted_urls[:max_images]


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
