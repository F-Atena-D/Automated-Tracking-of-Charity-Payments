
# gmail_reader.py
from gmail_auth import authenticate_gmail
import base64
from datetime import datetime


def extract_email_body(payload):
    """
    Recursively extract the email body from a nested MIME payload.
    Tries text/plain first, falls back to text/html if needed.
    """
    def decode_data(data):
        return base64.urlsafe_b64decode(data).decode('utf-8')

    def get_body(part):
        # mime_type = part.get("mimeType", "")
        body_data = part.get("body", {}).get("data")
        
        if body_data:
            return decode_data(body_data)
        elif "parts" in part:
            for sub_part in part["parts"]:
                result = get_body(sub_part)
                if result:
                    return result
        return None

    return get_body(payload)


from bs4 import BeautifulSoup
import html2text

def html_to_text(html_body):
    # Option 1: BeautifulSoup (less structured but fast)
    soup = BeautifulSoup(html_body, 'html.parser')
    return soup.get_text(separator='\n', strip=True)


def normalize_name(name: str | None):
    """
    Convert name to all lower case and normalize whitespace.
    """
    if not name:
        return None

    return " ".join(name.strip().lower().split())


import re

def parse_payment_info(cleaned_text: str):
    """
    Extract sender/recipient, signed amount, and (if present) buyer email + shipping address.
    Paid  -> negative amount
    Received -> positive amount
    """

    # Amount like $75.00 or $1,234.56
    amount_match = re.search(
        r"\$(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,3})?",
        cleaned_text
    )
    amount = None
    if amount_match:
        amount = float(amount_match.group(0).replace("$", "").replace(",", ""))

    # Name pattern: 1–2 words
    name_pattern = r"([A-Za-z]+(?:\s+[A-Za-z]+){0,1})"

    # Paid
    name_match_paid = (
        re.search(rf"You sent {name_pattern}", cleaned_text)
        or re.search(rf"to {name_pattern} has been sent", cleaned_text)
        or re.search(rf"You paid {name_pattern}", cleaned_text)
    )
    name_paid = name_match_paid.group(1) if name_match_paid else None

    # Received
    name_match_received = (
        re.search(rf"{name_pattern} sent you", cleaned_text)
        or re.search(rf"{name_pattern} paid you", cleaned_text)
        or re.search(rf"You received a payment from {name_pattern}", cleaned_text)
        or re.search(
            rf"You received a payment of "
            r"\$\d{1,5}\.\d{2} USD from "
            rf"{name_pattern}",
            cleaned_text,
        )
        or re.search(
            rf"This email confirms that you have received a donation of "
            r"\$\d{1,5}\.\d{2} USD from "
            rf"{name_pattern}",
            cleaned_text,
        )
    )
    name_received = name_match_received.group(1) if name_match_received else None

    name_paid = normalize_name(name_paid)
    name_received = normalize_name(name_received)


    # ----------------------------
    # Email extraction
    # - Prefer the "Buyer information" block (PayPal)
    # - Otherwise fall back to first email in the text
    # ----------------------------
    email = None

    buyer_email_match = re.search(
        r"Buyer information\s+.*?\s([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})",
        cleaned_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if buyer_email_match:
        email = buyer_email_match.group(1)
    else:
        any_email_match = re.search(
            r"([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})",
            cleaned_text,
            flags=re.IGNORECASE,
        )
        if any_email_match:
            email = any_email_match.group(1)

    # Sometimes PayPal shows email in parentheses right after "from (...)"
    # This will already be caught by the generic email pattern above,
    # but you can keep it explicit as a preference if you want:
    from_paren_email = re.search(
        r"from\s*\(\s*([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})\s*\)",
        cleaned_text,
        flags=re.IGNORECASE,
    )
    if from_paren_email:
        email = from_paren_email.group(1)

    # ----------------------------
    # Shipping address extraction (PayPal-style)
    # Try to capture the block under "Shipping information"
    # ----------------------------
    shipping_address = None
    ship_block = re.search(
        r"Shipping information\s+(.*?)(?:\n\s*Shipping method|\n\s*Description|\n\s*Total:|\Z)",
        cleaned_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if ship_block:
        block = ship_block.group(1).strip()

        # Clean lines and remove empties
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]

        # Typical format:
        # [Name]
        # [Street]
        # [City, ST ZIP]  (sometimes 2 lines, sometimes more)
        if len(lines) >= 3:
            # Keep name separately if you want; here we return the address lines joined
            shipping_address = "\n".join(lines[1:])  # exclude the person name line

        # If it doesn't look like that, fall back to returning the whole block
        elif lines:
            shipping_address = "\n".join(lines)

    # ----------------------------
    # Sign the amount (existing logic)
    # ----------------------------
    signed_amount = None
    if amount is not None:
        if name_received and not name_paid:
            signed_amount = abs(amount)
        elif name_paid and not name_received:
            signed_amount = -abs(amount)
        elif name_received and name_paid:
            signed_amount = abs(amount)

    return {
        "name_paid": name_paid,
        "name_received": name_received,
        "amount": signed_amount,          # signed float
        "email": email,                   # extracted email if found
        "shipping_address": shipping_address,  # extracted address block if found
    }


###############################################################################################
from datetime import datetime
from typing import List, Dict, Any, Optional

def get_payment_emails(
    start_date: Optional[str] = None,   # "YYYY-MM-DD"
    end_date: Optional[str] = None,     # "YYYY-MM-DD"
    max_results_per_query: int = 10000,
) -> List[Dict[str, Any]]:
    """
    Universal Gmail fetcher for Venmo, PayPal, Zelle, and additional mailbox/sender filters.

    It:
      1) queries Gmail for multiple queries (senders + specific recipients),
      2) downloads each message (full),
      3) extracts subject/body,
      4) runs parse_payment_info(cleaned_body),
      5) attaches email timestamp + email_date,
      6) returns a single combined list with 'source'.

    Assumes you already have:
      - authenticate_gmail()
      - extract_email_body(payload)
      - html_to_text(html)
      - parse_payment_info(text)   -> dict with at least amount/name_paid/name_received
                                     (and optionally: email, shipping_address)

    Returns:
        List[dict] where each dict has:
          - source: str
          - subject: str
          - parsed: dict  (parse_payment_info output + email_date)
          - email_timestamp: datetime | None
          - message_id: str
          - thread_id: str | None
    """
    service = authenticate_gmail()

    # ---- Sender/recipient queries ----
    queries = {
        "Venmo":     "from:venmo@venmo.com",
        "PayPal":    "from:service@paypal.com",
        "Zelle":     "from:no.reply.alerts@chase.com",

        # added queries
        "BoA":       "from:onlinebanking@ealerts.bankofamerica.com",
    }

    # ---- Helpers ----
    def _get_header(headers_list, header_name, default=None):
        for h in headers_list or []:
            if h.get("name") == header_name:
                return h.get("value", default)
        return default

    def _parse_email_date(date_str: Optional[str]) -> Optional[datetime]:
        """
        Parse common email 'Date' header formats into datetime.
        """
        if not date_str:
            return None

        ds = date_str.strip()
        # remove trailing "(PST)" style comments
        ds = re.sub(r"\s*\([^)]*\)\s*$", "", ds)
        ds = re.sub(r"\s+", " ", ds).strip()

        formats = [
            "%a, %d %b %Y %H:%M:%S %z",
            "%a, %d %b %Y %H:%M:%S",
            "%d %b %Y %H:%M:%S %z",
            "%d %b %Y %H:%M:%S",
            "%a, %d %b %Y",
            "%d %b %Y",
        ]
        for fmt in formats:
            try:
                return datetime.strptime(ds, fmt)
            except ValueError:
                continue
        return None

    def _within_range(ts: Optional[datetime]) -> bool:
        if ts is None:
            return False

        if start_date:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
            if ts.replace(tzinfo=None) < start_dt:
                return False
        if end_date:
            # inclusive end date (end of day)
            end_dt = datetime.strptime(end_date, "%Y-%m-%d")
            end_dt = end_dt.replace(hour=23, minute=59, second=59)
            if ts.replace(tzinfo=None) > end_dt:
                return False
        return True

    def _list_all_message_ids(gmail_query: str) -> List[Dict[str, str]]:
        """
        Returns list of {id, threadId} dicts for a query, handling pagination.
        """
        all_msgs = []
        page_token = None
        while True:
            resp = service.users().messages().list(
                userId="me",
                q=gmail_query,
                maxResults=max_results_per_query,
                pageToken=page_token
            ).execute()

            batch = resp.get("messages", []) or []
            all_msgs.extend(batch)

            page_token = resp.get("nextPageToken")
            if not page_token:
                break

        # de-dup by message id
        seen = set()
        dedup = []
        for m in all_msgs:
            mid = m.get("id")
            if mid and mid not in seen:
                seen.add(mid)
                dedup.append(m)
        return dedup

    # ---- Fetch + parse ----
    combined: List[Dict[str, Any]] = []

    for source, q in queries.items():
        msgs = _list_all_message_ids(q)
        print(f"🔍 {source}: found {len(msgs)} messages for query: {q}")

        for i, m in enumerate(msgs, start=1):
            msg_id = m.get("id")
            thread_id = m.get("threadId")

            if i == 1 or i % 25 == 0:
                print(f"   {source}: processing {i}/{len(msgs)} (msg_id={msg_id})")

            msg = service.users().messages().get(
                userId="me",
                id=msg_id,
                format="full"
            ).execute()

            payload = msg.get("payload") or {}
            headers = payload.get("headers") or []

            subject = _get_header(headers, "Subject", "No Subject")
            date_str = _get_header(headers, "Date", None)

            email_timestamp = _parse_email_date(date_str)
            if start_date or end_date:
                if not _within_range(email_timestamp):
                    continue

            body = extract_email_body(payload)
            if not body:
                continue

            cleaned = html_to_text(body)
            info = parse_payment_info(cleaned) or {}

            info["email_date"] = (
                email_timestamp.strftime("%Y-%m-%d %H:%M:%S") if email_timestamp else None
            )

            if (info.get("name_paid") or info.get("name_received")) and info.get("amount") and info.get("email_date"):
                combined.append({
                    "source": source,
                    "subject": subject,
                    "parsed": info,
                    "email_timestamp": email_timestamp,
                    "message_id": msg_id,
                    "thread_id": thread_id,
                })

    combined.sort(key=lambda x: x["email_timestamp"] or datetime.min)

    print(f"✅ Total valid payment emails (all sources): {len(combined)}")
    return combined
