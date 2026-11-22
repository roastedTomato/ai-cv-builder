"""Estimate 730-hour Sydney MySQL test costs using Decimal arithmetic.

RDS, NAT and gp3 rates are from AWS Price List on 2026-11-23. Remaining rows
are planning envelopes, not regional quotes. Credits/free tier are excluded.
"""
from decimal import Decimal

HOURS = Decimal("730")
RDS_INSTANCE_HOURLY = Decimal("0.025")
NAT_GATEWAY_HOURLY = Decimal("0.059")
PUBLIC_IPV4_HOURLY = Decimal("0.005")
RDS_GP3_GB_MONTHLY = Decimal("0.138")
RDS_STORAGE_GB = Decimal("20")

FIXED_USD = {
    "RDS MySQL db.t4g.micro": RDS_INSTANCE_HOURLY * HOURS,
    "NAT Gateway": NAT_GATEWAY_HOURLY * HOURS,
    "NAT public IPv4": PUBLIC_IPV4_HOURLY * HOURS,
    "RDS gp3 20 GB": RDS_GP3_GB_MONTHLY * RDS_STORAGE_GB,
}

MONTHLY_USD = {
    "Lambda": ("0.80", "3.00"),
    "API Gateway": ("0.10", "0.50"),
    "S3": ("0.10", "0.50"),
    "CloudFront": ("0.20", "2.00"),
    "Cognito Lite": ("0.00", "0.00"),
    "CloudWatch": ("0.20", "1.00"),
    "Secrets Manager": ("0.41", "1.00"),
    "ECR": ("0.10", "0.30"),
    "SNS and Budgets": ("0.00", "0.10"),
}


def totals() -> tuple[Decimal, Decimal]:
    fixed = sum(FIXED_USD.values(), Decimal("0"))
    return tuple(fixed + sum((Decimal(row[index]) for row in MONTHLY_USD.values()), Decimal("0")) for index in (0, 1))


if __name__ == "__main__":
    low, high = totals()
    print(f"Planning envelope USD/month: {low:.2f}–{high:.2f}")
