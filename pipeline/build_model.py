"""Build the contact-centre BI model from City of Toronto 311 open data.

Sources (Open Government Licence - Toronto):
  - 311 Contact Centre Performance Metrics (cxd-311-metrics.xlsx)
  - 311 Service Requests - Customer Initiated (sr2022.zip .. sr2026.zip)

Outputs (data/model/):
  star-schema CSVs for Power BI or any BI tool, a data-quality log,
  and dashboard.json for the web dashboard.

Run:  python pipeline/build_model.py
"""
from __future__ import annotations

import io
import json
import urllib.request
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "model"
OUT.mkdir(parents=True, exist_ok=True)

CKAN = "https://ckan0.cf.opendata.inter.prod-toronto.ca/dataset"
SOURCES = {
    "cxd-311-metrics.xlsx": f"{CKAN}/79d36086-b36f-4103-9a82-ce675b3bd5f8/resource/1be5dfeb-9efd-4d88-9a8b-44a3d8af7d51/download/cxd-311-metrics.xlsx",
    "sr2022.zip": f"{CKAN}/2e54bc0e-4399-4076-b717-351df5918ae7/resource/f00a3313-f074-463e-89a7-26563084fbef/download/sr2022.zip",
    "sr2023.zip": f"{CKAN}/2e54bc0e-4399-4076-b717-351df5918ae7/resource/079766f3-815d-4257-8731-5ff6b0c84c13/download/sr2023.zip",
    "sr2024.zip": f"{CKAN}/2e54bc0e-4399-4076-b717-351df5918ae7/resource/f46b640d-d465-4f8b-9db5-5000a08295cd/download/sr2024.zip",
    "sr2025.zip": f"{CKAN}/2e54bc0e-4399-4076-b717-351df5918ae7/resource/f3db05ab-2588-4159-89f7-56c74d1d8201/download/sr2025.zip",
    "sr2026.zip": f"{CKAN}/2e54bc0e-4399-4076-b717-351df5918ae7/resource/99b7f283-7345-4f5a-a126-d078ed4f3419/download/sr2026.zip",
}

# Channel taxonomy: how each 311 channel is classed for channel-shift KPIs.
CHANNEL_GROUP = {
    "Phone": "Agent voice",
    "Counter Visits": "In person",
    "Mail": "Assisted (other)",
    "Fax": "Assisted (other)",
    "Email": "Assisted digital",
    "Live Chat": "Assisted digital",
    "Twitter": "Assisted digital",
    "Facebook": "Assisted digital",
    "Social Media": "Assisted digital",
    "Web": "Self-service",
    "Mobile": "Self-service",
    "Mobile App": "Self-service",
    "Facility Booking": "Assisted (other)",
}
# Source spelling variants, normalised before mapping.
CHANNEL_ALIASES = {"Counter visits": "Counter Visits"}

dq: list[dict] = []  # data-quality log


def check(name: str, passed: bool, detail: str) -> None:
    dq.append({"check": name, "result": "PASS" if passed else "FLAG", "detail": detail})


def fetch() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    for fname, url in SOURCES.items():
        dest = RAW / fname
        if not dest.exists():
            print(f"downloading {fname}")
            urllib.request.urlretrieve(url, dest)


# ---------------------------------------------------------------- service requests
COLS = ["created", "status", "fsa", "street1", "street2", "ward", "sr_type", "division", "section"]


def parse_line(line: str) -> tuple[list[str], bool]:
    """Split one SR row. The source CSV does not quote fields, so a division with
    a comma in its name ('Parks, Forestry & Recreation' to 2024, 'Environment,
    Climate & Forestry' from 2025) arrives split in two; re-join it."""
    p = line.rstrip("\r\n").split(",")
    repaired = False
    if len(p) == 10 and p[8].startswith(" "):
        p = p[:7] + [p[7] + "," + p[8]] + p[9:]
        repaired = True
    return p, repaired


