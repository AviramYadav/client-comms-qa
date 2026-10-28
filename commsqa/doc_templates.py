"""Plain-text templates for the three kinds of client correspondence.

The bank name is fictional. Older template versions are kept because part of
the network kept printing from them, which is one of the defects QA has to find.
"""

HEADER = """DEMO BANK LTD - CLIENT SERVICES
{title}
Ref            : {ref}
Date of issue  : {issued_on}

To : {name}
     {address}
     {city} - {pin}

Account        : {account}
"""

TRADE_BODY = """
Trade date     : {trade_date}
Settlement date: {settlement_date}
Transaction    : {side}
Security       : {security} ({isin})
Quantity       : {quantity}
Price (INR)    : {price}
Gross consideration (INR): {gross}
Brokerage (INR): {brokerage}
GST on brokerage (INR): {gst}
Net amount (INR): {net}
"""

PAYMENT_BODY = """
Value date     : {value_date}
Amount         : {currency} {amount}
Beneficiary    : {beneficiary}
Purpose        : {purpose}
"""

RATE_BODY = """
Product        : {product}
Current rate   : {old_rate}% p.a.
New rate       : {new_rate}% p.a.
Effective date : {effective_date}
"""

CLAUSES = {
    "TC_TERMS": "This confirmation is issued subject to the terms and conditions of your securities account.",
    "TC_DISCREPANCY": "Please report any discrepancy within 24 hours of receipt, failing which the details are deemed correct.",
    "PA_FRAUD": "If you did not authorise this payment, call 1800-000-0000 immediately.",
    "RN_EXIT": "You may close or switch this account without any charge within 30 days of this notice.",
    "GRIEVANCE": "For complaints, write to the Grievance Officer at grievance@demobank.example.",
}

TEMPLATES = {
    # version: (title, body, clause keys)
    "TC-v3.1": ("CONTRACT NOTE / TRADE CONFIRMATION", TRADE_BODY, ["TC_TERMS", "TC_DISCREPANCY", "GRIEVANCE"]),
    "TC-v2.9": ("CONTRACT NOTE / TRADE CONFIRMATION", TRADE_BODY, ["TC_TERMS", "GRIEVANCE"]),
    "PA-v2.0": ("PAYMENT ADVICE", PAYMENT_BODY, ["PA_FRAUD", "GRIEVANCE"]),
    "RN-v1.4": ("NOTICE OF CHANGE IN INTEREST RATE", RATE_BODY, ["RN_EXIT", "GRIEVANCE"]),
}
CURRENT_VERSION = {"TRADE_CONFIRM": "TC-v3.1", "PAYMENT_ADVICE": "PA-v2.0", "RATE_NOTICE": "RN-v1.4"}
REQUIRED_CLAUSES = {doc_type: TEMPLATES[v][2] for doc_type, v in CURRENT_VERSION.items()}


def render(version: str, fields: dict) -> str:
    title, body, clauses = TEMPLATES[version]
    text = HEADER.format(title=title, **fields) + body.format(**fields) + "\n"
    text += "\n".join(CLAUSES[c] for c in clauses)
    return text + f"\n\nTemplate {version}\n"
