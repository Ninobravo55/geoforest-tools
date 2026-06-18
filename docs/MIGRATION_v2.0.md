# GeoForest Tools — v2.0 Consolidación · Notas de migración

Este documento describe **exactamente** qué cambió, qué está verificado y
qué queda pendiente. Léelo antes de publicar el plugin.

---

## 1. Qué se hizo (y cómo está verificado)

### 1.1 Base reutilizable `GeeRasterAlgorithm`
Nuevo: `core/base/gee_raster_algorithm.py` (223 líneas). Absorbe el flujo
común de los algoritmos raster: AOI → simplify → geometría EE → buffer →
init GEE → export Drive/GCS (vía `export_router`) → descarga local →
fallback automático por área grande → carga + simbología en QGIS.

La subclase implementa solo lo que varía, mediante hooks:
- `build_export_image(...)` → `RasterJob` (asset, máscaras, escala, folder).
- `apply_symbology(rlayer, job)` → rampa/renderer.
- `compute_statistics(...)` → opcional; default 5-números, override por algoritmo.
- `prepare_for_export(image)` → opcional (p.ej. Meta hace `.toByte()`).

### 1.2 Algoritmos migrados a la base
| Algoritmo | Antes | Después | Reducción |
|---|---|---|---|
| `glad_canopy` | 282 | 98 | −65% |
| `eth_canopy` | 300 | 99 | −67% |
| `meta_canopy` | 316 | 147 | −53% |
| `planet_canopy` | 278 | 90 | −67% |
| **Total 4** | **1176** | **434** | **−63%** |

**Verificación de equivalencia (sin QGIS):** un script compara, entre el
original y el refactor, todos los valores que definen el comportamiento:
assets (resolviendo concatenaciones), escalas (30 / 10 / 4.78 / param),
densificación de buffer (30 / 10), carpetas Drive, valores de cada
`ColorRampItem`, `setClassificationMin/Max`, paletas completas, umbrales
de clasificación de Meta, `toByte` y `divide`. **Todos coinciden.**

> Esto prueba que los datos de configuración se preservaron. NO sustituye
> una corrida real en QGIS contra GEE — ver §4.

### 1.3 Bug corregido (era crash en producción)
`core/glcluc_algorithm.py` usaba `QgsVectorLayer` sin importarlo →
`NameError` en el producto "Dinámica". **Corregido** (import añadido). El
smoke test `test_no_undefined_names` ahora **garantiza que no reaparezca**.

### 1.4 Estabilidad de matplotlib
Se añadió `import matplotlib; matplotlib.use("Agg")` antes del primer
`import matplotlib.pyplot` en los **12** módulos que generan gráficos.
Backend headless = thread-safe en el worker de Processing. Esto elimina la
causa de crashes intermitentes al graficar fuera del hilo principal.

### 1.5 Limpieza de código muerto
`pyflakes` pasó de **188 → ~10 hallazgos**, todos cosméticos
(variables locales sin uso pre-existentes). Se eliminaron 137 imports
muertos (incluido `shapely.wkt.loads` importado y nunca usado en 7
archivos) y las variables muertas de `geobosques`/`glcluc`.

### 1.6 Higiene de proyecto (nuevo)
- `requirements.txt` con rangos de versión fijados (mayor acotado).
- `ruff.toml` — gate de **corrección** (F + E9), no de estilo, con la
  razón documentada (evita romper el orden `import matplotlib` / `use`).
- `.pre-commit-config.yaml`.
- `tests/test_static.py` — 5 smoke tests que corren sin QGIS.
- `.github/workflows/ci.yml` — ruff + smoke tests en cada push/PR.
- `icon.png`: 1254×1254 / 1218 KB → 256×256 / 83 KB (−93%).

---

## 2. Estado del gate (reproducible)

