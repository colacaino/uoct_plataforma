# Plataforma UOCT

Aplicación Django para gestionar cruces, importar bitácoras, analizar archivos de rutas y generar reportes.

## Estado

Fase 1 en desarrollo:

- Login y logout.
- Usuario personalizado con rol.
- Admin supremo mediante Django Admin.
- Modelos base: proyectos, cruces, archivos y análisis.
- CRUD inicial de cruces.

Fase 2 en desarrollo:

- Importador de bitácora Excel.
- Detección de encabezados reales de `Bitácora Terrenos`.
- Vista previa con duplicados, errores y advertencias.
- Guardado de cruces válidos en base de datos.

Fase 3 en desarrollo:

- Comparador de archivos de rutas ANTES/DESPUÉS.
- Detección automática de columnas `timestamp`, `routeName`, `length`, `time`.
- Métricas por ruta: velocidad, tiempo, muestras, desviación y percentiles.
- Resumen de calidad de datos y clasificación global.

Fase 4 en desarrollo:

- Dashboard con gráficos y últimos análisis.
- Exportación de análisis a Excel y PDF.
- Enlaces públicos revocables para compartir reportes.

Fase 5 en desarrollo:

- Permisos por rol y compartición interna con usuarios.
- Configuración por variables de entorno.
- Límites de carga para archivos Excel.
- Comando de backup `backup_uoct`.

## Desarrollo local

```powershell
.\.venv\Scripts\python manage.py runserver
```

URL local:

```text
http://127.0.0.1:8000/
```

## Base de datos

Por defecto usa `db.sqlite3`, una base real local para desarrollo. Para subirla a la web y trabajar con varios usuarios, configura PostgreSQL mediante `DATABASE_URL` o las variables `POSTGRES_*` descritas en [DEPLOYMENT.md](DEPLOYMENT.md).

Verifica el entorno con:

```powershell
.\.venv\Scripts\python manage.py check_uoct_env
```
