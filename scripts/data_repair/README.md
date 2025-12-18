# Shipping Profile Repair & Implementation

This folder contains a helper to put all **Vinyl Record** products into the **Books and Media** delivery profile.

## Script: assign_shipping_profile.py

What it does:
- Finds products where `product_type = "Vinyl Record"` (default) or a tag filter.
- Looks up the delivery profile (defaults to name `Books and Media`, or you can pass an ID).
- Assigns all matching variants to that profile via the Shopify Admin GraphQL API (`deliveryProfileUpdate` with `variantsToAssociate`).
- Supports dry-run and apply.

Prereqs:
- Env var `SHOPIFY_ADMIN_TOKEN` set to an Admin API access token.
- Store domain is hard-coded to `a908bf-3.myshopify.com`; API version `2025-01`.

Usage:
```bash
# Dry run (no changes)
python scripts/data_repair/assign_shipping_profile.py --mode dry-run

# Apply to a known profile id
python scripts/data_repair/assign_shipping_profile.py \
  --profile-id gid://shopify/DeliveryProfile/... \
  --mode apply

# Use a tag instead of product_type
python scripts/data_repair/assign_shipping_profile.py \
  --filter-type tag --filter-value record --mode apply

```

Notes:
- Default profile name: `Books and Media`. Override with `--profile-name` or use `--profile-id`.
- Product filter defaults to `product_type = "Vinyl Record"`. Adjust `--filter-value` if your store uses a different value.
- The mutation used is `deliveryProfileUpdate` with `variantsToAssociate`; variants are sent in batches (size 80).
- The script prints sample product handles (up to 10) and total variants found; in dry-run mode it sends no writes.

Operational reminder:
- After bulk uploads of new records, rerun the script (`--mode dry-run` to inspect, then `--mode apply`) to catch and assign new variants to the shipping profile. Ensure `SHOPIFY_ADMIN_TOKEN` is set in the session before running.

Operational checklist:
1) Ensure new Record uploads set `product_type = "Vinyl Record"` (or consistent tag).
2) Run dry-run first; verify counts.
3) Run with `--mode apply` to attach variants.
4) Re-run after uploads to catch new products, or schedule if desired.
