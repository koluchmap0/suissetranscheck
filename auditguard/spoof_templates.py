"""
BUST GO — Spoof Templates.
Pre-built phishing templates for spoof testing.
Each template includes: brand, from_name, from_email, subject, html, reply_to.
"""
from __future__ import annotations
from pathlib import Path

def _load_html(filename: str) -> str:
    """Load HTML template from file next to this module."""
    p = Path(__file__).parent / filename
    if p.exists():
        return p.read_text(encoding="utf-8")
    return ""

SPOOF_TEMPLATES: dict[str, dict] = {
    "amazon": {
        "brand": "Amazon",
        "from_name": "Amazon.com",
        "from_email": "noreply@amazon.com",
        "subject": "Action Required: Verify Your Account Information",
        "reply_to": "noreply@amazon.com",
        "html": """<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#eaeded;font-family:Arial,Helvetica,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#eaeded;padding:20px 0">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:4px">
<tr><td style="background:#232f3e;padding:16px 30px;border-radius:4px 4px 0 0">
<img src="https://ci3.googleusercontent.com/meips/ADKq_NbV5WOY7JYCni1B5t39RDrXAsepnMGzOMqKt-G8-sLcwYhXiT2E8VLiMVg6lkME5u1r0xCCuY5VWKP5uWGccJ9h39D-lKEv8N1Lsm3HBVH5MjjP2ZYrOCXpwpV_lF63G1=s0-d-e1-ft#https://m.media-amazon.com/images/G/01/x-locale/cs/te/logo._CB485949097_.png" alt="Amazon" style="height:31px">
</td></tr>
<tr><td style="padding:30px">
<h2 style="color:#111;margin:0 0 15px;font-size:20px">Account Security Alert</h2>
<p style="color:#333;font-size:14px;line-height:1.6;margin:0 0 15px">
Dear Customer,</p>
<p style="color:#333;font-size:14px;line-height:1.6;margin:0 0 15px">
We detected unusual activity on your Amazon account. For your security, we've temporarily limited access to certain account features.</p>
<p style="color:#333;font-size:14px;line-height:1.6;margin:0 0 20px">
Please verify your account information within 24 hours to restore full access.</p>
<table cellpadding="0" cellspacing="0" width="100%"><tr><td align="center">
<a href="#" style="display:inline-block;background:#ff9900;color:#111;text-decoration:none;padding:12px 35px;border-radius:3px;font-size:14px;font-weight:bold">Verify My Account</a>
</td></tr></table>
<p style="color:#333;font-size:14px;line-height:1.6;margin:20px 0 0">
If you did not request this, please secure your account immediately by changing your password.</p>
<p style="color:#999;font-size:12px;margin:20px 0 0;border-top:1px solid #eee;padding-top:15px">
This message was sent from a notification-only address. Please do not reply to this email.<br>
&copy; 2026 Amazon.com, Inc. All rights reserved. Amazon, Amazon.com and the Amazon.com logo are trademarks of Amazon.com, Inc.</p>
</td></tr></table>
</td></tr></table>
</body></html>""",
    },

    "swisspost": {
        "brand": "Swiss Post",
        "from_name": "Post CH SA",
        "from_email": "notifications@post.ch",
        "subject": "Votre envoi est en cours de dédouanement",
        "reply_to": "notifications@post.ch",
        "html": _load_html("swisspost_letter.html"),
    },

    "binance": {
        "brand": "Binance",
        "from_name": "Binance",
        "from_email": "do-not-reply@binance.com",
        "subject": "[Binance] Security Alert: Unauthorized Login Attempt Detected",
        "reply_to": "do-not-reply@binance.com",
        "html": """<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#12161c;font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;color:#eaecef">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#12161c;padding:20px 0">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="background:#1e2329;border-radius:8px">
<tr><td style="padding:25px 30px;border-bottom:1px solid #2b3139">
<table width="100%"><tr>
<td><span style="font-size:22px;font-weight:bold;color:#f0b90b">Binance</span></td>
<td align="right"><span style="font-size:12px;color:#848e9c">Security Notification</span></td>
</tr></table>
</td></tr>
<tr><td style="padding:30px">
<div style="background:#2b1a1a;border:1px solid #f6465d;border-radius:6px;padding:15px;margin:0 0 20px">
<p style="margin:0;color:#f6465d;font-size:14px;font-weight:bold">&#9888; Unauthorized Login Attempt</p>
</div>
<p style="color:#eaecef;font-size:14px;line-height:1.7;margin:0 0 15px">
Dear Binance User,</p>
<p style="color:#eaecef;font-size:14px;line-height:1.7;margin:0 0 15px">
We detected a login attempt to your account from an unrecognized device.</p>
<table style="width:100%;background:#2b3139;border-radius:6px;margin:0 0 20px">
<tr><td style="padding:12px 15px;border-bottom:1px solid #3c4043">
<span style="color:#848e9c;font-size:12px">IP Address</span><br>
<span style="color:#eaecef;font-size:14px">185.220.101.34</span>
</td></tr>
<tr><td style="padding:12px 15px;border-bottom:1px solid #3c4043">
<span style="color:#848e9c;font-size:12px">Location</span><br>
<span style="color:#eaecef;font-size:14px">Moscow, Russia</span>
</td></tr>
<tr><td style="padding:12px 15px">
<span style="color:#848e9c;font-size:12px">Time (UTC)</span><br>
<span style="color:#eaecef;font-size:14px">2026-10-02 06:42:17</span>
</td></tr></table>
<p style="color:#eaecef;font-size:14px;line-height:1.7;margin:0 0 20px">
If this was not you, your account may be compromised. Please secure your account immediately.</p>
<table cellpadding="0" cellspacing="0" width="100%"><tr><td align="center">
<a href="#" style="display:inline-block;background:#f0b90b;color:#12161c;text-decoration:none;padding:14px 40px;border-radius:4px;font-size:14px;font-weight:bold">Secure My Account</a>
</td></tr></table>
<p style="color:#848e9c;font-size:11px;margin:25px 0 0;border-top:1px solid #2b3139;padding-top:15px">
This is an automated security notification. Do not share this email.<br>
&copy; 2026 Binance. All rights reserved.</p>
</td></tr></table>
</td></tr></table>
</body></html>""",
    },

    "okx": {
        "brand": "OKX",
        "from_name": "OKX",
        "from_email": "noreply@okx.com",
        "subject": "[OKX] Withdrawal Request — Confirm or Cancel",
        "reply_to": "noreply@okx.com",
        "html": """<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#0b0e11;font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;color:#e6e8ea">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#0b0e11;padding:20px 0">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="background:#16191e;border-radius:8px">
<tr><td style="padding:25px 30px;border-bottom:1px solid #252930">
<table width="100%"><tr>
<td><span style="font-size:24px;font-weight:bold;color:#fff">OKX</span></td>
<td align="right"><span style="font-size:12px;color:#8a939d">Withdrawal Alert</span></td>
</tr></table>
</td></tr>
<tr><td style="padding:30px">
<h2 style="color:#fff;margin:0 0 20px;font-size:18px">Withdrawal Confirmation Required</h2>
<p style="color:#e6e8ea;font-size:14px;line-height:1.7;margin:0 0 15px">
A withdrawal request has been submitted from your OKX account:</p>
<table style="width:100%;background:#1e2329;border-radius:8px;margin:0 0 20px">
<tr><td style="padding:15px;border-bottom:1px solid #252930">
<span style="color:#8a939d;font-size:12px">Asset</span><br>
<span style="color:#fff;font-size:18px;font-weight:bold">2.458 BTC</span>
<span style="color:#8a939d;font-size:13px"> (~$152,403.24 USD)</span>
</td></tr>
<tr><td style="padding:15px;border-bottom:1px solid #252930">
<span style="color:#8a939d;font-size:12px">Destination Address</span><br>
<span style="color:#e6e8ea;font-size:13px;font-family:monospace">bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh</span>
</td></tr>
<tr><td style="padding:15px;border-bottom:1px solid #252930">
<span style="color:#8a939d;font-size:12px">Network</span><br>
<span style="color:#e6e8ea;font-size:14px">Bitcoin (BTC)</span>
</td></tr>
<tr><td style="padding:15px">
<span style="color:#8a939d;font-size:12px">Time (UTC)</span><br>
<span style="color:#e6e8ea;font-size:14px">2026-10-02 07:15:33</span>
</td></tr></table>
<div style="background:#2a1a0e;border:1px solid #f0a000;border-radius:6px;padding:12px;margin:0 0 20px">
<p style="margin:0;color:#f0a000;font-size:13px">&#9888; If you did not initiate this request, cancel immediately and change your password.</p>
</div>
<table cellpadding="0" cellspacing="0" width="100%"><tr>
<td width="48%" align="center">
<a href="#" style="display:inline-block;background:#00c076;color:#fff;text-decoration:none;padding:14px 30px;border-radius:4px;font-size:14px;font-weight:bold;width:80%;text-align:center">Confirm</a>
</td>
<td width="4%"></td>
<td width="48%" align="center">
<a href="#" style="display:inline-block;background:#f6465d;color:#fff;text-decoration:none;padding:14px 30px;border-radius:4px;font-size:14px;font-weight:bold;width:80%;text-align:center">Cancel</a>
</td>
</tr></table>
<p style="color:#8a939d;font-size:11px;margin:25px 0 0;border-top:1px solid #252930;padding-top:15px">
This is an automated notification from OKX. Do not reply.<br>
&copy; 2026 OKX. All rights reserved.</p>
</td></tr></table>
</td></tr></table>
</body></html>""",
    },

}


def get_template(name: str) -> dict | None:
    return SPOOF_TEMPLATES.get(name.lower())


def list_templates() -> list[str]:
    return list(SPOOF_TEMPLATES.keys())