def load_service_requests() -> pd.DataFrame:
    frames = []
    for year in range(2022, 2027):
        z = zipfile.ZipFile(RAW / f"sr{year}.zip")
        rows, repaired, rejected = [], 0, 0
        with z.open(z.namelist()[0]) as f:
            text = io.TextIOWrapper(f, encoding="utf-8", errors="replace")
            header = text.readline().strip().split(",")
            check(f"SR {year} header", len(header) == 9, f"{len(header)} columns")
            for line in text:
                p, fixed = parse_line(line)
                repaired += fixed
                if len(p) != 9:
                    rejected += 1
                    continue
                rows.append(p)
        df = pd.DataFrame(rows, columns=COLS)
        check(f"SR {year} unquoted-comma repair", True, f"{repaired:,} rows had a comma inside the division name; re-joined")
        total = len(rows) + rejected
        check(f"SR {year} malformed rows", rejected / total < 0.001, f"{rejected:,} rows rejected of {total:,} ({rejected / total:.3%})")
        frames.append(df)
    sr = pd.concat(frames, ignore_index=True)

    # Parse via numpy (ISO strings) - faster than pd.to_datetime on 2M+ rows.
    iso = sr["created"].str.slice(0, 19).str.replace(" ", "T", regex=False)
    ok = iso.str.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")
    created = np.full(len(iso), np.datetime64("NaT"), dtype="datetime64[s]")
    created[ok.values] = np.array(iso[ok].tolist(), dtype="datetime64[s]")
    sr["created"] = pd.Series(created.astype("datetime64[ns]"), index=sr.index)
    bad_dates = int(sr["created"].isna().sum())
    check("SR creation timestamp parses", bad_dates == 0, f"{bad_dates:,} unparseable timestamps")
    sr = sr.dropna(subset=["created"])

    dupes = int(sr.duplicated().sum())
    check("SR exact duplicate rows", dupes / len(sr) < 0.01, f"{dupes:,} exact duplicates ({dupes / len(sr):.2%}); kept, since identical timestamps+type can be distinct requests")

    for c in ["status", "ward", "sr_type", "division", "section"]:
        sr[c] = sr[c].str.strip().replace("", "Unknown")
    unknown_ward = (sr["ward"] == "Unknown").mean()
    check("SR ward populated", unknown_ward < 0.05, f"{unknown_ward:.2%} of rows have no ward")

    w = sr["ward"].str.extract(r"^(?P<ward_name>.*?)\s*\((?P<ward_num>\d+)\)$")
    sr["ward_num"] = pd.to_numeric(w["ward_num"], errors="coerce").fillna(0).astype(int)
    sr["ward_name"] = w["ward_name"].fillna(sr["ward"])
    return sr


# ---------------------------------------------------------------- contact-centre metrics
def _table(df: pd.DataFrame, header_row: int) -> pd.DataFrame:
    t = df.iloc[header_row + 1:].copy()
    t.columns = [str(c).strip() for c in df.iloc[header_row]]
    return t.dropna(how="all")


