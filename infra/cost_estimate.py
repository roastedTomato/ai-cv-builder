"""Sum the documented planning envelopes without floating-point arithmetic.

These are assumptions for a small fictional-account test, not regional AWS rates.
Confirm current Sydney prices and account credits in AWS Pricing Calculator.
"""
from decimal import Decimal


MONTHLY_USD = {
    "Lambda": ("0.80", "3.00"),
    "API Gateway": ("0.10", "0.50"),
    "DynamoDB": ("0.20", "1.00"),
    "S3": ("0.10", "0.50"),
    "CloudFront": ("0.20", "2.00"),
    "Cognito Lite": ("0.00", "0.00"),
    "CloudWatch": ("0.20", "1.00"),
    "Secrets Manager": ("0.41", "0.50"),
    "ECR": ("0.10", "0.30"),
    "SNS and Budgets": ("0.00", "0.10"),
}


def totals() -> tuple[Decimal, Decimal]:
    return tuple(sum((Decimal(row[index]) for row in MONTHLY_USD.values()), Decimal("0")) for index in (0, 1))


if __name__ == "__main__":
    low, high = totals()
    print(f"Planning envelope USD/month: {low:.2f}–{high:.2f}")
