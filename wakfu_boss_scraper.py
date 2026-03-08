#!/usr/bin/env python3
"""
Scraper for boss and dungeon strategies from wakfu.guide.

Scrapes all dungeon guide pages (level brackets 1-230) plus special boss pages
(Ogrest, Tal Kasha) and outputs structured JSON data.
"""

import json
import re
import time
import sys
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag

BASE_URL = "https://wakfu.guide"

DUNGEON_LEVEL_RANGES = [
    "1-20", "21-35", "36-50", "51-65", "66-80", "81-95",
    "96-110", "111-125", "126-140", "141-155", "156-170",
    "171-185", "186-200", "201-215", "216-230",
]

SPECIAL_BOSS_PAGES = [
    "/ogrest/",
    "/talkasha/",
]

OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_FILE = OUTPUT_DIR / "wakfu_boss_strategies.json"

REQUEST_DELAY = 1.5  # seconds between requests


def fetch_page(url: str) -> BeautifulSoup | None:
    """Fetch a page and return a BeautifulSoup object."""
    try:
        response = requests.get(url, timeout=30, headers={
            "User-Agent": "WakfuGuideScraper/1.0 (educational project)",
        })
        response.raise_for_status()
        return BeautifulSoup(response.text, "html.parser")
    except requests.RequestException as e:
        print(f"  [ERROR] Failed to fetch {url}: {e}")
        return None


def extract_text_block(elements: list[Tag]) -> list[str]:
    """Extract text from a list of sibling elements until next h2."""
    texts = []
    for el in elements:
        if isinstance(el, Tag) and el.name == "ul":
            for li in el.find_all("li", recursive=False):
                texts.append(li.get_text(strip=True))
    return texts


def extract_images(elements: list[Tag], page_url: str) -> list[str]:
    """Extract image URLs from a list of elements."""
    images = []
    for el in elements:
        if isinstance(el, Tag):
            # Direct img tag
            if el.name == "img":
                src = el.get("src", "")
                if src:
                    images.append(urljoin(page_url, src))
            # figure containing img
            if el.name == "figure":
                img = el.find("img")
                if img and img.get("src"):
                    images.append(urljoin(page_url, img["src"]))
            # img nested in other elements
            for img in el.find_all("img"):
                src = img.get("src", "")
                if src:
                    full = urljoin(page_url, src)
                    if full not in images:
                        images.append(full)
    return images


def get_elements_between_h2s(h2_tag: Tag) -> list[Tag]:
    """Get all sibling elements between this h2 and the next h2."""
    elements = []
    for sibling in h2_tag.next_siblings:
        if isinstance(sibling, Tag):
            if sibling.name == "h2":
                break
            elements.append(sibling)
    return elements


def extract_location(elements: list[Tag]) -> str | None:
    """Find the location info from elements (pattern: <p><strong>Localisation</strong></p> followed by <p>location</p>)."""
    for i, el in enumerate(elements):
        if isinstance(el, Tag) and el.name == "p":
            strong = el.find("strong")
            if strong and "localisation" in strong.get_text(strip=True).lower():
                # Next <p> sibling should be the location
                for next_el in elements[i + 1:]:
                    if isinstance(next_el, Tag) and next_el.name == "p":
                        text = next_el.get_text(strip=True)
                        if text and "localisation" not in text.lower():
                            return text
                        break
    return None


def extract_metadata(elements: list[Tag]) -> dict:
    """Extract extra metadata like player count, key requirements, etc."""
    metadata = {}
    for i, el in enumerate(elements):
        if isinstance(el, Tag) and el.name == "p":
            strong = el.find("strong")
            if strong:
                label = strong.get_text(strip=True).lower()
                if label in ("donjon", "clé", "cle"):
                    for next_el in elements[i + 1:]:
                        if isinstance(next_el, Tag) and next_el.name == "p":
                            metadata[label] = next_el.get_text(strip=True)
                            break
    return metadata


