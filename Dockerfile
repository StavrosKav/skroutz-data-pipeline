FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de

# No Chrome here: the site refuses headless Chrome, so scraping runs on Windows
# and the container only does Clean + Load (+ reports) from the mounted CSVs.
ENV SKIP_SCRAPE=1

# Set working directory
WORKDIR /app

# Copy all project files
COPY . /app

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Run the pipeline: clean → load to PostgreSQL → charts/dashboard
CMD ["python", "run_pipeline.py"]
