import argparse
import logging
import os
import shutil
import tempfile
from datetime import datetime

import yaml

from crawl import crawl_all
from filter import DuplicateFilter, FilterStats, HandDetector, filter_image


def load_config(config_path: str) -> dict:
    with open(config_path) as f:
        config = yaml.safe_load(f)
    # allow env var overrides for API keys
    if "sources" in config:
        pexels = config["sources"].get("pexels", {})
        if not pexels.get("api_key") and os.environ.get("PEXELS_API_KEY"):
            pexels["api_key"] = os.environ["PEXELS_API_KEY"]
        unsplash = config["sources"].get("unsplash", {})
        if not unsplash.get("api_key") and os.environ.get("UNSPLASH_ACCESS_KEY"):
            unsplash["api_key"] = os.environ["UNSPLASH_ACCESS_KEY"]
    return config


def setup_logging(config: dict) -> logging.Logger:
    log_dir = config.get("logging", {}).get("log_dir", "logs")
    level_str = config.get("logging", {}).get("level", "INFO")
    os.makedirs(log_dir, exist_ok=True)

    level = getattr(logging, level_str.upper(), logging.INFO)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = os.path.join(log_dir, f"run_{timestamp}.log")

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(),
        ],
    )
    logging.getLogger("PIL").setLevel(logging.WARNING)

    logger = logging.getLogger("pipeline")
    logger.info("Log file: %s", log_file)
    return logger


def ensure_dirs(config: dict) -> tuple:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_dir = os.path.join(script_dir, config.get("output", {}).get("dir", "output"))
    os.makedirs(output_dir, exist_ok=True)
    staging_dir = os.path.join(script_dir, "staging")
    os.makedirs(staging_dir, exist_ok=True)
    return output_dir, staging_dir


def save_to_output(src_path: str, output_dir: str, index: int, quality: int = 92) -> str:
    from PIL import Image
    dest = os.path.join(output_dir, f"{index:06d}.jpg")
    with Image.open(src_path) as img:
        rgb = img.convert("RGB")
        rgb.save(dest, "JPEG", quality=quality)
    return dest


def cleanup_staging(staging_dir: str):
    if os.path.exists(staging_dir):
        shutil.rmtree(staging_dir)


def run_pipeline(config_path: str = "config.yaml",
                 skip_crawl: bool = False,
                 cleanup_staging_flag: bool = False,
                 dry_run: bool = False):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, config_path) if not os.path.isabs(config_path) else config_path

    config = load_config(config_path)
    logger = setup_logging(config)
    output_dir, staging_dir = ensure_dirs(config)
    jpg_quality = config.get("output", {}).get("jpg_quality", 92)

    # --- Crawl ---
    if skip_crawl:
        logger.info("--skip-crawl: scanning existing staging dir %s", staging_dir)
        staged = []
        for root, _, files in os.walk(staging_dir):
            for f in files:
                staged.append(os.path.join(root, f))
        staged.sort()
        logger.info("Found %d staged files", len(staged))
    else:
        logger.info("Starting crawl...")
        staged = crawl_all(config, staging_dir)
        staged.sort()
        logger.info("Crawl complete: %d total staged files", len(staged))

    # --- Filter ---
    dedup_cfg = config.get("dedup", {})
    state_file = os.path.join(script_dir, dedup_cfg.get("state_file", "seen_hashes.json"))
    threshold = dedup_cfg.get("phash_threshold", 8)
    dedup = DuplicateFilter(state_file=state_file, threshold=threshold)
    if dedup_cfg.get("enabled", True):
        dedup.load()

    detector = HandDetector()
    stats = FilterStats()

    # count existing output files to continue numbering
    existing = [f for f in os.listdir(output_dir) if f.endswith(".jpg")]
    index = len(existing) + 1

    logger.info("Filtering %d staged images...", len(staged))
    for path in staged:
        try:
            passes = filter_image(path, config, detector, dedup, stats)
        except Exception as e:
            logger.warning("Unexpected error filtering %s: %s", path, e)
            stats.errors += 1
            continue

        if passes:
            if dry_run:
                logger.info("[dry-run] Would save: %s", os.path.basename(path))
            else:
                dest = save_to_output(path, output_dir, index, jpg_quality)
                rel = os.path.relpath(path, staging_dir)
                logger.info("Saved: %s -> %s", rel, os.path.basename(dest))
                index += 1

    if dedup_cfg.get("enabled", True) and not dry_run:
        dedup.save()

    logger.info("=== Filter Summary === %s", stats.summary())

    if cleanup_staging_flag and not dry_run:
        cleanup_staging(staging_dir)
        logger.info("Staging dir cleaned up.")
    else:
        logger.info("Staged images kept at: %s", staging_dir)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AINail dataset preparation pipeline")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--skip-crawl", action="store_true",
                        help="Skip downloading; re-filter already-staged images")
    parser.add_argument("--cleanup-staging", action="store_true",
                        help="Delete staging dir after pipeline completes")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run filters but do not write output files")
    args = parser.parse_args()
    run_pipeline(args.config, skip_crawl=args.skip_crawl,
                 cleanup_staging_flag=args.cleanup_staging, dry_run=args.dry_run)
