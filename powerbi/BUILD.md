# Building the Power BI report

The model in `data/model/` is a star schema ready for Power BI Desktop (free, Windows). A first build takes about two hours.

## 1. Load

**Get data → Text/CSV** for each file in `data/model/` (skip `dashboard.json` and `data_quality_log.csv`, or load the log as a governance page). Set the types:

| Table | Columns to type |
|---|---|
| `dim_date` | `date`, `month_start` → Date; `is_weekend` → True/False |
| `fact_contacts_monthly` | `month_start` → Date; `contacts` → Whole number |
| `fact_service_requests_monthly` | `month_start` → Date; `requests` → Whole number |
| `fact_service_requests_hourly` | `date` → Date; `hour`, `requests` → Whole number |
| `fact_status_inquiries_quarterly` | `quarter_start` → Date |
| `forecast_phone_monthly` | `month_start` → Date |

Mark `dim_date` as the date table (**Table tools → Mark as date table**, column `date`).

## 2. Relationships (all single-direction, many-to-one)

| From (many) | To (one) |
|---|---|
| `fact_contacts_monthly[month_start]` | `dim_date[date]` |
| `fact_contacts_monthly[channel]` | `dim_channel[channel]` |
| `fact_service_requests_monthly[month_start]` | `dim_date[date]` |
| `fact_service_requests_monthly[sr_type_key]` | `dim_sr_type[sr_type_key]` |
| `fact_service_requests_monthly[ward_key]` | `dim_ward[ward_key]` |
| `fact_service_requests_hourly[date]` | `dim_date[date]` |
| `fact_status_inquiries_quarterly[quarter_start]` | `dim_date[date]` |
| `forecast_phone_monthly[month_start]` | `dim_date[date]` |

Monthly and quarterly facts join on the first day of the period, so slicing by `dim_date[year]`, `quarter` or `month_start` rolls them up correctly.

## 3. Measures

Create an empty table called `_Measures` and paste in the measures from `measures.dax`.

## 4. Pages (mirror the web dashboard)

1. **Executive summary.** Cards: `Contacts T12M`, `Phone Share T12M`, `Self-Service Share`, `Service Level`, `Follow-Up Contact Rate`, `Forecast Next 12M`. Add a line chart of `Phone Share` and `Self-Service Share` by `dim_date[month_start]`.
2. **Channel shift.** Stacked column of `Contacts` by `month_start` and `dim_channel[channel_group]`.
3. **Service and follow-up demand.** Column chart of `Service Level` by year with a constant line at 0.8. Column chart of `Follow-Up Contact Rate` by `quarter_start`.
4. **Forecast.** Line chart with `Phone Actual`, `Phone Forecast`, and an error band from `Phone Forecast Low 80` / `Phone Forecast High 80`.
5. **Demand drivers.** Matrix heatmap: `dim_date[weekday]` × `fact_service_requests_hourly[hour]`, values `Requests per Hour (avg)`, with conditional formatting. Bar chart of `Service Requests` by `dim_sr_type[sr_type]`, top N 10. Slicers for `dim_sr_type[division]` and `dim_ward[ward_name]`.
6. **Data governance.** A table of `data_quality_log`, plus a KPI dictionary (copy it from the README).

## 5. Publish

**File → Publish → Publish to web** (needs a free Power BI service account) gives a public embed link. Put it in the README next to the GitHub Pages dashboard, and save a screenshot to `docs/powerbi.png`.