def parse_dungeon_page(url: str, level_range: str) -> list[dict]:
    """Parse a dungeon level range page and extract all dungeon entries."""
    print(f"  Fetching {url}")
    soup = fetch_page(url)
    if not soup:
        return []

    dungeons = []
    main = soup.find("main") or soup

    h2_tags = main.find_all("h2")
    for h2 in h2_tags:
        name = h2.get_text(strip=True)
        if not name:
            continue

        dungeon_id = h2.get("id", "")
        elements = get_elements_between_h2s(h2)
        location = extract_location(elements)
        strategies = extract_text_block(elements)
        images = extract_images(elements, url)
        metadata = extract_metadata(elements)

        # Collect all paragraph text that isn't location/metadata labels
        description_parts = []
        skip_next = False
        for el in elements:
            if skip_next:
                skip_next = False
                continue
            if isinstance(el, Tag) and el.name == "p":
                strong = el.find("strong")
                if strong:
                    label = strong.get_text(strip=True).lower()
                    if label in ("localisation", "donjon", "clé", "cle"):
                        skip_next = True
                        continue
                text = el.get_text(strip=True)
                if text:
                    description_parts.append(text)

        dungeon = {
            "name": name,
            "slug": dungeon_id,
            "level_range": level_range,
            "location": location,
            "strategies": strategies,
            "description": " ".join(description_parts) if description_parts else None,
            "images": images,
            "source_url": url,
            "type": "dungeon",
        }
        if metadata:
            dungeon["metadata"] = metadata

        dungeons.append(dungeon)
        print(f"    Found dungeon: {name}")

    return dungeons


def parse_special_boss_page(url: str) -> dict | None:
    """Parse a special boss page (Ogrest, Tal Kasha)."""
    print(f"  Fetching {url}")
    soup = fetch_page(url)
    if not soup:
        return None

    main = soup.find("main") or soup

    # Get the boss name from h1
    h1 = main.find("h1")
    boss_name = h1.get_text(strip=True) if h1 else url.split("/")[-2].title()

    # Extract phases/sections from h2 headings
    phases = []
    h2_tags = main.find_all("h2")
    for h2 in h2_tags:
        section_name = h2.get_text(strip=True)
        if not section_name:
            continue

        elements = get_elements_between_h2s(h2)
        strategies = extract_text_block(elements)
        images = extract_images(elements, url)

        # Gather paragraph descriptions
        desc_parts = []
        for el in elements:
            if isinstance(el, Tag) and el.name == "p":
                text = el.get_text(strip=True)
                if text:
                    desc_parts.append(text)

        phases.append({
            "section": section_name,
            "slug": h2.get("id", ""),
            "strategies": strategies,
            "description": " ".join(desc_parts) if desc_parts else None,
            "images": images,
        })

    # Also gather h3 subsections
    h3_tags = main.find_all("h3")
    for h3 in h3_tags:
        section_name = h3.get_text(strip=True)
        if not section_name:
            continue

        elements = []
        for sibling in h3.next_siblings:
            if isinstance(sibling, Tag):
                if sibling.name in ("h2", "h3"):
                    break
                elements.append(sibling)

        strategies = extract_text_block(elements)
        if strategies:
            phases.append({
                "section": section_name,
                "slug": h3.get("id", ""),
                "strategies": strategies,
                "description": None,
                "images": extract_images(elements, url),
            })

    # Collect all images on the page
    all_images = []
    for img in main.find_all("img"):
        src = img.get("src", "")
        if src:
            full = urljoin(url, src)
            if full not in all_images:
                all_images.append(full)

    boss = {
        "name": boss_name,
        "slug": url.rstrip("/").split("/")[-1],
        "source_url": url,
        "type": "special_boss",
        "phases": phases,
        "all_images": all_images,
    }

    print(f"    Found boss: {boss_name} ({len(phases)} sections)")
    return boss


def scrape_all() -> dict:
    """Run the full scrape and return all data."""
    data = {
        "source": "wakfu.guide",
        "scraped_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "dungeons": [],
        "special_bosses": [],
    }

    # Scrape dungeon pages
    print("Scraping dungeon pages...")
    for level_range in DUNGEON_LEVEL_RANGES:
        url = f"{BASE_URL}/donjons/{level_range}/"
        dungeons = parse_dungeon_page(url, level_range)
        data["dungeons"].extend(dungeons)
        time.sleep(REQUEST_DELAY)

    # Scrape special boss pages
    print("\nScraping special boss pages...")
    for path in SPECIAL_BOSS_PAGES:
        url = f"{BASE_URL}{path}"
        boss = parse_special_boss_page(url)
        if boss:
            data["special_bosses"].append(boss)
        time.sleep(REQUEST_DELAY)

    return data


def main():
    print("=" * 60)
    print("Wakfu Boss Strategy Scraper - wakfu.guide")
    print("=" * 60)

    data = scrape_all()

    dungeon_count = len(data["dungeons"])
    boss_count = len(data["special_bosses"])
    print(f"\nResults: {dungeon_count} dungeons, {boss_count} special bosses")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"Data saved to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
