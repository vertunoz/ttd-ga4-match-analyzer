from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import pandas as pd


MISSING_OPTION = "(not available)"

ATTRIBUTION_ORDER = [
    "All",
    "Always On",
    "CTV",
    "Direct Mail",
    "Mid Direct Mail",
    "Mission Wired",
    "Site",
]

TTD_GROUPS = [
    "All",
    "Always On",
    "CTV",
    "Direct Mail",
    "Mid Direct Mail",
    "Mission Wired",
]


@dataclass
class LoadedFile:
    dataframe: pd.DataFrame
    metadata: dict[str, Any]


def read_input_file(uploaded_file: Any, ga4_mode: bool = False) -> LoadedFile:
    """Read an uploaded CSV/XLSX file and clean the GA4 exported header when needed."""
    file_name = getattr(uploaded_file, "name", "") or "uploaded_file"
    raw = uploaded_file.getvalue()
    suffix = file_name.lower().rsplit(".", 1)[-1] if "." in file_name else ""

    metadata: dict[str, Any] = {
        "file_name": file_name,
        "skipped_rows": 0,
        "header_row": 0,
        "reader_notes": [],
    }

    if suffix in {"xlsx", "xls"}:
        if ga4_mode:
            df, header_row, skipped = _read_ga4_excel(raw)
            metadata["header_row"] = header_row
            metadata["skipped_rows"] = skipped
        else:
            df = pd.read_excel(io.BytesIO(raw), dtype=object)
        return LoadedFile(_clean_columns(df), metadata)

    if ga4_mode:
        df, header_row, skipped, notes = _read_ga4_csv(raw)
        metadata["header_row"] = header_row
        metadata["skipped_rows"] = skipped
        metadata["reader_notes"] = notes
        return LoadedFile(_clean_columns(df), metadata)

    df = pd.read_csv(io.BytesIO(raw), dtype=object, comment=None, low_memory=False)
    return LoadedFile(_clean_columns(df), metadata)


def _read_ga4_csv(raw: bytes) -> tuple[pd.DataFrame, int, int, list[str]]:
    text = _decode_bytes(raw)
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
    except csv.Error:
        dialect = csv.get_dialect("excel")

    rows = list(csv.reader(io.StringIO(text), dialect))
    header_idx = _find_header_row(rows)
    if header_idx is None:
        df = pd.read_csv(io.BytesIO(raw), dtype=object, comment="#", low_memory=False)
        return df, 0, 0, ["Could not confidently detect GA4 header; used normal CSV parsing."]

    header = [str(cell).strip() for cell in rows[header_idx]]
    records: list[list[Any]] = []
    skipped = header_idx
    notes: list[str] = []
    extra_field_rows = 0

    for row in rows[header_idx + 1 :]:
        if _is_skip_row(row):
            skipped += 1
            continue
        if len(row) > len(header):
            row = row[: len(header) - 1] + ["".join(row[len(header) - 1 :])]
            extra_field_rows += 1
        elif len(row) < len(header):
            row = row + [None] * (len(header) - len(row))
        records.append(row)

    if extra_field_rows:
        notes.append(f"Trimmed {extra_field_rows} GA4 rows with more fields than the header.")

    return pd.DataFrame(records, columns=header), header_idx, skipped, notes


def _read_ga4_excel(raw: bytes) -> tuple[pd.DataFrame, int, int]:
    preview = pd.read_excel(io.BytesIO(raw), dtype=object, header=None)
    rows = preview.fillna("").astype(str).values.tolist()
    header_idx = _find_header_row(rows) or 0
    header = [str(cell).strip() for cell in rows[header_idx]]
    data = preview.iloc[header_idx + 1 :].copy()
    data.columns = header
    data = data.dropna(how="all")
    if len(data.columns) > 0:
        first_col = data.iloc[:, 0].astype(str).str.strip().str.lower()
        data = data.loc[first_col.ne("grand total")]
    return data.reset_index(drop=True), header_idx, header_idx