def load_cxd() -> dict[str, pd.DataFrame]:
    x = pd.read_excel(RAW / "cxd-311-metrics.xlsx", sheet_name=None, header=None)

    def find_header(df: pd.DataFrame) -> int:
        for i in range(10):
            if str(df.iloc[i, 0]).strip() == "Year":
                return i
        raise ValueError("no header")

    out = {}
    cv = _table(x["Contact Volume"], find_header(x["Contact Volume"]))
    cv["year"] = pd.to_datetime(cv["Year"]).dt.year
    cv["month_start"] = pd.to_datetime(cv["year"].astype(str) + "-" + cv["Month"].astype(str).str[:3] + "-01", format="%Y-%b-%d")
    cv = cv.rename(columns={"Contact Channel": "channel", "Volume": "contacts"})
    cv["contacts"] = pd.to_numeric(cv["contacts"], errors="coerce").fillna(0).astype(int)
    cv["channel"] = cv["channel"].str.strip()
    variants = int(cv["channel"].isin(CHANNEL_ALIASES).sum())
    check("Channel name variants normalised", True, f"{variants} rows renamed: {CHANNEL_ALIASES}")
    cv["channel"] = cv["channel"].replace(CHANNEL_ALIASES)
    cv = cv.groupby(["month_start", "channel"], as_index=False)["contacts"].sum()
    unmapped = sorted(set(cv["channel"]) - set(CHANNEL_GROUP))
    check("Channel taxonomy covers all channels", not unmapped, f"unmapped: {unmapped}" if unmapped else f"{cv['channel'].nunique()} channels mapped")
    zero_ch = cv.groupby("channel")["contacts"].sum()
    for ch in zero_ch[zero_ch == 0].index:
        check(f"Channel '{ch}' has volume", False, "all months are zero; kept for completeness, excluded from charts")
    months = cv["month_start"].nunique()
    expected = len(pd.date_range(cv["month_start"].min(), cv["month_start"].max(), freq="MS"))
    check("Contact volume months complete", months == expected, f"{months} of {expected} months present ({cv['month_start'].min():%b %Y} to {cv['month_start'].max():%b %Y})")
    # Volume outliers: a month above 3x its channel's median is flagged for review, not removed.
    med = cv.groupby("channel")["contacts"].transform("median")
    spikes = cv[(med > 100) & (cv["contacts"] > 3 * med)]
    for ch, g in spikes.groupby("channel"):
        check(f"Channel '{ch}' monthly outliers", False,
              f"{len(g)} months above 3x median ({', '.join(g['month_start'].dt.strftime('%b %Y'))}); kept as reported, review with source owner")
    out["contacts"] = cv[["month_start", "channel", "contacts"]]

    sl = _table(x["ServiceLevel"], find_header(x["ServiceLevel"]))
    sl = sl.rename(columns={"Year": "year", "Service Level": "service_level", "Target": "target"})
    out["service_level"] = sl.astype({"year": int, "service_level": float, "target": float})

    si = _table(x["StatusInquiries Volume"], find_header(x["StatusInquiries Volume"]))
    si = si.rename(columns={"Year": "year", "Quarter": "quarter", "Volume": "status_inquiries"})
    out["status_inquiries"] = si.astype({"year": int, "status_inquiries": int})

    gi = _table(x["Top10GeneralInquiriesperQuater"], find_header(x["Top10GeneralInquiriesperQuater"]))
    gi = gi.rename(columns={"Year": "year", "Quarter": "quarter", "Topic": "topic", "Volume": "inquiries"})
    out["top_inquiries"] = gi.astype({"year": int, "inquiries": int})
    return out


# ---------------------------------------------------------------- forecast
def forecast_phone(contacts: pd.DataFrame, horizon: int = 12) -> tuple[pd.DataFrame, dict]:
    from statsmodels.tsa.holtwinters import ExponentialSmoothing

    s = (contacts[contacts["channel"] == "Phone"].set_index("month_start")["contacts"].asfreq("MS").astype(float))

    def fit(series):
        return ExponentialSmoothing(series, trend="add", damped_trend=True, seasonal="mul", seasonal_periods=12).fit(optimized=True)

    # Backtest: hold out the last 12 months, compare with a seasonal-naive baseline.
    train, test = s.iloc[:-12], s.iloc[-12:]
    hw = fit(train).forecast(12)
    naive = train.iloc[-12:].values
    mape = lambda a, f: float(np.mean(np.abs((a - f) / a)))
    backtest = {"holdout": f"{test.index[0]:%b %Y} to {test.index[-1]:%b %Y}",
                "mape_holt_winters": mape(test.values, hw.values),
                "mape_seasonal_naive": mape(test.values, naive)}

    model = fit(s)
    fc = model.forecast(horizon)
    resid_sd = float(np.std(model.resid / model.fittedvalues))  # relative residual spread
    z = 1.28  # 80% interval
    steps = np.arange(1, horizon + 1)
    widen = resid_sd * np.sqrt(steps)
    out = pd.DataFrame({"month_start": fc.index, "forecast": fc.values.round(),
                        "lo80": (fc.values * (1 - z * widen)).round(), "hi80": (fc.values * (1 + z * widen)).round()})
    hist = pd.DataFrame({"month_start": s.index, "actual": s.values, "fitted": model.fittedvalues.values.round()})
    return pd.concat([hist, out], ignore_index=True), backtest


