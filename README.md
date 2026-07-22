# MD Teknoloji TV Manager

Merkezi ekran, dijital signage ve TV oynatıcı yönetim platformu.

## İlk sürüm kapsamı

- FastAPI tabanlı yönetim sunucusu
- WebSocket ile gerçek zamanlı player bağlantısı
- Cihaz ve grup hedefleme
- Görsel, video, web sayfası ve acil duyuru yayını
- Kalıcı playlist ve zamanlama yönetimi
- Tarayıcı tabanlı TV player
- Yönetim paneli
- SQLAlchemy tabanlı kurumsal veri modeli
- JWT ve rol tabanlı yetkilendirme altyapısı

## Kurulum

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Yönetim paneli: `http://localhost:8000/admin`

Player örneği:

`http://localhost:8000/player?device_id=tv-1&name=Giris&group=Genel`

## Ortam değişkenleri

- `PIXMAP_DATABASE_URL`
- `PIXMAP_JWT_SECRET`
- `PIXMAP_JWT_ALGORITHM`
- `PIXMAP_ACCESS_TOKEN_MINUTES`
- `PIXMAP_REFRESH_TOKEN_DAYS`

Üretimde `PIXMAP_JWT_SECRET` mutlaka güçlü ve gizli bir değer olarak ayarlanmalıdır.