def _decode_bytes(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _find_header_row(rows: list[list[Any]]) -> int | None:
    for idx, row in enumerate(rows[:100]):
        normalized = [_norm_col(cell) for cell in row]
        score = 0
        if any("transaction id" == cell or "transactionid" == cell for cell in normalized):
            score += 3
        if any("session source medium" == cell or "source medium" in cell for cell in normalized):
            score += 3
        if any("purchase revenue" == cell or "revenue" in cell for cell in normalized):
            score += 2
        if any(cell == "date" for cell in normalized):
            score += 1
        if score >= 4:
            return idx
    return None


def _is_skip_row(row: list[Any]) -> bool:
    stripped = [str(cell).strip() for cell in row if str(cell).strip()]
    if not stripped:
        return True
    first = stripped[0].lower()
    return first.startswith("#") or first == "grand total"


def _clean_columns(df: pd.DataFrame) -> pd.DataFrame:
    cleaned = df.copy()
    cleaned.columns = [str(col).strip() for col in cleaned.columns]
    return cleaned


def detect_columns(ttd_df: pd.DataFrame, ga4_df: pd.DataFrame) -> dict[str, str]:
    return {
        "ttd_id": _detect_column(ttd_df, ["Order Id", "Order ID", "OrderId"], ["order", "id"]),
        "ttd_campaign": _detect_column(
            ttd_df, ["Last Impression Campaign Name", "Campaign Name"], ["campaign"]
        ),
        "ttd_tracking": _detect_column(ttd_df, ["Tracking Tag Name"], ["tracking", "tag"]),
        "ttd_referrer": _detect_column(ttd_df, ["Conversion Referrer URL", "Referrer URL"], ["referrer"]),
        "ttd_conversion_id": _detect_column(ttd_df, ["Conversion ID"], ["conversion", "id"]),
        "ttd_revenue": _detect_column(ttd_df, ["Monetary Value", "Revenue"], ["monetary", "value"]),
        "ttd_time": _detect_column(ttd_df, ["Conversion Time"], ["conversion", "time"]),
        "ga4_id": _detect_column(ga4_df, ["Transaction ID", "Transaction Id"], ["transaction", "id"]),
        "ga4_source": _detect_column(
            ga4_df, ["Session source / medium", "Source / medium"], ["source", "medium"]
        ),
        "ga4_revenue": _detect_column(ga4_df, ["Purchase revenue", "Revenue"], ["purchase", "revenue"]),
        "ga4_date": _detect_column(ga4_df, ["Date"], ["date"]),
    }


def _detect_column(df: pd.DataFrame, exact_names: list[str], required_words: list[str]) -> str:
    if df is None or df.empty:
        return MISSING_OPTION

    norm_to_actual = {_norm_col(col): col for col in df.columns}
    for name in exact_names:
        found = norm_to_actual.get(_norm_col(name))
        if found is not None:
            return found

    for col in df.columns:
        norm = _norm_col(col)
        if all(word.lower() in norm for word in required_words):
            return col
    return MISSING_OPTION


def _norm_col(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value).strip().lower()).strip()


def process_data(
    ttd_df: pd.DataFrame,
    ga4_df: pd.DataFrame,
    mapping: dict[str, str],
    revenue_source: str = "GA4 revenue",
    period_mode: str = "Full period",
    date_range: tuple[pd.Timestamp, pd.Timestamp] | None = None,
    source_filter_mode: str = "All sources",
    selected_sources: list[str] | None = None,
    custom_sources: str = "",
    selected_groups: list[str] | None = None,
) -> dict[str, pd.DataFrame | dict[str, Any] | list[str]]:
    ttd_all = _prepare_ttd(ttd_df, mapping)
    ttd_view_rows = ttd_all[ttd_all["is_ttd_view_row"]].copy()
    ttd = ttd_all[~ttd_all["is_ttd_view_row"]].copy()
    ga4 = _prepare_ga4(ga4_df, mapping)

    ga4_txn = _dedupe_ga4_transactions(ga4)
    raw_matched = ttd.merge(
        ga4_txn,
        left_on="ttd_transaction_id_norm",
        right_on="ga4_transaction_id_norm",
        how="inner",
        suffixes=("", "_ga4"),
    )

    matched = _dedupe_matched_by_group(raw_matched)
    matched_all = _dedupe_all_group(raw_matched)
    matched_transactions = pd.concat([matched_all, matched], ignore_index=True)

    selected_revenue_col = "ga4_revenue" if revenue_source == "GA4 revenue" else "ttd_revenue"
    matched_transactions["selected_summary_revenue"] = matched_transactions[selected_revenue_col].fillna(0)
    raw_matched["selected_summary_revenue"] = raw_matched[selected_revenue_col].fillna(0)

    date_source = _choose_date_source(ttd, ga4)
    matched_transactions = _assign_periods(matched_transactions, period_mode, date_source)
    raw_matched = _assign_periods(raw_matched, period_mode, date_source)
    ga4_txn = _assign_site_periods(ga4_txn, period_mode, date_source)

    matched_transactions, raw_matched, ga4_txn = _apply_filters(
        matched_transactions,
        raw_matched,
        ga4_txn,
        date_range,
        source_filter_mode,
        selected_sources or [],
        custom_sources,
        selected_groups or TTD_GROUPS,
    )

    summary = build_summary(matched_transactions, ga4_txn)
    campaign_mapping = build_campaign_mapping(ttd, raw_matched)
    quality = build_data_quality(ttd, ga4, ga4_txn, raw_matched, mapping, len(ttd_all), len(ttd_view_rows))
    ttd_unmatched, ga4_unmatched = build_unmatched(ttd, ga4_txn, raw_matched)
    warnings = _build_warnings(mapping, ttd, ga4, date_source)
    if len(ttd_view_rows):
        warnings.append(f"Excluded {len(ttd_view_rows):,} TTD rows containing ttd_view from all matching and summaries.")

    return {
        "ttd_clean": ttd,
        "ttd_view_excluded": ttd_view_rows,
        "ga4_clean": ga4,
        "summary": summary,
        "matched_transactions": _select_matched_columns(matched_transactions),
        "raw_matched_rows": _select_raw_columns(raw_matched),
        "campaign_mapping": campaign_mapping,
        "data_quality": quality,
        "ttd_unmatched": ttd_unmatched,
        "ga4_unmatched": ga4_unmatched,
        "date_source": date_source,
        "warnings": warnings,
    }