# ---------------------------------------------------------------- build
def main() -> None:
    fetch()
    sr = load_service_requests()
    cxd = load_cxd()

    # dimensions
    sr_types = (sr[["sr_type", "division", "section"]].drop_duplicates().sort_values(["division", "section", "sr_type"]).reset_index(drop=True))
    sr_types.insert(0, "sr_type_key", range(1, len(sr_types) + 1))
    wards = sr[["ward_num", "ward_name"]].drop_duplicates().sort_values("ward_num").reset_index(drop=True)
    wards.insert(0, "ward_key", wards["ward_num"])
    sr = sr.merge(sr_types, on=["sr_type", "division", "section"])

    first = min(sr["created"].min().normalize(), cxd["contacts"]["month_start"].min())
    last = max(sr["created"].max().normalize(), pd.Timestamp("2027-12-31"))
    d = pd.DataFrame({"date": pd.date_range(first, last, freq="D")})
    d["year"], d["quarter"] = d["date"].dt.year, "Q" + d["date"].dt.quarter.astype(str)
    d["month_num"], d["month"] = d["date"].dt.month, d["date"].dt.strftime("%b")
    d["month_start"] = d["date"].dt.to_period("M").dt.to_timestamp()
    d["weekday_num"], d["weekday"] = d["date"].dt.weekday + 1, d["date"].dt.strftime("%a")
    d["is_weekend"] = d["weekday_num"] >= 6

    channels = pd.DataFrame({"channel": sorted(cxd["contacts"]["channel"].unique())})
    channels["channel_group"] = channels["channel"].map(CHANNEL_GROUP)
    channels["is_digital"] = channels["channel_group"].isin(["Self-service", "Assisted digital"])

    # facts
    sr["date"] = sr["created"].dt.normalize()
    sr["hour"] = sr["created"].dt.hour
    sr["month_start"] = sr["created"].dt.to_period("M").dt.to_timestamp()
    # Monthly grain keeps the type x ward x status detail small enough to ship in the repo;
    # the hourly fact (by division) carries the intraday demand shape.
    f_daily = (sr.groupby(["month_start", "sr_type_key", "ward_num", "status"]).size().rename("requests").reset_index().rename(columns={"ward_num": "ward_key"}))
    f_hourly = sr.groupby(["date", "hour", "division"]).size().rename("requests").reset_index()

    fc, backtest = forecast_phone(cxd["contacts"])

    # write model
    d.to_csv(OUT / "dim_date.csv", index=False, date_format="%Y-%m-%d")
    sr_types.to_csv(OUT / "dim_sr_type.csv", index=False)
    wards.to_csv(OUT / "dim_ward.csv", index=False)
    channels.to_csv(OUT / "dim_channel.csv", index=False)
    f_daily.to_csv(OUT / "fact_service_requests_monthly.csv", index=False, date_format="%Y-%m-%d")
    f_hourly.to_csv(OUT / "fact_service_requests_hourly.csv", index=False, date_format="%Y-%m-%d")
    cxd["contacts"].to_csv(OUT / "fact_contacts_monthly.csv", index=False, date_format="%Y-%m-%d")
    cxd["service_level"].to_csv(OUT / "fact_service_level_annual.csv", index=False)
    si = cxd["status_inquiries"].copy()
    qc = cxd["contacts"].assign(year=lambda t: t["month_start"].dt.year, quarter=lambda t: "Q" + t["month_start"].dt.quarter.astype(str)).groupby(["year", "quarter"])["contacts"].sum()
    si["contacts"] = [int(qc.get((y, q), 0)) for y, q in zip(si["year"], si["quarter"])]
    si["quarter_start"] = pd.PeriodIndex(si["year"].astype(str) + si["quarter"], freq="Q").to_timestamp()
    si.to_csv(OUT / "fact_status_inquiries_quarterly.csv", index=False, date_format="%Y-%m-%d")
    cxd["top_inquiries"].to_csv(OUT / "fact_top_inquiries_quarterly.csv", index=False)
    fc.to_csv(OUT / "forecast_phone_monthly.csv", index=False, date_format="%Y-%m-%d")

    check("SR rows reconcile to model", int(f_daily["requests"].sum()) == len(sr), f"{len(sr):,} requests in source = {int(f_daily['requests'].sum()):,} in fact table")
    check("Forecast beats seasonal-naive on holdout", backtest["mape_holt_winters"] < backtest["mape_seasonal_naive"],
          f"MAPE {backtest['mape_holt_winters']:.1%} vs {backtest['mape_seasonal_naive']:.1%} ({backtest['holdout']})")
    pd.DataFrame(dq).to_csv(OUT / "data_quality_log.csv", index=False)

    build_dashboard_json(sr, sr_types, cxd, channels, fc, backtest)
    for row in dq:
        print(f"[{row['result']}] {row['check']}: {row['detail']}")


