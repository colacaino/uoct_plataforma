# Despliegue UOCT

## Variables de entorno mínimas

Usa `.env.example` como guía. En producción define:

```text
DJANGO_SECRET_KEY=...
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=tu-dominio.cl,www.tu-dominio.cl
UOCT_MAX_UPLOAD_MB=100
```

## Base de datos

La app funciona hoy con SQLite local (`db.sqlite3`). Esa sí es una base de datos real, pero es adecuada para desarrollo, pruebas y uso personal en un solo equipo. Para web real con varios usuarios, usa PostgreSQL.

Puedes configurar PostgreSQL de dos formas.

Con `DATABASE_URL`:

```text
DATABASE_URL=postgresql://uoct_user:password@localhost:5432/uoct
```

O con variables separadas:

```text
POSTGRES_DB=uoct
POSTGRES_USER=uoct_user
POSTGRES_PASSWORD=...
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
```

## Docker Compose con PostgreSQL

Para levantar app + PostgreSQL:

```powershell
docker compose up --build
```

La app quedará en:

```text
http://127.0.0.1:8000/
```

El contenedor `web` ejecuta automáticamente:

- `migrate`
- `collectstatic`
- `gunicorn`

## Comandos base

```powershell
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python manage.py migrate
.\.venv\Scripts\python manage.py collectstatic
.\.venv\Scripts\python manage.py createsuperuser
```

## Verificación de entorno

```powershell
.\.venv\Scripts\python manage.py check_uoct_env
```

Modo estricto para producción:

```powershell
.\.venv\Scripts\python manage.py check_uoct_env --strict
```

## Backup

```powershell
.\.venv\Scripts\python manage.py backup_uoct
```

El comando crea un ZIP en `backups/` con:

- `data.json` generado por `dumpdata`.
- Copia de `db.sqlite3` si se usa SQLite.
- Carpeta `media/` con archivos subidos.

## Permisos

- `superuser` o rol `Administrador`: ve y gestiona todo.
- `Usuario`: ve sus cruces/análisis y lo compartido internamente con él.
- Los enlaces públicos de reportes usan token, expiración y pueden revocarse.