def _prepare_ttd(df: pd.DataFrame, mapping: dict[str, str]) -> pd.DataFrame:
    ttd = df.copy()
    ttd["_ttd_row_number"] = range(1, len(ttd) + 1)
    id_col = _column_or_none(mapping.get("ttd_id"))
    campaign_col = _column_or_none(mapping.get("ttd_campaign"))
    tracking_col = _column_or_none(mapping.get("ttd_tracking"))
    referrer_col = _column_or_none(mapping.get("ttd_referrer"))
    conversion_id_col = _column_or_none(mapping.get("ttd_conversion_id"))
    revenue_col = _column_or_none(mapping.get("ttd_revenue"))
    time_col = _column_or_none(mapping.get("ttd_time"))

    ttd["ttd_transaction_id_raw"] = _series_or_blank(ttd, id_col)
    ttd["ttd_transaction_id_norm"] = ttd["ttd_transaction_id_raw"].map(normalize_transaction_id)
    ttd["ttd_campaign_name"] = _series_or_blank(ttd, campaign_col)
    ttd["ttd_tracking_tag_name"] = _series_or_blank(ttd, tracking_col)
    ttd["ttd_conversion_referrer_url"] = _series_or_blank(ttd, referrer_col)
    ttd["ttd_conversion_id"] = _series_or_blank(ttd, conversion_id_col)
    ttd["ttd_conversion_time_raw"] = _series_or_blank(ttd, time_col)
    ttd["ttd_conversion_time_parsed"] = parse_ttd_dates(ttd["ttd_conversion_time_raw"]) if time_col else pd.NaT
    ttd["ttd_revenue"] = clean_revenue(_series_or_blank(ttd, revenue_col)) if revenue_col else 0.0
    ttd["ttd_revenue_missing"] = _series_or_blank(ttd, revenue_col).map(is_missing_value) if revenue_col else True
    ttd["is_ttd_view_row"] = ttd.apply(row_contains_ttd_view, axis=1)
    ttd["attribution_group"] = ttd.apply(classify_ttd_row, axis=1)
    return ttd


def _prepare_ga4(df: pd.DataFrame, mapping: dict[str, str]) -> pd.DataFrame:
    ga4 = df.copy()
    ga4["_ga4_row_number"] = range(1, len(ga4) + 1)
    id_col = _column_or_none(mapping.get("ga4_id"))
    source_col = _column_or_none(mapping.get("ga4_source"))
    revenue_col = _column_or_none(mapping.get("ga4_revenue"))
    date_col = _column_or_none(mapping.get("ga4_date"))

    ga4["ga4_transaction_id_raw"] = _series_or_blank(ga4, id_col)
    ga4["ga4_transaction_id_norm"] = ga4["ga4_transaction_id_raw"].map(normalize_transaction_id)
    ga4["ga4_source_medium_raw"] = _series_or_blank(ga4, source_col)
    ga4["ga4_source_medium_display"] = ga4["ga4_source_medium_raw"].map(display_source_medium)
    ga4["ga4_revenue"] = clean_revenue(_series_or_blank(ga4, revenue_col)) if revenue_col else 0.0
    ga4["ga4_revenue_missing"] = _series_or_blank(ga4, revenue_col).map(is_missing_value) if revenue_col else True
    ga4["ga4_date_raw"] = _series_or_blank(ga4, date_col)
    ga4["ga4_date_parsed"] = parse_ga4_dates(ga4["ga4_date_raw"]) if date_col else pd.NaT
    return ga4


