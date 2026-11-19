# استقرار NEXO-C

## پارس‌پک / Gunicorn

- `Procfile` از قبل تنظیم شده است.
- پورت را از `PORT` بخوانید؛ پیش‌فرض کد `8000` است.
- `SECRET_KEY` را حتماً در محیط تولید تنظیم کنید.
- برای داده پایدار: `NEXO_DATA_DIR` را به دیسک ماندگار اشاره دهید (پوشه‌های `nexo.db`، `uploads`، `backups`).

```bash
pip install -r requirements.txt
export PORT=8000
export SECRET_KEY=$(python -c 'import secrets;print(secrets.token_hex(32))')
gunicorn --worker-class gthread --workers 1 --threads 100 --timeout 120 --bind 0.0.0.0:$PORT app:sio
```

## مالک

مالک اصلی `@arad_kr` در کد ثابت است. پس از اولین ثبت‌نام یا ورود با این نام کاربری، نقش `owner` اعمال می‌شود.

## بررسی سلامت

پس از بالا آمدن سرویس:

```bash
curl -s http://127.0.0.1:8000/manifest.json
curl -s http://127.0.0.1:8000/api/rules
```
