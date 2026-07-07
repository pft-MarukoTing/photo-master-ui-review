"""
One-off diagnostic: load a Google Images search, click the first thumbnail,
and dump page HTML before/after the click so we can find the current
selectors for the thumbnail grid and the source-page link in the side panel.

Usage:
    python debug_dump.py --query "flocked nails"
"""
import argparse
import time
import urllib.parse
from pathlib import Path

from crawler import build_driver, accept_cookies
from selenium.webdriver.common.by import By

parser = argparse.ArgumentParser()
parser.add_argument("--query", required=True)
args = parser.parse_args()

driver = build_driver(headless=False)
try:
    params = urllib.parse.urlencode({"q": args.query, "tbm": "isch"})
    driver.get(f"https://www.google.com/search?{params}")
    accept_cookies(driver)
    time.sleep(2)

    Path("debug_before_click.html").write_text(driver.page_source, encoding="utf-8")
    print("Saved debug_before_click.html")

    imgs = driver.find_elements(By.CSS_SELECTOR, "#islrg img, div[data-id] img, g-img img")
    print(f"Found {len(imgs)} candidate thumbnail <img> elements")
    if imgs:
        for i, im in enumerate(imgs[:3]):
            print(f"  [{i}] outerHTML: {im.get_attribute('outerHTML')[:300]}")
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", imgs[0])
        time.sleep(0.5)
        try:
            imgs[0].click()
        except Exception as e:
            print(f"Click failed: {e} — trying JS click")
            driver.execute_script("arguments[0].click();", imgs[0])
        time.sleep(2)

    Path("debug_after_click.html").write_text(driver.page_source, encoding="utf-8")
    print("Saved debug_after_click.html")
    print("\nCurrent URL:", driver.current_url)
    input("Press Enter to close the browser...")
finally:
    driver.quit()
