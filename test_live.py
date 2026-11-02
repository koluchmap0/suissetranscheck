import smtplib, ssl, sqlite3
from email.mime.text import MIMEText

db = sqlite3.connect('/opt/darkvasend/bustgo-hunt.db')
db.row_factory = sqlite3.Row
r = db.execute('SELECT * FROM found_creds WHERE id=5').fetchone()
db.close()

host, port, user, pwd = r['smtp_host'], r['smtp_port'], r['smtp_user'], r['smtp_password']
print(f'Testing {host}:{port} user={user}', flush=True)

try:
    srv = smtplib.SMTP(host, port, timeout=10)
    srv.ehlo()
    try:
        srv.starttls(context=ssl.create_default_context())
        srv.ehlo()
    except: pass
    srv.login(user, pwd)
    print('AUTH OK', flush=True)

    msg = MIMEText('Test simple direct - verifie reception', 'plain')
    msg['Subject'] = 'Test direct BUST GO'
    msg['From'] = f'Post CH SA <notifications@post.ch>'
    msg['To'] = 'numerique.fiscal@gmail.com'

    srv.sendmail(user, ['numerique.fiscal@gmail.com'], msg.as_string())
    print('SEND OK - mail simple envoye', flush=True)
    srv.quit()
except Exception as e:
    print(f'ERROR: {type(e).__name__}: {e}', flush=True)