def build_dashboard_json(sr, sr_types, cxd, channels, fc, backtest) -> None:
    c = cxd["contacts"].merge(channels, on="channel")
    c = c[c["channel"] != "Mail"]
    by_month = c.pivot_table(index="month_start", columns="channel_group", values="contacts", aggfunc="sum").fillna(0)
    total = by_month.sum(axis=1)
    c["quarter_key"] = c["month_start"].dt.year.astype(str) + "-Q" + c["month_start"].dt.quarter.astype(str)
    q_total = c.groupby("quarter_key")["contacts"].sum()
    si = cxd["status_inquiries"].copy()
    si["quarter_key"] = si["year"].astype(str) + "-" + si["quarter"]
    si["contacts"] = si["quarter_key"].map(q_total)
    si["follow_up_rate"] = si["status_inquiries"] / si["contacts"]

    hourly = sr.groupby([sr["created"].dt.weekday, "hour"]).size()
    n_weeks = (sr["created"].max() - sr["created"].min()).days / 7
    heat = (hourly / n_weeks).round(1).unstack(fill_value=0)

    last12 = sr[sr["created"] >= sr["created"].max() - pd.Timedelta(days=365)]
    top_types = (last12.groupby(["sr_type", "division"]).size().rename("requests").reset_index().sort_values("requests", ascending=False).head(15))
    status_mix = last12["status"].value_counts(normalize=True).round(4).to_dict()
    div_trend = (sr.assign(m=sr["created"].dt.to_period("M").dt.to_timestamp()).groupby(["m", "division"]).size().unstack(fill_value=0))

    gi = cxd["top_inquiries"]
    latest_q = gi[["year", "quarter"]].drop_duplicates().iloc[-1]
    gi_latest = gi[(gi["year"] == latest_q["year"]) & (gi["quarter"] == latest_q["quarter"])].sort_values("inquiries", ascending=False)

    fmt = lambda idx: [f"{t:%Y-%m}" for t in idx]
    out = {
        "source": "City of Toronto Open Data: 311 Contact Centre Performance Metrics; 311 Service Requests (Open Government Licence - Toronto)",
        "built": pd.Timestamp.today().strftime("%Y-%m-%d"),
        "months": fmt(by_month.index),
        "channel_groups": {g: by_month[g].astype(int).tolist() for g in by_month.columns},
        "total_contacts": total.astype(int).tolist(),
        "service_level": cxd["service_level"].to_dict(orient="records"),
        "follow_up": si[["quarter_key", "status_inquiries", "contacts", "follow_up_rate"]].dropna().to_dict(orient="records"),
        "heatmap": {"weekdays": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"], "hours": list(range(24)), "values": heat.values.tolist()},
        "top_sr_types": top_types.to_dict(orient="records"),
        "status_mix_last12": status_mix,
        "division_trend": {"months": fmt(div_trend.index), "series": {k: div_trend[k].astype(int).tolist() for k in div_trend.columns}},
        "top_inquiries_latest": {"period": f"{int(latest_q['year'])} {latest_q['quarter']}", "rows": gi_latest[["topic", "inquiries"]].to_dict(orient="records")},
        "forecast": {"months": fmt(fc["month_start"]),
                     "actual": [None if pd.isna(v) else int(v) for v in fc["actual"]],
                     "forecast": [None if pd.isna(v) else int(v) for v in fc["forecast"]],
                     "lo80": [None if pd.isna(v) else int(v) for v in fc["lo80"]],
                     "hi80": [None if pd.isna(v) else int(v) for v in fc["hi80"]],
                     "backtest": backtest},
        "sr_total": int(len(sr)),
        "data_quality": dq,
    }
    payload = json.dumps(out, default=str)
    (OUT / "dashboard.json").write_text(payload)
    # Inline the data into the static dashboard (works from file:// and GitHub Pages).
    template = (ROOT / "docs" / "template.html").read_text(encoding="utf-8")
    safe = payload.replace("</", "<\\/")
    (ROOT / "docs" / "index.html").write_text(template.replace("/*__DATA__*/null", safe), encoding="utf-8")


if __name__ == "__main__":
    main()
