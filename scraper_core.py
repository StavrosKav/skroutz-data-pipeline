"""
scraper_core.py
---------------
Shared scraping engine for all four skroutz.gr category scrapers.

The four skroutz_*WHILE.py files are thin entry points that call
scrape(CONFIGS["<category>"]) — the pagination loop, card parsing, retry
logic, markup-drift guard, and CSV writing all live here.

Output: date-stamped raw CSV in the category folder (see CONFIGS).

Dependencies: undetected-chromedriver, selenium, pandas
"""

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException
from dataclasses import dataclass
import pandas as pd
import time
import undetected_chromedriver as uc   # bypasses bot-detection on skroutz
import re
import datetime
import os
import sys
import logging
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))

logger = logging.getLogger(__name__)

CARD_SELECTOR = "li.cf.card"
NAME_LINK_SELECTOR = "a.js-sku-link.pic"
SPECS_SELECTOR = "div.card-content > p"
PRICE_SELECTOR = "div.price.react-component.reviewable div a"
INSTALLMENTS_SELECTOR = "span.installments-label"
RATING_SELECTOR = "div.rating-with-count span"
REVIEWS_SELECTOR = "div.rating-with-count a:last-child"
NEXT_SELECTOR = "a.button.button-large.button-secondary.next"

# Markup-drift guard: minimum share of rows that must have a valid value.
# Only fields that are near-100% populated on a healthy day are guarded —
# Specs is ~30% empty for smartwatches and installments/ratings are
# legitimately absent on many products, so they can't be thresholded.
DRIFT_THRESHOLDS = {
    "Product": (0.90, NAME_LINK_SELECTOR),
    "Link": (0.90, NAME_LINK_SELECTOR),
    "Price_EUR": (0.80, PRICE_SELECTOR),
}


@dataclass(frozen=True)
class ScraperConfig:
    category: str
    url: str
    folder: str
    file_prefix: str
    log_name: str
    extract_memory_info: bool = False


CONFIGS = {
    "phones": ScraperConfig(
        category="phones",
        url="https://www.skroutz.gr/c/40/kinhta-thlefwna.html",
        folder="Phones_skroutz",
        file_prefix="skroutz_phones",
        log_name="scraper_phones.log",
        extract_memory_info=True,
    ),
    "laptops": ScraperConfig(
        category="laptops",
        url="https://www.skroutz.gr/c/25/laptop.html",
        folder="Laptops_skroutz",
        file_prefix="skroutz_laptops",
        log_name="scraper_laptops.log",
    ),
    "tablets": ScraperConfig(
        category="tablets",
        url="https://www.skroutz.gr/c/1105/tablet.html",
        folder="Tablets_skroutz",
        file_prefix="skroutz_tablets",
        log_name="scraper_tablets.log",
    ),
    "smartwatches": ScraperConfig(
        category="smartwatches",
        url="https://www.skroutz.gr/c/1705/Smartwatches.html",
        folder="Smartwatches_skroutz",
        file_prefix="skroutz_Smartwatches",
        log_name="scraper_smartwatches.log",
    ),
}


def _chrome_major():
    """Return the installed Chrome major version so undetected_chromedriver fetches the matching driver."""
    try:
        v = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command",
             r'(Get-Item "C:\Program Files\Google\Chrome\Application\chrome.exe").VersionInfo.ProductVersion'],
            text=True, stderr=subprocess.DEVNULL, timeout=5,
        ).strip()
        return int(v.split(".")[0])
    except Exception:
        return 0


