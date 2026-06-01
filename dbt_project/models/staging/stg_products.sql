-- Staging model for the raw products table.
-- Selects all columns with clean aliases; downstream mart models ref this.

SELECT
    id,
    category,
    skroutz_link,
    product_name,
    brand,
    model,
    specs,
    ram_gb,
    storage_gb,
    num_cameras,
    camera_type,
    display_inches,
    battery_info,
    display_info,
    color,
    first_seen,
    last_seen
FROM {{ source('skroutz_raw', 'products') }}
