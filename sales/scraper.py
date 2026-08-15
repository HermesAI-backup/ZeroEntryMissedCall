"""Lead generation — scrape business phone numbers from public directories and Outscraper."""

from __future__ import annotations

import csv
import re
import json
from pathlib import Path
from typing import Iterator

import httpx

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "leads"
DATA_DIR.mkdir(parents=True, exist_ok=True)


def extract_phone_numbers(text: str) -> list[str]:
    """Extract US phone numbers from text and return them in E.164 format."""
    # Match various formats: (555) 123-4567, 555-123-4567, +15551234567, etc.
    pattern = r"(\+?1?[-.\s]?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4})"
    matches = re.findall(pattern, text)
    cleaned = []
    for m in matches:
        digits = re.sub(r"\D", "", m)
        if len(digits) == 10:
            digits = "+1" + digits
        elif len(digits) == 11 and digits.startswith("1"):
            digits = "+" + digits
        if len(digits) == 12 and digits.startswith("+1"):
            cleaned.append(digits)
    return cleaned


def scrape_outscraper_csv(csv_path: str | Path) -> list[dict]:
    """Parse an Outscraper-exported CSV of business listings.

    Outscraper typically exports: name, phone, address, website, rating, etc.
    """
    results = []
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            phone = row.get("phone", "") or row.get("Phone", "") or ""
            if phone:
                numbers = extract_phone_numbers(phone)
                if numbers:
                    results.append(
                        {
                            "business_name": row.get("name", "")
                            or row.get("Name", "")
                            or "Unknown",
                            "phone": numbers[0],
                            "address": row.get("full_address", "")
                            or row.get("address", "")
                            or "",
                            "website": row.get("site", "")
                            or row.get("website", "")
                            or "",
                        }
                    )
    return results


def save_phone_list(phone_numbers: list[str], filename: str = "phone_list.csv"):
    """Save phone numbers to a CSV file ready for voicemail drop upload."""
    filepath = DATA_DIR / filename
    with open(filepath, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["phone_number"])
        for num in phone_numbers:
            writer.writerow([num])
    logger.info("Saved %d phone numbers to %s", len(phone_numbers), filepath)
    return filepath


def normalize_phone_csv(input_csv: str | Path, output_csv: str | Path | None = None):
    """Normalize phone numbers from a raw export into clean E.164 format."""
    import logging

    global logger
    logger = logging.getLogger("sales.scraper")

    if output_csv is None:
        p = Path(input_csv)
        output_csv = p.parent / f"{p.stem}_cleaned{p.suffix}"

    numbers = []
    with open(input_csv, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line == "phone_number":
                continue
            nums = extract_phone_numbers(line)
            numbers.extend(nums)

    with open(output_csv, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["phone_number"])
        for num in numbers:
            writer.writerow([num])

    logger.info(
        "Normalized %d numbers from %s -> %s",
        len(numbers),
        input_csv,
        output_csv,
    )
    return Path(output_csv)


import logging

logger = logging.getLogger("sales.scraper")