def parse_card(card, extract_memory_info=False):
    """
    Extract one product row from a listing-card element.

    Every field falls back to "N/A" individually so one broken sub-element
    doesn't lose the whole card; systemic selector breakage is caught by the
    markup-drift guard before the CSV is written.
    """
    # --- Product name & canonical link ---
    try:
        a = card.find_element(By.CSS_SELECTOR, NAME_LINK_SELECTOR)
        name = a.get_attribute("title").strip()
        href = a.get_attribute("href").strip()
        # Some hrefs are relative paths; ensure we always store a full URL
        full = href if href.startswith("http") else "https://www.skroutz.gr" + href
        link = full.split("?")[0]   # strip tracking params before storing
    except Exception:
        name = link = "N/A"

    # --- Short spec summary shown on the card ---
    try:
        specs = card.find_element(By.CSS_SELECTOR, SPECS_SELECTOR).text.strip()
    except Exception:
        specs = "N/A"

    # --- RAM / Storage variants (phones only, e.g. "Μνήμη: 8/128GB, 8/256GB") ---
    # Try DOM elements first; fall back to full card text regex if not found
    memory_info = "N/A"
    if extract_memory_info:
        try:
            memory_els = card.find_elements(
                By.XPATH,
                ".//p[contains(text(), 'Μνήμη:')] | .//div[contains(text(), 'Μνήμη:')] | .//span[contains(text(), 'Μνήμη:')]"
            )
            for el in memory_els:
                if "Μνήμη:" in el.text:
                    memory_info = el.text.strip()
                    break
        except Exception:
            pass
        if memory_info == "N/A":
            try:
                match = re.search(r"Μνήμη:\s*([^\n]+)", card.text)
                if match:
                    memory_info = match.group(0).strip()
            except Exception:
                pass

    # --- Price ---
    # Stored raw (e.g. "1.800,00 €" → "1.800.00") so the cleaner handles formatting
    try:
        price_el = card.find_element(By.CSS_SELECTOR, PRICE_SELECTOR)
        price = (price_el.text.strip()
                 .replace(" ", "").replace(",", ".")
                 .replace("€", "").replace("από", "").strip())
    except Exception:
        price = "N/A"

    # --- Installment plan ---
    # Example text: "44,10 €/μήνα σε 24 δόσεις" (44.10 €/month over 24 installments)
    per_month = all_installments = "N/A"
    try:
        inst_text = card.find_element(By.CSS_SELECTOR, INSTALLMENTS_SELECTOR).text.strip()
        # Group 1 = monthly amount (Greek decimal comma, e.g. "44,10")
        # Group 2 = number of installments (e.g. "24")
        match = re.search(r"([\d,.]+)\s*€?/μήνα σε (\d+)", inst_text)
        if match:
            per_month = match.group(1)
            all_installments = match.group(2)
    except Exception:
        pass

    # --- User rating & review count ---
    try:
        rating = card.find_element(By.CSS_SELECTOR, RATING_SELECTOR).text.strip()
    except Exception:
        rating = "N/A"

    # The reviews element sometimes renders count and rating joined by a newline
    # (e.g. "1\n0.0") — the count is always the first integer in the text.
    try:
        reviews_text = card.find_element(By.CSS_SELECTOR, REVIEWS_SELECTOR).text.strip()
        m = re.search(r"\d+", reviews_text)
        reviews = m.group(0) if m else "N/A"
    except Exception:
        reviews = "N/A"

    row = {"Product": name, "Specs": specs}
    if extract_memory_info:
        row["Memory_Info"] = memory_info
    row.update({
        "Price_EUR": price,
        "Installments_per_month": per_month,
        "Installments_in_total": all_installments,
        "Rating": rating,
        "Reviews": reviews,
        "Link": link,
    })
    return row


def _load_page(driver, url, attempts=3, backoff=10):
    for attempt in range(1, attempts + 1):
        try:
            driver.get(url)
            return
        except WebDriverException as e:
            if attempt == attempts:
                raise
            logger.warning(f"Page load failed (attempt {attempt}/{attempts}): {e.__class__.__name__} — retrying in {backoff * attempt}s")
            time.sleep(backoff * attempt)


def _wait_for_cards(driver, attempts=3):
    for attempt in range(1, attempts + 1):
        try:
            WebDriverWait(driver, 15).until(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, CARD_SELECTOR))
            )
            return driver.find_elements(By.CSS_SELECTOR, CARD_SELECTOR)
        except TimeoutException:
            if attempt == attempts:
                raise
            logger.warning(f"No product cards after 15s (attempt {attempt}/{attempts}) — refreshing")
            driver.refresh()


