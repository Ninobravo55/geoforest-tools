# GeoForest Tools

![Geomatica Ambiental](https://github.com/geomatica-ambiental/geoforest-tools/logo3x1m-01.jpg)

**Análisis y gestión forestal integrada con Google Earth Engine para QGIS**

[![QGIS Plugin](https://img.shields.io/badge/QGIS-3.28%2B%20%7C%204.0%2B-green)](https://plugins.qgis.org/plugins/geoforest-tools/)
[![Python](https://img.shields.io/badge/Python-3.9%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## 🌳 Descripción

**GeoForest Tools** es un plugin de QGIS que conecta directamente con **Google Earth Engine** para realizar procesamientos masivos de datos satelitales en la nube. Especializado en monitoreo de bosques, deforestación histórica, alertas tempranas y análisis hidrológico en América Latina.

### Casos de uso

- **Monitoreo de deforestación**: GEOBOSQUES (Perú), GFW (Global Forest Watch), GLCLUC
- **Alertas tempranas**: Detección de perturbaciones con GLAD-L, Sentinel-2, Sentinel-1
- **Altura forestal**: GEDI (NASA), GLAD, Meta, ETH, Planet NICFI
- **Biomasa aérea**: GEDI L2B, GEDI L4A/L4B
- **Análisis dinámico**: Dynamic World (cobertura anual)

---

## ⚡ Características principales

✅ **Procesamiento en la nube** — Sin descargas masivas locales  
✅ **Múltiples fuentes de datos** — 15+ algoritmos especializados  
✅ **Compatible dual** — QGIS 3.28 LTR y QGIS 4.0+  
✅ **Exportación flexible** — Google Drive, Google Cloud Storage, descargas locales  
✅ **Gráficos automáticos** — Reportes visuales integrados  
✅ **Autenticación segura** — OAuth2 con Google Earth Engine  

---

## 📦 Instalación

### Opción 1: Desde QGIS Plugin Manager (recomendado)

1. Abre QGIS 3.28+ o 4.0+
2. **Plugins → Manage and Install Plugins**
3. Busca: `GeoForest Tools`
4. Haz clic en **Install**
5. Reinicia QGIS

### Opción 2: Instalación manual

```bash
# En Linux/macOS:
cd ~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/

# En Windows:
cd %APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\

# Descargar y extraer
git clone https://github.com/geomatica-ambiental/geoforest-tools.git
cd geoforest-tools
pip install -r requirements.txt
```

---

## 🔧 Requisitos

### Sistema

- **QGIS**: 3.28 LTR o 4.0+
- **Python**: 3.9+
- **RAM**: 4 GB mínimo (8 GB recomendado para procesamiento)
- **Internet**: Conexión estable (procesamiento en nube)

### Dependencias Python

```bash
pip install -r requirements.txt
```

Incluye:
- `ee` (Google Earth Engine API)
- `geemap` (Utilidades de Earth Engine)
- `matplotlib` (Gráficos)
- `pandas` (Análisis de datos)
- `geopandas` (Geometrías)

### Autenticación

1. Crear cuenta en [Google Earth Engine](https://earthengine.google.com)
2. Aceptar términos de uso
3. El plugin maneja la autenticación automáticamente (OAuth2)

---

## 🚀 Uso rápido

### 1. Autenticar con Google Earth Engine

Al abrir QGIS por primera vez con GeoForest Tools:
- El plugin mostrará diálogo de autenticación
- Inicia sesión con tu cuenta Google
- Se abre navegador → autoriza acceso a Earth Engine

### 2. Seleccionar área de análisis

```
GeoForest Tools → Monitoreo GEOBOSQUES Perú
```

- Dibuja un polígono en QGIS o carga un shapefile
- Define fechas de análisis
- Selecciona formato de descarga

### 3. Procesar

- Haz clic en **Run**
- Espera (típicamente 30-120 segundos)
- Resultados se descargan automáticamente
- Se genera reporte con gráficos

---

## 📊 Algoritmos disponibles

### Monitoreo de deforestación

| Algoritmo | Fuente | Resolución | Cobertura |
|-----------|--------|-----------|-----------|
| GEOBOSQUES Perú | MINAM | 30m | Perú |
| Global Forest Watch | WRI | 30m | Global |
| GLCLUC 2020 | USGS | 30m | Global |
| Early Warning (GLAD) | UMD | 30m | Trópicos |

### Altura forestal

| Algoritmo | Satélite | Resolución | Años |
|-----------|----------|-----------|------|
| GLAD | Landsat | 30m | 2000-2020 |
| Meta | Sentinel-2 | 1m | 2018-2020 |
| ETH | Sentinel-2 | 10m | 2020 |
| GEDI L2A | NASA GEDI | 25m | 2019-presente |
| Planet NICFI | Planet | 5m | 2015-presente |

### Biomasa

| Algoritmo | Parámetro | Resolución | Años |
|-----------|-----------|-----------|------|
| GEDI L2B | Estructura vertical | 25m | 2019-presente |
| GEDI L4A | Densidad biomasa | 25m | 2020-presente |
| GEDI L4B | Biomasa regional | 1km | 2020-presente |
| ESA CCI | Biomasa aérea | 100m | 2010-2020 |

---

## ⚙️ Configuración avanzada

### Variables de entorno

```bash
# Especificar proyecto GEE
export EARTHENGINE_PROJECT=mi-proyecto-gee

# Modo debug
export GEOFOREST_DEBUG=1

# Ruta de caché personalizada
export GEOFOREST_CACHE=/ruta/al/cache
```

### Dependencias opcionales

Para exportación a formatos específicos:

```bash
# NetCDF (hidrología)
pip install netcdf4

# Cloud Optimized GeoTIFF
pip install rio-cogeo

# PostGIS
pip install psycopg2-binary
```

---

## 🐛 Troubleshooting

### Error: "Authentication failed"
- Verifica conexión a internet
- Intenta limpiar caché: `~/.config/earthengine/`
- Revoca permisos en Google Account y reinicia autenticación

### Error: "Task timed out"
- El área de análisis es muy grande
- Reduce el área o usa fecha más corta
- Intenta a diferente hora (servidores GEE tienen picos)

### Error: "Módulo no encontrado"
```bash
# Reinstalar dependencias
pip install --upgrade -r requirements.txt

# Verificar entorno QGIS
python -c "import ee; ee.Initialize()"
```

### Rendimiento lento
- Aumenta `TIMEOUT` en settings
- Reduce resolución de salida
- Procesa en zonas más pequeñas

---

## 📄 Licencia

Este proyecto está licenciado bajo MIT License. Ver [LICENSE](LICENSE) para detalles.

**Atribución requerida**: Mencioná a Geomatica Ambiental en trabajos derivados.

---

## 📧 Soporte

- **Email**: nino@geomatica.pe
- **Website**: https://www.geomatica.pe
- **Twitter**: [@GeomaticaAmbient](https://twitter.com/GeomaticaAmbient)
- **Issues técnicos**: [GitHub Issues](https://github.com/geomatica-ambiental/geoforest-tools/issues)

---

## 🙏 Reconocimientos

- **Google Earth Engine** — Infraestructura de procesamiento
- **QGIS** — Plataforma SIG de código abierto
- **NASA GEDI** — Datos de altura forestal
- **Global Forest Watch** — Datos de deforestación
- **Comunidad de QGIS** — Feedback y contribuciones

---

## 📊 Estado del proyecto

| Aspecto | Estado |
|--------|--------|
| Versión | 2.0 (Estable) |
| QGIS compatible | 3.28, 3.34, 4.0+ |
| Últimas actualizaciones | Junio 2026 |
| Cobertura de tests | 85% |
| Documentación | 95% |

---

**¿Te fue útil? Dale una ⭐ en [GitHub](https://github.com/geomatica-ambiental/geoforest-tools)**
