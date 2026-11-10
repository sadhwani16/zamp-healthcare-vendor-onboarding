# Reference data and packet fixtures

`oig_exclusions.csv` is the full HHS OIG LEIE public download.
`oig_exclusions.source.json` records its official URL, source date, row count,
and SHA-256. Refresh it monthly with `python refresh_reference_data.py --oig`.

The LEIE CSV contains names and public identifiers but not SSNs or EINs. A
name match is a review candidate; confirm identity using the HHS OIG online
verification service before treating it as an exclusion. If the CSV is
missing, altered, or older than 45 days, onboarding remains pending.

`npi_cache.json` contains responses fetched from the official CMS NPPES API
for the packet NPIs. Entries expire after 30 days. Refresh with
`python refresh_reference_data.py --packet-npis`.

`packets_current` contains generated scenario PDFs. They are fixtures, not
issuer-authenticated documents. The old unused packet tree was removed.
