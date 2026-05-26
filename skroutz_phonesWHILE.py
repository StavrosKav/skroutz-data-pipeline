"""
skroutz_phonesWHILE.py
----------------------
Web scraper for the Greek e-commerce platform skroutz.gr.
Navigates the smartphones category, iterates through all paginated pages,
and extracts product data (name, specs, price, installments, rating, reviews).

Output: CSV file saved to  Phones_skroutz/skroutz_phones_<YYYY-MM-DD>.csv
Run daily; each run creates a new date-stamped file.

Dependencies: undetected-chromedriver, selenium, pandas
"""

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import pandas as pd
import time
import undetected_chromedriver as uc   # bypasses bot-detection on skroutz
import re
import datetime
import os
import logging
import subprocess

# Log to both console and a persistent file so failures are traceable
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("scraper_phones.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


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


def scrape():
    # --headless is intentionally omitted because it triggers bot-detection on skroutz
    options = uc.ChromeOptions()
    options.add_argument("--disable-gpu")

    driver = uc.Chrome(options=options, version_main=_chrome_major())
    driver.get("https://www.skroutz.gr/c/40/kinhta-thlefwna.html")

    products = []
    page = 1

    # Paginate through all listing pages until the "next" button disappears
    while True:
        logger.info(f"Σελίδα {page}…")

        # Wait until product cards are present before scraping
        WebDriverWait(driver, 15).until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, "li.cf.card"))
        )
        cards = driver.find_elements(By.CSS_SELECTOR, "li.cf.card")

        for card in cards:

            # --- Product name & canonical link ---
            try:
                a = card.find_element(By.CSS_SELECTOR, "a.js-sku-link.pic")
                name = a.get_attribute("title").strip()
                href = a.get_attribute("href").strip()
                # Some hrefs are relative paths; ensure we always store a full URL
                link = href if href.startswith("http") else "https://www.skroutz.gr" + href
            except Exception:
                name = link = "N/A"

            # --- Short spec summary shown on the card (camera, display, battery) ---
            try:
                specs = card.find_element(By.CSS_SELECTOR, "div.card-content > p").text.strip()
            except Exception:
                specs = "N/A"

            # --- RAM / Storage variants (e.g. "Μνήμη: 8/128GB, 8/256GB") ---
            # Try DOM elements first; fall back to full card text regex if not found
            memory_info = "N/A"
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
            # Stored raw (e.g. "1.800,00 €") so the cleaning script handles formatting
            try:
                price_el = card.find_element(By.CSS_SELECTOR, "div.price.react-component.reviewable div a")
                price = price_el.text.strip().replace(" ", "").replace(",", ".").replace("€", "").strip()
            except Exception:
                price = "N/A"

            # --- Installment plan ---
            # skroutz renders installments inside <span class="installments-label">
            # Example text: "44,10 €/μήνα σε 24 δόσεις" (44.10 €/month over 24 installments)
            per_month = all_installments = "N/A"
            try:
                inst_el = card.find_element(By.CSS_SELECTOR, "span.installments-label")
                inst_text = inst_el.text.strip()
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
                rating = card.find_element(By.CSS_SELECTOR, "div.rating-with-count span").text.strip()
            except Exception:
                rating = "N/A"

            try:
                reviews = card.find_element(By.CSS_SELECTOR, "div.rating-with-count a:last-child").text.strip()
            except Exception:
                reviews = "N/A"

            products.append({
                "Product": name,
                "Specs": specs,
                "Memory_Info": memory_info,
                "Price_EUR": price,
                "Installments_per_month": per_month,
                "Installments_in_total": all_installments,
                "Rating": rating,
                "Reviews": reviews,
                "Link": link,
            })

        # --- Pagination: click "next" or stop if last page ---
        try:
            next_btn = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "a.button.button-large.button-secondary.next"))
            )
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", next_btn)
            time.sleep(1.2)   # brief pause before clicking to mimic human behaviour
            next_btn.click()
            page += 1
            time.sleep(3)     # wait for the next page to load
        except Exception:
            logger.info("Τέλος σελίδων.")
            break

    driver.quit()

    # Save raw data; date-stamp prevents overwrites and enables historical comparison
    output_folder = os.path.join(".", "Phones_skroutz")
    os.makedirs(output_folder, exist_ok=True)
    today = datetime.date.today().isoformat()
    df = pd.DataFrame(products)
    filename = f"skroutz_phones_{today}.csv"
    full_path = os.path.join(output_folder, filename)
    df.to_csv(full_path, index=False, encoding="utf-8-sig")   # utf-8-sig for Excel compatibility with Greek text
    logger.info(f"Αποθηκεύτηκε: {full_path} | {len(products)} προϊόντα")


if __name__ == "__main__":
    scrape()
