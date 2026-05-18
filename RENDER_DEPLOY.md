# Deploy en Render

Esta app se despliega en Render como Web Service usando Docker.

## 1. Configuracion del servicio

- Runtime: Docker
- Instance type: Free
- Branch: main
- Auto deploy: Yes

El `Dockerfile` usa `docker/entrypoint.sh`, que ejecuta automaticamente:

```text
python manage.py migrate --noinput
python manage.py ensure_default_admin
python manage.py collectstatic --noinput
gunicorn config.wsgi:application
```

## 2. Variables de entorno

Configura estas variables en Render. No las subas a GitHub.

```text
DATABASE_URL=postgresql://postgres.kpyyclhvvwzntahesjkn:TU_PASSWORD_SUPABASE@aws-1-us-west-2.pooler.supabase.com:5432/postgres
DJANGO_SECRET_KEY=GENERAR_UNA_CLAVE_SEGURA
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=TU_APP.onrender.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://TU_APP.onrender.com
UOCT_MAX_UPLOAD_MB=50
DJANGO_SUPERUSER_USERNAME=admin
DJANGO_SUPERUSER_PASSWORD=GENERAR_UNA_CLAVE_SEGURA
DJANGO_SUPERUSER_EMAIL=admin@example.com
DJANGO_SUPERUSER_DISPLAY_NAME=Administrador
```

## 3. Despues del primer deploy

Cuando Render entregue el dominio real, ajusta:

```text
DJANGO_ALLOWED_HOSTS=TU_APP.onrender.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://TU_APP.onrender.com
```

Luego redeploy.

## 4. Login

Entra a:

```text
https://TU_APP.onrender.com/login/
```

Admin Django:

```text
https://TU_APP.onrender.com/admin/
```

Usuario:

```text
DJANGO_SUPERUSER_USERNAME
```

Password:

```text
DJANGO_SUPERUSER_PASSWORD
```

## 5. Nota sobre archivos

Render Free no tiene almacenamiento persistente para archivos subidos. La base de datos queda en Supabase, pero para archivos reales conviene conectar Supabase Storage/S3 en una siguiente fase.
