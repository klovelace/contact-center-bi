import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))
from build_model import CHANNEL_GROUP, parse_line  # noqa: E402

MODEL = ROOT / "data" / "model"


def test_parse_line_rejoins_comma_in_division():
    line = "2025-01-01 07:52:59.0000000,In Progress,M4G,,,Don Valley West (15),General Pruning,Environment, Climate & Forestry,Forestry Operations\r\n"
    p, fixed = parse_line(line)
    assert fixed and len(p) == 9
    assert p[7] == "Environment, Climate & Forestry"
    assert p[8] == "Forestry Operations"


def test_parse_line_leaves_clean_rows_alone():
    line = "2025-01-01 00:01:05.0000000,Completed,M8Y,,,Etobicoke-Lakeshore (03),Injured - Domestic,Municipal Licensing & Standards,Toronto Animal Services\n"
    p, fixed = parse_line(line)
    assert not fixed and len(p) == 9


def test_every_channel_is_classified():
    c = pd.read_csv(MODEL / "fact_contacts_monthly.csv")
    assert set(c["channel"]) <= set(CHANNEL_GROUP)


def test_channel_groups_sum_to_total():
    c = pd.read_csv(MODEL / "fact_contacts_monthly.csv")
    d = pd.read_csv(MODEL / "dim_channel.csv")
    m = c.merge(d, on="channel", how="left")
    assert m["channel_group"].notna().all()
    assert m.groupby("month_start")["contacts"].sum().equals(c.groupby("month_start")["contacts"].sum())


def test_fact_keys_resolve_to_dimensions():
    f = pd.read_csv(MODEL / "fact_service_requests_monthly.csv")
    assert set(f["sr_type_key"]) <= set(pd.read_csv(MODEL / "dim_sr_type.csv")["sr_type_key"])
    assert set(f["ward_key"]) <= set(pd.read_csv(MODEL / "dim_ward.csv")["ward_key"])


def test_follow_up_rate_in_plausible_range():
    si = pd.read_csv(MODEL / "fact_status_inquiries_quarterly.csv")
    rate = si["status_inquiries"] / si["contacts"]
    assert rate.between(0.01, 0.25).all()


def test_forecast_has_twelve_future_months_with_ordered_interval():
    fc = pd.read_csv(MODEL / "forecast_phone_monthly.csv")
    fut = fc[fc["forecast"].notna()]
    assert len(fut) == 12
    assert (fut["lo80"] <= fut["forecast"]).all() and (fut["forecast"] <= fut["hi80"]).all()
