
# save_excel.py

from gmail_reader import get_payment_emails 

# emails = get_payment_emails()
# print(emails[0])

import re
import pandas as pd

def _amount_to_float(amount_str):
    """
    Convert '$1,234.56' -> 1234.56
    """
    if amount_str is None:
        return None
    s = str(amount_str).strip()
    s = re.sub(r"[\$,]", "", s)
    try:
        return float(s)
    except Exception:
        return None


def save_get_payment_emails_to_two_excels(
    transactions_xlsx="Payments_Transactions.xlsx",
    summary_xlsx="Payments_Summary_ByPerson_ByMonth.xlsx",
):
    """
    Uses get_payment_emails() output and creates:

    1) transactions_xlsx:
         Title, Amount, Recipient, Sender, Source, Email Date, Email, Shipping Address

    2) summary_xlsx:
         One row per Person, with:
           - total per month (all sources)
           - total per month by source (Venmo/PayPal/Zelle)
           - overall total by source per person
           - overall total per person

    Notes:
      - 'Person' is defined as counterparty: Recipient if present else Sender.
      - Requires get_payment_emails() to exist and return dicts with:
            source, subject, parsed{amount,name_paid,name_received,email_date,email,shipping_address}, email_timestamp
    """
    emails = get_payment_emails()

    # --------------------------
    # Build transactions table
    # --------------------------
    rows = []
    for e in emails:
        parsed = e.get("parsed", {}) or {}

        title = e.get("subject", "")
        source = e.get("source", "")

        amount = _amount_to_float(parsed.get("amount"))
        recipient = parsed.get("name_paid")
        sender = parsed.get("name_received")
        email_date = parsed.get("email_date")
        payer_email = parsed.get("email")
        shipping_address = parsed.get("shipping_address")

        # keep only valid
        if amount is None or (recipient is None and sender is None) or not email_date:
            continue

        rows.append({
            "Title": title,
            "Amount": amount,
            "Recipient": recipient if recipient else "-",
            "Sender": sender if sender else "-",
            "Source": source,
            "Email Date": email_date,
            "Email": payer_email if payer_email else "-",
            "Shipping Address": shipping_address if shipping_address else "-",
        })

    df_tx = pd.DataFrame(rows)
    df_tx["Email Date"] = pd.to_datetime(df_tx["Email Date"], errors="coerce")
    df_tx = df_tx.dropna(subset=["Email Date"]).sort_values("Email Date")

    df_tx.to_excel(transactions_xlsx, index=False)
    print(f"✅ Saved transactions file: {transactions_xlsx} (rows={len(df_tx)})")

    # --------------------------
    # Build summary table
    # --------------------------
    df = df_tx.copy()

    # define Person (counterparty)
    df["Person"] = df["Recipient"].where(df["Recipient"] != "-", df["Sender"])
    df = df[df["Person"].notna() & (df["Person"] != "-")].copy()

    # month key
    df["Month"] = df["Email Date"].dt.to_period("M").astype(str)  # "YYYY-MM"

    # total per person per month (all sources)
    pivot_total = (
        df.pivot_table(index="Person", columns="Month", values="Amount", aggfunc="sum", fill_value=0.0)
        .sort_index(axis=1)
    )
    pivot_total.columns = [f"{c} Total" for c in pivot_total.columns]

    # per person per month per source
    pivot_src = (
        df.pivot_table(index="Person", columns=["Month", "Source"], values="Amount", aggfunc="sum", fill_value=0.0)
        .sort_index(axis=1)
    )
    pivot_src.columns = [f"{m} {s}" for (m, s) in pivot_src.columns]

    # totals per person per source (all months)
    totals_by_source = df.groupby(["Person", "Source"])["Amount"].sum().unstack("Source").fillna(0.0)

    # ensure consistent columns even if one source has no rows
    for col in ["Venmo", "PayPal", "Zelle"]:
        if col not in totals_by_source.columns:
            totals_by_source[col] = 0.0
    totals_by_source = totals_by_source[["Venmo", "PayPal", "Zelle"]]
    totals_by_source = totals_by_source.rename(columns={
        "Venmo": "Venmo_Total",
        "PayPal": "PayPal_Total",
        "Zelle": "Zelle_Total",
    })

    # total per person overall
    total_all = df.groupby("Person")["Amount"].sum().to_frame("Total_All")

    # combine into final summary
    summary = (
        pivot_total
        .join(pivot_src, how="outer")
        .join(totals_by_source, how="outer")
        .join(total_all, how="outer")
        .fillna(0.0)
        .reset_index()
    )

    # optional: put grand totals at the end
    grand_cols = ["Venmo_Total", "PayPal_Total", "Zelle_Total", "Total_All"]
    month_cols = [c for c in summary.columns if c not in (["Person"] + grand_cols)]
    summary = summary[["Person"] + month_cols + grand_cols]

    summary.to_excel(summary_xlsx, index=False)
    print(f"✅ Saved summary file: {summary_xlsx} (persons={summary.shape[0]})")

    return df_tx, summary


save_get_payment_emails_to_two_excels(
    transactions_xlsx="Payments_Transactions.xlsx",
    summary_xlsx="Payments_Summary_ByPerson_ByMonth.xlsx",
)
