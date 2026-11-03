# Degussa Email Checker

Email enumeration tool - degussa.com registration endpoint.

## CLI
```bash
python3 checker.py -e "email@test.com"
python3 checker.py -f emails.txt -t 5 -d 0.5
```

## Web Interface
```bash
pip install httpx[http2] fastapi uvicorn
python3 web.py
# -> http://localhost:8001
```