def _goto_next_page(driver, attempts=3):
    """Click through to the next listing page. Returns False on the last page."""
    for attempt in range(1, attempts + 1):
        try:
            next_btn = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, NEXT_SELECTOR))
            )
        except TimeoutException:
            # Absent from the DOM entirely → genuinely the last page.
            if not driver.find_elements(By.CSS_SELECTOR, NEXT_SELECTOR):
                return False
            if attempt == attempts:
                logger.warning("Next button present but never clickable — treating as last page.")
                return False
            logger.warning(f"Next button not clickable (attempt {attempt}/{attempts}) — retrying")
            continue
        try:
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", next_btn)
            time.sleep(1.2)   # brief pause before clicking to mimic human behaviour
            next_btn.click()
            return True
        except WebDriverException as e:
            if attempt == attempts:
                raise
            logger.warning(f"Next-page click failed (attempt {attempt}/{attempts}): {e.__class__.__name__} — retrying")
            time.sleep(3)
    return False


def _check_markup_drift(df, category):
    """
    Detect systemic selector breakage: if a guarded field is below its valid-rate
    threshold, the day's data is garbage — refuse to write it and exit non-zero
    so the pipeline aborts loudly instead of loading a day of NULLs.
    """
    if df.empty:
        logger.error(f"[{category}] 0 products scraped — aborting without writing a CSV.")
        sys.exit(1)

    failures = []
    for col, (threshold, selector) in DRIFT_THRESHOLDS.items():
        s = df[col].astype(str)
        if col == "Price_EUR":
            valid = s.str.contains(r"\d", regex=True)   # "N/A" has no digit
        else:
            valid = s.ne("N/A") & s.str.len().gt(0)
        rate = valid.mean()
        if rate < threshold:
            failures.append((col, rate, threshold, selector))

    if failures:
        for col, rate, threshold, selector in failures:
            logger.error(
                f"[{category}] markup drift: only {rate:.0%} of {len(df)} rows have a valid "
                f"'{col}' (threshold {threshold:.0%}) — selector likely broken: \"{selector}\""
            )
        logger.error(f"[{category}] refusing to write garbage CSV — exiting non-zero.")
        sys.exit(1)


def scrape(cfg: ScraperConfig):
    # Log to both console and a persistent file so failures are traceable
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(os.path.join(HERE, cfg.log_name), encoding="utf-8", mode="w"),
        ],
    )

    # --headless is intentionally omitted because it triggers bot-detection on skroutz
    options = uc.ChromeOptions()
    options.add_argument("--disable-gpu")

    driver = uc.Chrome(options=options, version_main=_chrome_major() or None)
    try:
        _load_page(driver, cfg.url)

        products = []
        page = 1

        # Paginate through all listing pages until the "next" button disappears
        while True:
            logger.info(f"Σελίδα {page}…")
            cards = _wait_for_cards(driver)
            for card in cards:
                products.append(parse_card(card, extract_memory_info=cfg.extract_memory_info))

            if not _goto_next_page(driver):
                logger.info("Τέλος σελίδων.")
                break
            page += 1
            time.sleep(3)   # wait for the next page to load

        df = pd.DataFrame(products).drop_duplicates(subset="Link", keep="first")
        _check_markup_drift(df, cfg.category)

        # Save raw data; date-stamp prevents overwrites and enables historical comparison
        output_folder = os.path.join(HERE, cfg.folder)
        os.makedirs(output_folder, exist_ok=True)
        today = datetime.date.today().isoformat()
        full_path = os.path.join(output_folder, f"{cfg.file_prefix}_{today}.csv")
        tmp_path = full_path + ".tmp"
        df.to_csv(tmp_path, index=False, encoding="utf-8-sig")   # utf-8-sig for Excel compatibility with Greek text
        os.replace(tmp_path, full_path)   # atomic: never leave a half-written CSV
        logger.info(f"Αποθηκεύτηκε: {full_path} | {len(df)} προϊόντα")
    finally:
        driver.quit()   # always release Chrome, even if an exception occurs mid-scrape
