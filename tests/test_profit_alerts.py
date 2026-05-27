from decimal import Decimal

from analytics.profit_alerts import ProfitInputs, calculate_profit_metrics, generate_alerts


def test_calculate_profit_metrics_uses_fixed_profit_formula():
    metrics = calculate_profit_metrics(
        ProfitInputs(
            gmv=Decimal("1000"),
            product_cost=Decimal("300"),
            ad_spend=Decimal("100"),
            logistics_cost=Decimal("80"),
            commission=Decimal("50"),
            tax=Decimal("20"),
            refund_amount=Decimal("40"),
            other_fees=Decimal("10"),
        )
    )

    assert metrics.cost_total == Decimal("600")
    assert metrics.profit == Decimal("400")
    assert metrics.profit_margin == Decimal("0.4")
    assert metrics.roi == Decimal("4")


def test_generate_alerts_covers_core_business_rules():
    metrics = calculate_profit_metrics(
        ProfitInputs(
            gmv=Decimal("600"),
            product_cost=Decimal("200"),
            ad_spend=Decimal("200"),
        )
    )

    alerts = generate_alerts(
        metrics=metrics,
        baseline_gmv=Decimal("1000"),
        baseline_roi=Decimal("2.5"),
        available_stock=6,
        recent_7d_sales=10,
        return_rate=Decimal("0.18"),
        baseline_return_rate=Decimal("0.10"),
        data_age_hours=24,
    )

    assert [alert.alert_type for alert in alerts] == [
        "gmv_drop",
        "roi_drop",
        "low_inventory",
        "high_return_rate",
        "stale_data",
    ]