```
python -c "import ast,glob;[ast.parse(open(f).read()) for f in glob.glob('**/*.py',recursive=True)]"  # sintaxis
python -m pyflakes $(find . -name '*.py')   # 0 undefined names
ruff check .                                 # All checks passed
python tests/test_static.py                  # 5/5
```

---

## 3. Lo que NO se hizo (y por qué) — próximos pasos

### 3.1 ~~Adopción de `export_router` en los ~12 algoritmos restantes~~ ✅ HECHO
**Completado.** Cero `ee.batch.Export` a mano en todo el plugin. Los 10
algoritmos restantes (`dynamic_world`, `geobosques`, `early_warning`,
`glcluc`, `gfw`, `firecci`, `gedi_l4a`, `gedi_l4b`, `gedi_l2b`,
`gedi_canopy`) ahora delegan en `export_image` / `export_table`.

Verificación (sin QGIS): un diff de parámetros confirmó que folder,
scale, fileFormat, maxPixels y fileNamePrefix se preservan en los 10
archivos. El smoke test `test_no_handrolled_export_anywhere` impide que
el patrón reaparezca.

Cambio puntual al wrapper: `export_image` ahora omite `crs` cuando se le
pasa `crs=None`, para preservar el caso de `gedi_l4b` (export de imagen
en proyección nativa). Los demás llamadores pasan `crs` explícito y no se
ven afectados.

Pendiente real aquí: en `gedi_l4b` y `geobosques` los mensajes de
`feedback` se unificaron levemente (se parametrizó "Drive/GCS" en una sola
línea). Comportamiento de export idéntico; solo cambia el texto de log.

### 3.2 i18n real (`.ts` / `.qm`)
El plugin ya llama `self.tr(...)` por todas partes, pero falta el ciclo
`pylupdate` → traducir → `lrelease`. Es trabajo de tooling, no de código,
y se deja como tarea aparte para no entregar `.qm` sin revisar.

### 3.3 Gestor de tareas GEE in-app
Es una **feature nueva**, no consolidación. Diseño propuesto: un
`QDockWidget` que liste los `ee.batch.Task` lanzados (id, estado,
progreso) leyendo `ee.batch.Task.list()`, con polling vía `QgsTask`, y un
botón "importar resultado" cuando el estado sea COMPLETED. No se incluye
código porque la GUI no es verificable sin QGIS en este entorno.

---

## 4. Antes de publicar: prueba de humo manual en QGIS

El gate estático no ejecuta GEE. Antes del release, correr en QGIS real
**al menos un producto de cada algoritmo migrado** con un AOI pequeño:
1. GLAD 2020 (raster + Excel + rampa Discrete).
2. ETH altura (rampa Interpolated desde paleta).
3. Meta 1m a escala 30 m (clasificación + Excel de áreas + `toByte`).
4. Planet NICFI (divide 2.5 + rampa de 9 ítems).
Confirmar: descarga el `.tif`, genera el Excel correcto, carga con la
simbología esperada, y el fallback a Drive funciona con un AOI enorme.

**Adicional para la adopción de `export_router`:** lanzar **una** exportación
a Google Drive (método 1) desde cada uno de los 10 algoritmos migrados
(`dynamic_world`, `geobosques`, `early_warning`, `glcluc`, `gfw`,
`firecci`, `gedi_l4a/l4b/l2b/canopy`) con un AOI pequeño y confirmar que
la tarea aparece en `code.earthengine.google.com/tasks` con la carpeta y
formato correctos. Esto valida el camino Drive/GCS de una sola vez.

---

## 5. Bug funcional surgido durante la limpieza (revisar aparte)

`core/mapbiomas_c3_algorithm.py` crea `vlayer = QgsVectorLayer(...)` y
**nunca lo añade al proyecto** (`F841: vlayer assigned but never used`).
No es un error de sintaxis y no se tocó para no cambiar comportamiento a
ciegas, pero parece que la capa resultante no se está cargando en QGIS.
Verificar si es intencional o un olvido.