def _column_or_none(value: str | None) -> str | None:
    if not value or value == MISSING_OPTION:
        return None
    return value


def _series_or_blank(df: pd.DataFrame, column: str | None) -> pd.Series:
    if column and column in df.columns:
        return df[column]
    return pd.Series([""] * len(df), index=df.index, dtype=object)


def normalize_transaction_id(value: Any) -> str | None:
    if is_missing_value(value):
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, int):
        return str(value)

    text = str(value).strip()
    if is_missing_value(text):
        return None

    text = re.sub(r"\s+", "", text)
    if re.fullmatch(r"\d+\.0+", text):
        return text.split(".", 1)[0]
    if re.fullmatch(r"\d+(?:\.\d+)?[eE][+-]?\d+", text):
        try:
            decimal_value = Decimal(text)
            if decimal_value == decimal_value.to_integral_value():
                return str(decimal_value.quantize(Decimal(1)))
        except (InvalidOperation, ValueError):
            pass
    return text


def is_missing_value(value: Any) -> bool:
    if pd.isna(value):
        return True
    text = str(value).strip()
    return text == "" or text.lower() in {"nan", "none", "null", "nat"}


def parse_ttd_dates(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce", dayfirst=False)


def parse_ga4_dates(series: pd.Series) -> pd.Series:
    text = series.astype(str).str.strip()
    parsed_compact = pd.to_datetime(text.where(text.str.fullmatch(r"\d{8}")), format="%Y%m%d", errors="coerce")
    parsed_general = pd.to_datetime(text, errors="coerce", dayfirst=False)
    return parsed_compact.fillna(parsed_general)


def clean_revenue(series: pd.Series) -> pd.Series:
    text = series.astype(str).str.strip()
    negative = text.str.match(r"^\(.*\)$", na=False)
    cleaned = (
        text.str.replace(r"[\$,€£,\s]", "", regex=True)
        .str.replace("(", "", regex=False)
        .str.replace(")", "", regex=False)
    )
    numbers = pd.to_numeric(cleaned, errors="coerce")
    numbers = numbers.mask(negative, -numbers.abs())
    return numbers.fillna(0.0).astype(float)


def classify_ttd_row(row: pd.Series) -> str:
    fields = [
        row.get("ttd_campaign_name", ""),
        row.get("ttd_tracking_tag_name", ""),
        row.get("ttd_conversion_referrer_url", ""),
        row.get("ttd_conversion_id", ""),
    ]
    text = " ".join(str(value) for value in fields).lower()

    if is_mid_direct_mail_text(text):
        return "Mid Direct Mail"
    if is_direct_mail_text(text):
        return "Direct Mail"
    if re.search(r"mission\s*wired|mission_wired|missionwired|\bmw\b", text, flags=re.I):
        return "Mission Wired"
    if re.search(r"\bctv\b|video_ctv|connected\s*tv", text, flags=re.I):
        return "CTV"
    return "Always On"


def row_contains_ttd_view(row: pd.Series) -> bool:
    text = " ".join(str(value) for value in row.tolist() if not is_missing_value(value)).lower()
    return bool(re.search(r"\bttd[_\s-]?view\b", text))


def is_mid_direct_mail_text(text: str) -> bool:
    return bool(re.search(r"\bmid\b", text, flags=re.I)) and is_direct_mail_text(text)


def is_direct_mail_text(text: str) -> bool:
    return bool(re.search(r"direct\s*mail|direct_mail|directmail|\bdm\b", text, flags=re.I))


def display_source_medium(value: Any) -> str:
    if is_missing_value(value):
        return "(missing source / medium)"
    text = str(value).strip()
    if text.lower() in {"(direct) / (none)", "web (direct)"}:
        return "Web (Direct)"
    return text


def _dedupe_ga4_transactions(ga4: pd.DataFrame) -> pd.DataFrame:
    valid = ga4[ga4["ga4_transaction_id_norm"].notna()].copy()
    if valid.empty:
        return valid

    def first_non_missing(series: pd.Series) -> Any:
        non_missing = series[~series.map(is_missing_value)]
        return non_missing.iloc[0] if not non_missing.empty else ""

    return (
        valid.groupby("ga4_transaction_id_norm", as_index=False)
        .agg(
            ga4_transaction_id_raw=("ga4_transaction_id_raw", first_non_missing),
            ga4_source_medium_raw=("ga4_source_medium_raw", first_non_missing),
            ga4_source_medium_display=("ga4_source_medium_display", first_non_missing),
            ga4_revenue=("ga4_revenue", "max"),
            ga4_date_raw=("ga4_date_raw", first_non_missing),
            ga4_date_parsed=("ga4_date_parsed", "min"),
            _ga4_row_number=("_ga4_row_number", "min"),
        )
        .reset_index(drop=True)
    )


def _dedupe_matched_by_group(raw_matched: pd.DataFrame) -> pd.DataFrame:
    if raw_matched.empty:
        return raw_matched.copy()
    sort_cols = ["ttd_transaction_id_norm", "attribution_group", "_ttd_row_number"]
    deduped = raw_matched.sort_values(sort_cols).drop_duplicates(
        ["ttd_transaction_id_norm", "attribution_group"], keep="first"
    )
    deduped = deduped.copy()
    deduped["source_of_attribution"] = deduped["attribution_group"]
    return deduped


def _dedupe_all_group(raw_matched: pd.DataFrame) -> pd.DataFrame:
    if raw_matched.empty:
        return raw_matched.copy()
    deduped = raw_matched.sort_values(["ttd_transaction_id_norm", "_ttd_row_number"]).drop_duplicates(
        ["ttd_transaction_id_norm"], keep="first"
    )
    deduped = deduped.copy()
    deduped["source_of_attribution"] = "All"
    return deduped


def _choose_date_source(ttd: pd.DataFrame, ga4: pd.DataFrame) -> str:
    if ttd["ttd_conversion_time_parsed"].notna().any():
        return "TTD Conversion Time"
    if ga4["ga4_date_parsed"].notna().any():
        return "GA4 Date"
    return "No parseable date"


def _assign_periods(df: pd.DataFrame, period_mode: str, date_source: str) -> pd.DataFrame:
    output = df.copy()
    date_col = "ttd_conversion_time_parsed" if date_source == "TTD Conversion Time" else "ga4_date_parsed"
    output["analysis_date"] = pd.to_datetime(output.get(date_col, pd.NaT), errors="coerce").dt.normalize()
    output["selected_period"] = _period_labels(output["analysis_date"], period_mode)
    return output


def _assign_site_periods(ga4_txn: pd.DataFrame, period_mode: str, date_source: str) -> pd.DataFrame:
    output = ga4_txn.copy()
    output["analysis_date"] = pd.to_datetime(output["ga4_date_parsed"], errors="coerce").dt.normalize()
    if date_source == "TTD Conversion Time":
        output["analysis_date"] = pd.to_datetime(output["ga4_date_parsed"], errors="coerce").dt.normalize()
    output["selected_period"] = _period_labels(output["analysis_date"], period_mode)
    return output


def _period_labels(dates: pd.Series, period_mode: str) -> pd.Series:
    if period_mode == "Weekly":
        labels = pd.Series(["Unknown Period"] * len(dates), index=dates.index, dtype=object)
        valid_dates = dates.dropna()
        if not valid_dates.empty:
            week_start = valid_dates - pd.to_timedelta(valid_dates.dt.weekday, unit="D")
            week_end = week_start + pd.Timedelta(days=6)
            labels.loc[valid_dates.index] = week_start.dt.strftime("%Y-%m-%d") + " - " + week_end.dt.strftime(
                "%Y-%m-%d"
            )
        return labels
    return pd.Series(["Full Period"] * len(dates), index=dates.index)


def _apply_filters(
    matched: pd.DataFrame,
    raw_matched: pd.DataFrame,
    ga4_txn: pd.DataFrame,
    date_range: tuple[pd.Timestamp, pd.Timestamp] | None,
    source_filter_mode: str,
    selected_sources: list[str],
    custom_sources: str,
    selected_groups: list[str],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if date_range:
        start, end = date_range
        start = pd.to_datetime(start).normalize()
        end = pd.to_datetime(end).normalize()
        matched = matched[matched["analysis_date"].isna() | matched["analysis_date"].between(start, end)]
        raw_matched = raw_matched[raw_matched["analysis_date"].isna() | raw_matched["analysis_date"].between(start, end)]
        ga4_txn = ga4_txn[ga4_txn["analysis_date"].isna() | ga4_txn["analysis_date"].between(start, end)]

    source_values: set[str] | None = None
    if source_filter_mode == "Direct only":
        source_values = {"Web (Direct)", "(direct) / (none)"}
    elif source_filter_mode == "Selected sources":
        source_values = set(selected_sources)
    elif source_filter_mode == "Custom source list":
        source_values = {item.strip() for item in re.split(r"[\n,]", custom_sources) if item.strip()}

    if source_values is not None:
        matched = matched[matched["ga4_source_medium_display"].isin(source_values)]
        raw_matched = raw_matched[raw_matched["ga4_source_medium_display"].isin(source_values)]
        ga4_txn = ga4_txn[ga4_txn["ga4_source_medium_display"].isin(source_values)]

    selected = set(selected_groups)
    if selected:
        selected.add("All")
        matched = matched[matched["source_of_attribution"].isin(selected)]

    return matched, raw_matched, ga4_txn


def build_summary(matched: pd.DataFrame, ga4_txn: pd.DataFrame) -> pd.DataFrame:
    site = (
        ga4_txn.groupby(["selected_period", "ga4_source_medium_display"], dropna=False)
        .agg(
            Conversions=("ga4_transaction_id_norm", "nunique"),
            Revenue=("ga4_revenue", "sum"),
        )
        .reset_index()
    )
    site["Source of attribution"] = "Site"

    attr = (
        matched.groupby(["selected_period", "ga4_source_medium_display", "source_of_attribution"], dropna=False)
        .agg(
            Conversions=("ttd_transaction_id_norm", "nunique"),
            Revenue=("selected_summary_revenue", "sum"),
        )
        .reset_index()
        .rename(columns={"source_of_attribution": "Source of attribution"})
    )

    site_denominator = site.rename(
        columns={
            "selected_period": "Period",
            "ga4_source_medium_display": "Session Source in GA4",
            "Conversions": "Site Conversions",
            "Revenue": "Site Revenue",
        }
    )[["Period", "Session Source in GA4", "Site Conversions", "Site Revenue"]]

    summary = pd.concat([attr, site], ignore_index=True)
    summary = summary.rename(
        columns={
            "selected_period": "Period",
            "ga4_source_medium_display": "Session Source in GA4",
        }
    )
    summary = summary.merge(site_denominator, on=["Period", "Session Source in GA4"], how="left")
    summary["Share of total conversions"] = (summary["Conversions"] / summary["Site Conversions"]).where(
        summary["Site Conversions"].ne(0)
    )
    summary["Share of total revenue"] = (summary["Revenue"] / summary["Site Revenue"]).where(
        summary["Site Revenue"].ne(0)
    )
    site_mask = summary["Source of attribution"].eq("Site")
    summary.loc[site_mask, ["Share of total conversions", "Share of total revenue"]] = pd.NA
    summary["Attribution Sort"] = summary["Source of attribution"].map(
        {name: idx for idx, name in enumerate(ATTRIBUTION_ORDER)}
    )
    summary = summary.sort_values(["Period", "Attribution Sort", "Conversions"], ascending=[True, True, False])
    return summary[
        [
            "Period",
            "Session Source in GA4",
            "Source of attribution",
            "Conversions",
            "Revenue",
            "Share of total conversions",
            "Share of total revenue",
        ]
    ].reset_index(drop=True)


def build_campaign_mapping(ttd: pd.DataFrame, raw_matched: pd.DataFrame) -> pd.DataFrame:
    matched_ids = set(raw_matched["ttd_transaction_id_norm"].dropna())
    table = ttd.copy()
    table["matched_flag"] = table["ttd_transaction_id_norm"].isin(matched_ids)
    grouped = (
        table.groupby(["ttd_campaign_name", "attribution_group"], dropna=False)
        .agg(
            **{
                "Raw TTD rows": ("_ttd_row_number", "count"),
                "Unique transaction IDs": ("ttd_transaction_id_norm", "nunique"),
                "Matched transactions": ("matched_flag", "sum"),
                "Revenue": ("ttd_revenue", "sum"),
                "Blank transaction ID rows": ("ttd_transaction_id_norm", lambda s: s.isna().sum()),
            }
        )
        .reset_index()
        .rename(
            columns={
                "ttd_campaign_name": "Campaign name",
                "attribution_group": "Assigned attribution group",
            }
        )
    )
    return grouped.sort_values(["Assigned attribution group", "Raw TTD rows"], ascending=[True, False])


def build_data_quality(
    ttd: pd.DataFrame,
    ga4: pd.DataFrame,
    ga4_txn: pd.DataFrame,
    raw_matched: pd.DataFrame,
    mapping: dict[str, str],
    ttd_raw_row_count: int,
    ttd_view_excluded_count: int,
) -> pd.DataFrame:
    ttd_ids = set(ttd["ttd_transaction_id_norm"].dropna())
    ga4_ids = set(ga4["ga4_transaction_id_norm"].dropna())
    matched_ids = set(raw_matched["ttd_transaction_id_norm"].dropna())
    discrepancy_count = _revenue_discrepancy_count(raw_matched)

    metrics = [
        ("TTD raw rows before ttd_view exclusion", ttd_raw_row_count),
        ("TTD rows excluded because of ttd_view", ttd_view_excluded_count),
        ("TTD rows used after ttd_view exclusion", len(ttd)),
        ("TTD rows with blank transaction ID", int(ttd["ttd_transaction_id_norm"].isna().sum())),
        ("TTD unique transaction IDs", len(ttd_ids)),
        ("GA4 raw rows", len(ga4)),
        ("GA4 unique transaction IDs", len(ga4_ids)),
        ("Matched transaction IDs", len(matched_ids)),
        ("Match rate vs TTD unique transaction IDs", _safe_rate(len(matched_ids), len(ttd_ids))),
        ("Match rate vs GA4 unique transaction IDs", _safe_rate(len(matched_ids), len(ga4_ids))),
        ("TTD IDs not found in GA4", len(ttd_ids - ga4_ids)),
        ("GA4 IDs not found in TTD", len(ga4_ids - ttd_ids)),
        ("Duplicate TTD transaction IDs", int(ttd["ttd_transaction_id_norm"].duplicated().sum())),
        ("Duplicate GA4 transaction IDs", int(ga4["ga4_transaction_id_norm"].duplicated().sum())),
        ("Missing TTD revenue rows", int(ttd["ttd_revenue_missing"].sum())),
        ("Missing GA4 revenue rows", int(ga4["ga4_revenue_missing"].sum())),
        ("Missing GA4 source / medium rows", int(ga4["ga4_source_medium_raw"].map(is_missing_value).sum())),
        ("Unparseable TTD Conversion Time rows", _unparseable_count(ttd, "ttd_conversion_time_raw", "ttd_conversion_time_parsed")),
        ("Unparseable GA4 Date rows", _unparseable_count(ga4, "ga4_date_raw", "ga4_date_parsed")),
        ("Revenue discrepancy between TTD and GA4 for matched IDs", discrepancy_count),
        ("GA4 rows available for Site denominator", len(ga4_txn)),
    ]
    if _column_or_none(mapping.get("ttd_time")) is None:
        metrics.append(("TTD Conversion Time column selected", "No"))
    if _column_or_none(mapping.get("ga4_date")) is None:
        metrics.append(("GA4 Date column selected", "No"))
    return pd.DataFrame(metrics, columns=["Check", "Value"])


def _safe_rate(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "0.0%"
    return f"{numerator / denominator:.1%}"


def _unparseable_count(df: pd.DataFrame, raw_col: str, parsed_col: str) -> int:
    raw_has_value = ~df[raw_col].map(is_missing_value)
    return int((raw_has_value & df[parsed_col].isna()).sum())


def _revenue_discrepancy_count(raw_matched: pd.DataFrame) -> int:
    if raw_matched.empty:
        return 0
    by_id = raw_matched.groupby("ttd_transaction_id_norm").agg(
        ttd_revenue=("ttd_revenue", "max"),
        ga4_revenue=("ga4_revenue", "max"),
    )
    return int((by_id["ttd_revenue"].round(2) != by_id["ga4_revenue"].round(2)).sum())


def build_unmatched(
    ttd: pd.DataFrame, ga4_txn: pd.DataFrame, raw_matched: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    matched_ids = set(raw_matched["ttd_transaction_id_norm"].dropna())
    ttd_unmatched = ttd[
        ttd["ttd_transaction_id_norm"].notna() & ~ttd["ttd_transaction_id_norm"].isin(matched_ids)
    ].copy()
    ga4_unmatched = ga4_txn[
        ga4_txn["ga4_transaction_id_norm"].notna() & ~ga4_txn["ga4_transaction_id_norm"].isin(matched_ids)
    ].copy()
    return ttd_unmatched, ga4_unmatched


def _select_matched_columns(df: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "ttd_transaction_id_norm",
        "ttd_transaction_id_raw",
        "ga4_transaction_id_raw",
        "ttd_campaign_name",
        "source_of_attribution",
        "ttd_tracking_tag_name",
        "ttd_conversion_id",
        "ttd_conversion_referrer_url",
        "ttd_conversion_time_raw",
        "ttd_conversion_time_parsed",
        "ga4_date_raw",
        "ga4_date_parsed",
        "ga4_source_medium_raw",
        "ga4_source_medium_display",
        "ttd_revenue",
        "ga4_revenue",
        "selected_summary_revenue",
        "selected_period",
    ]
    return df[[col for col in columns if col in df.columns]].rename(
        columns={
            "ttd_transaction_id_norm": "Normalized transaction ID",
            "ttd_transaction_id_raw": "Raw TTD Order Id",
            "ga4_transaction_id_raw": "Raw GA4 Transaction ID",
            "ttd_campaign_name": "TTD campaign name",
            "source_of_attribution": "Attribution group",
            "ttd_tracking_tag_name": "TTD tracking tag name",
            "ttd_conversion_id": "TTD conversion ID",
            "ttd_conversion_referrer_url": "TTD conversion referrer URL",
            "ttd_conversion_time_raw": "TTD conversion time raw",
            "ttd_conversion_time_parsed": "TTD conversion time parsed",
            "ga4_date_raw": "GA4 date raw",
            "ga4_date_parsed": "GA4 date parsed",
            "ga4_source_medium_raw": "GA4 source / medium raw",
            "ga4_source_medium_display": "GA4 source / medium display",
            "ttd_revenue": "TTD Monetary Value",
            "ga4_revenue": "GA4 Purchase revenue",
            "selected_summary_revenue": "Selected summary revenue",
            "selected_period": "Selected period",
        }
    )


def _select_raw_columns(df: pd.DataFrame) -> pd.DataFrame:
    preferred = [
        "_ttd_row_number",
        "ttd_transaction_id_raw",
        "ttd_transaction_id_norm",
        "ga4_transaction_id_raw",
        "ttd_campaign_name",
        "attribution_group",
        "ga4_source_medium_display",
        "ttd_revenue",
        "ga4_revenue",
        "selected_summary_revenue",
        "selected_period",
    ]
    remaining = [col for col in df.columns if col not in preferred]
    return df[[col for col in preferred + remaining if col in df.columns]]


def available_dates(ttd_df: pd.DataFrame, ga4_df: pd.DataFrame, mapping: dict[str, str]) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    ttd_time_col = _column_or_none(mapping.get("ttd_time"))
    ga4_date_col = _column_or_none(mapping.get("ga4_date"))
    dates = pd.Series(dtype="datetime64[ns]")
    if ttd_time_col and ttd_time_col in ttd_df.columns:
        dates = parse_ttd_dates(ttd_df[ttd_time_col])
    if dates.dropna().empty and ga4_date_col and ga4_date_col in ga4_df.columns:
        dates = parse_ga4_dates(ga4_df[ga4_date_col])
    dates = dates.dropna()
    if dates.empty:
        return None
    return dates.min().normalize(), dates.max().normalize()


def source_options(ga4_df: pd.DataFrame, mapping: dict[str, str]) -> list[str]:
    source_col = _column_or_none(mapping.get("ga4_source"))
    if not source_col or source_col not in ga4_df.columns:
        return []
    return sorted(ga4_df[source_col].map(display_source_medium).dropna().unique().tolist())


def _build_warnings(
    mapping: dict[str, str], ttd: pd.DataFrame, ga4: pd.DataFrame, date_source: str
) -> list[str]:
    warnings: list[str] = []
    required = {
        "ttd_id": "TTD transaction ID",
        "ga4_id": "GA4 transaction ID",
        "ga4_source": "GA4 source / medium",
        "ga4_revenue": "GA4 revenue",
    }
    for key, label in required.items():
        if _column_or_none(mapping.get(key)) is None:
            warnings.append(f"{label} column is not mapped.")
    if date_source == "No parseable date":
        warnings.append("No parseable TTD or GA4 date was found; only full-period analysis is reliable.")
    if ttd["ttd_transaction_id_norm"].notna().sum() == 0:
        warnings.append("No usable TTD transaction IDs were found after normalization.")
    if ga4["ga4_transaction_id_norm"].notna().sum() == 0:
        warnings.append("No usable GA4 transaction IDs were found after normalization.")
    return warnings
