import os
import re
import logging

logger = logging.getLogger(__name__)


def _sanitize(keyword: str, max_len: int = 50) -> str:
    slug = re.sub(r"[^\w\s-]", "", keyword).strip()
    slug = re.sub(r"[\s]+", "_", slug)
    return slug[:max_len]


def crawl_bing(keyword: str, max_num: int, staging_dir: str) -> list:
    from icrawler.builtin import BingImageCrawler

    dest = os.path.join(staging_dir, "bing", _sanitize(keyword))
    os.makedirs(dest, exist_ok=True)

    crawler = BingImageCrawler(
        storage={"root_dir": dest},
        log_level=logging.WARNING,
    )
    crawler.crawl(
        keyword=keyword,
        max_num=max_num,
        filters={"size": "large"},
    )

    paths = []
    for fname in os.listdir(dest):
        full = os.path.join(dest, fname)
        if os.path.isfile(full):
            paths.append(full)

    logger.info("Bing '%s': downloaded %d files to %s", keyword, len(paths), dest)
    return paths


def crawl_all(config: dict, staging_dir: str) -> list:
    sources = config.get("sources", {})
    keywords = config.get("keywords", [])
    all_paths = []

    for keyword in keywords:
        if sources.get("bing", {}).get("enabled", False):
            max_num = sources["bing"].get("max_num", 200)
            try:
                paths = crawl_bing(keyword, max_num, staging_dir)
                all_paths.extend(paths)
            except Exception as e:
                logger.warning("Bing crawl failed for '%s': %s", keyword, e)

    return all_paths
