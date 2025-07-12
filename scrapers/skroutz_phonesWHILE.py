from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import pandas as pd
import time
import undetected_chromedriver as uc
import re
import datetime
import os

# Skroutz.gr - Κινητά τηλέφωνα
options = uc.ChromeOptions()
options.add_argument("--disable-gpu")

# chrome_options.add_argument("--headless")  # once you’ve debugged
driver = uc.Chrome(options=options)
driver.get("https://www.skroutz.gr/c/40/kinhta-thlefwna.html")

base_url = "https://www.skroutz.gr/c/40/kinhta-thlefwna.html"
driver.get(base_url)
time.sleep(1)

products = []
page = 1
while True:
    print(f"📄 Σελίδα {page}…")
    WebDriverWait(driver, 10).until(
        EC.presence_of_all_elements_located((By.CSS_SELECTOR, "li.cf.card"))
    )
    cards = driver.find_elements(By.CSS_SELECTOR, "li.cf.card")

    for card in cards:
        # name & link
        try:
            a = card.find_element(By.CSS_SELECTOR, "a.js-sku-link.pic")
            name = a.get_attribute("title").strip()
            href = a.get_attribute("href").strip()
            link = href if href.startswith(
                "http") else "https://www.skroutz.gr" + href
        except:
            name = link = "N/A"

        # specs
        try:
            Specs = card.find_element(
                By.CSS_SELECTOR, "div.card-content > p").text.strip()

        except:
            Specs = "N/A"

        # price
        try:
            price_el = card.find_element(
                By.CSS_SELECTOR,
                "div.card-content > div.price.react-component.reviewable > div > a"
            )
            price = price_el.text.strip().replace(
                " ", "").replace(",", ".").replace("€", "").strip()
        except:
            price = "N/A"
        # installments amount
        try:
            installments_el = card.find_element(
                By.CSS_SELECTOR,
                "div.card-content > div.price.react-component.reviewable > div > span"
            ).text.strip()
            installment = installments_el.replace(
                "/μήνα σε ", "").replace("δόσεις", "").replace(" ", "").strip()
            match = re.search(r"([\d,.]+ ?€)(\d+)", installment)
            if match:
                per_month = match.group(1)
                all_installments = match.group(2)
        except:
            per_month = all_installments = "Ad!"
        # rating
        try:
            rating = card.find_element(
                By.CSS_SELECTOR,
                "div.card-content > div.rating-with-count.react-component > a > div > span"
            ).text
        except:
            rating = "N/A"
        # reviews
        try:
            reviews = card.find_element(
                By.CSS_SELECTOR,
                "div.card-content > div.rating-with-count.react-component > div > a"
            ).text.strip()
        except:
            reviews = "N/A"

        products.append({
            "Product": name,
            "Specs": Specs,
            "Price_EUR": price,
            "Installments_per_month": per_month,
            "Installments_in_total": all_installments,
            "Rating": rating,
            "Reviews": reviews,
            "Link": link
        })

    # pagination (outside the item-loop)
    try:
        next_btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable(
                (By.CSS_SELECTOR, "a.button.button-large.button-secondary.next"))
        )
        driver.execute_script("arguments[0].scrollIntoView();", next_btn)
        time.sleep(1)
        next_btn.click()
        page += 1
        time.sleep(3)  # wait for the next page to load
    except:
        print("Τέλος σελίδων.✅ (page: {page})")
        time.sleep(0.5)
        break

driver.quit()

# save
# output_folder = r"C:\Users\StavrosKV\Documents\Projects\ProjectsPY\SkroutzProject\Phones_skroutz"
output_folder = os.path.join('.', 'Phones_skroutz')
os.makedirs(output_folder, exist_ok=True)
today = datetime.date.today()
df = pd.DataFrame(products)
filename = f"skroutz_phones_{today}.csv"
full_path = os.path.join(output_folder, filename)
df.to_csv(full_path, index=False, encoding="utf-8-sig")
print("Τέλος! Αποθηκεύτηκε ✅")
