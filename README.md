# Offline TTD + GA4 Transaction Match Analyzer

This is a local Streamlit app for matching The Trade Desk transaction data to GA4 transaction exports. It runs fully offline on your computer after dependencies are installed.

## Install

```bash
pip install -r requirements.txt
```

## Run

```bash
streamlit run app.py
```

## Required Inputs

Upload two offline files:

- A The Trade Desk transaction report, usually with `Order Id`, `Last Impression Campaign Name`, `Monetary Value`, and optionally `Conversion Time`.
- A GA4 transaction export, usually with `Date`, `Transaction ID`, `Session source / medium`, and `Purchase revenue`.

CSV and Excel files are supported. GA4 CSV exports can include metadata rows, comment rows beginning with `#`, blank rows, and a `Grand total` row before or after the real table.

Use the `Client / project` selector at the top of the app:

- `CARE` uses the original TTD + GA4 workflow.
- `BCU` adds CM support and BCU-specific attribution groups.

BCU has two analysis modes:

- `TTD + CM only`: upload only TTD and CM, dedupe the two media sources, and export unique rows plus overlap audit. GA4 is not required and GA4 date/source filters are hidden.
- `TTD + CM -> GA4 overlap`: upload TTD, CM, and GA4, then compare the deduped media table against GA4.

## Matching Logic

The app normalizes transaction IDs from both files before matching. It:

- Converts numeric-looking IDs such as `9267367.0` to `9267367`.
- Strips spaces.
- Preserves alphanumeric IDs and meaningful symbols.
- Treats blanks, `nan`, `None`, and `null` as missing.
- Excludes missing IDs from matching while reporting them in Data Quality.

TTD `Order Id` is matched to GA4 `Transaction ID` after normalization.

Rows containing `ttd_view` in any TTD field are excluded before matching, summaries, campaign mapping, unmatched exports, and all calculated outputs. Data Quality shows how many rows were excluded.

TTD rows where `Monetary Value` is present and cleans to `0` are also excluded before matching and summaries. Data Quality shows how many rows were removed by this rule.

The UI includes a `TTD Tracking Tag Name filter`. Use it to limit the analysis to a specific conversion tag such as `Care - One Time Donation` before the matching starts.

## Attribution Groups

For `CARE`, TTD rows are classified into:

- `Mid Direct Mail`
- `Direct Mail`
- `Mission Wired`
- `CTV`
- `Always On`

The `All` row is an aggregate across matched TTD transactions. Rules are case-insensitive and live in `analysis_processor.py` in `classify_ttd_row`.

`Mid Direct Mail` is classified before `Direct Mail`, so Mid Direct Mail campaigns stay separate and do not fall into the regular Direct Mail group.

For `BCU`, media rows are classified into:

- `Prospecting`
- `Remarketing`
- `Retargeting`
- `Other`

BCU classification is based on campaign, ad group, tracking tag/activity text, and CM campaign/activity text.

## BCU TTD + CM Deduplication

For `BCU`, the app builds a deduped media transaction table before matching to GA4:

- TTD rows are cleaned by tracking tag, `ttd_view`, zero monetary value, and missing transaction ID rules.
- CM rows are read after the `Report Fields` metadata section and deduped by `ORD Value`.
- If the same transaction ID exists in both TTD and CM, it is kept once using the TTD-shaped row and marked `TTD + CM overlap`.
- TTD-only rows are marked `TTD only`.
- CM-only rows are mapped into TTD-style columns and marked `CM only`.

BCU exports include:

- `BCU TTD CM Deduped`
- `BCU TTD CM Overlap`

The GA4 overlap summary for BCU uses the deduped TTD + CM media table.

## Revenue Logic

The summary defaults to GA4 `Purchase revenue` because GA4 Site totals are the denominator. You can switch the summary to TTD `Monetary Value` in the UI.

Detailed exports always include both:

- `TTD Monetary Value`
- `GA4 Purchase revenue`

Revenue text is cleaned by removing currency symbols, commas, and whitespace before numeric conversion.

Rows with a real zero TTD monetary value are removed from analysis before matching because they are not useful for transaction/donation revenue reporting.

## Deduplication

Summary metrics count unique normalized transaction IDs:

- `Site` counts unique GA4 transaction IDs.
- `All` counts each matched transaction once.
- Individual attribution groups count each transaction once inside that group.
- If a transaction appears in multiple attribution groups, it can appear once in each group but only once in `All`.

Raw matched rows are exported separately so duplicates and source rows remain auditable.

## Date and Period Logic

TTD `Conversion Time` is the primary date source for matched transaction period logic when it is present and parseable. It supports US-format dates such as:

- `05/12/2026 12:58`
- `05/14/2026 16:11:32`

GA4 `Date` supports compact dates such as `20260301`.

Period modes:

- `Full period`
- `Weekly`, with Monday as the week start and labels like `2026-05-11 - 2026-05-17`

If no parseable dates are available, the app can still produce full-period analysis.

## Share Calculations

For each period and GA4 source / medium:

- Share of total conversions = attribution conversions / Site conversions
- Share of total revenue = attribution revenue / Site revenue

The `Site` row is calculated from GA4 only and acts as the denominator.

## Exports

The Excel workbook includes:

1. `Summary`
2. `Matched Transactions`
3. `Raw Matched Rows`
4. `Campaign Mapping`
5. `TTD Unmatched`
6. `GA4 Unmatched`
7. `Data Quality`

Separate CSV exports are also available for summary, matched transactions, campaign mapping, TTD unmatched, and GA4 unmatched.

## Known Limitations

This is an MVP. Campaign mapping is rule-based in code, not editable in the UI yet. Duplicate GA4 transaction IDs are deduplicated for summary purposes to avoid inflated totals; raw rows should be reviewed when duplicates are reported in Data Quality.

## Free Hosting Notes

The app is ready for Streamlit Community Cloud, Hugging Face Spaces, or another free Python app host because it has `app.py`, `requirements.txt`, and `.streamlit/config.toml`.

Important privacy note: hosted apps are no longer fully offline. Uploaded files are processed on the hosting provider's server while the app session runs. Do not use free public hosting for sensitive donor, transaction, or revenue files unless that is acceptable for your privacy requirements.

For fully private use, run the app locally with `streamlit run app.py` or deploy it to a private server you control.
