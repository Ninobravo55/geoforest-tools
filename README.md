# GeoForest Tools

GeoForest Tools es un plugin para QGIS diseñado para facilitar el análisis espacial y monitoreo forestal mediante la integración directa con la API de Google Earth Engine (GEE). Permite extraer datos oficiales de GEOBOSQUES (Perú) y realizar procesamientos pesados en la nube sin requerir gran capacidad de cómputo local.

## Características Principales
- **Conexión GEE Integrada:** Herramienta visual de autenticación y manejo automático de sesión para Google Earth Engine.
- **Análisis de Bosque y No Bosque:** Descarga de cobertura boscosa actualizada al año 2024.
- **Monitoreo de Pérdida de Bosque:** Cuantificación anual de pérdida boscosa histórica desde 2001 hasta 2024 con estadísticas y gráficos generados automáticamente.
- **Alertas Tempranas de Deforestación:** Extracción de alertas tempranas de deforestación de GEOBOSQUES para los años 2025 y 2026.
- **Generación Automática de Reportes:** Creación automática de archivos Excel/CSV, gráficos de barras y torta mediante `matplotlib`, y vectorización en Shapefile (`.shp`).
- **Renderizado Nativo en QGIS:** Aplicación automática de simbología y estilos nativos de QGIS (.qml).

## Requisitos Previos
1. **QGIS 3.x**
2. **Cuenta de Google Earth Engine:** Necesitas una cuenta registrada y un ID de proyecto de Google Cloud (Project ID) válido.
3. Librería `earthengine-api` instalada en tu entorno de Python de QGIS. Puedes instalarla usando la herramienta incluida en este plugin: *Instalar Dependencias GEE*.
4. Librerías adicionales requeridas: `geopandas`, `matplotlib`, `pandas`.

## Instalación
1. Descarga el repositorio o carpeta del plugin.
2. Cópialo en tu directorio de plugins de QGIS:
   - Windows: `C:\Users\TU_USUARIO\AppData\Roaming\QGIS\QGIS3\profiles\default\python\plugins\GeoForestTools`
3. Reinicia QGIS y activa el plugin en el **Administrador de Complementos**.

## Uso Básico
1. Ve al menú **GeoForest Tools > Autenticación GEE**. Introduce tu correo y tu **ID de Proyecto**. Si es la primera vez, se abrirá tu navegador para generar el token.
2. Abre la herramienta en **GeoForest Tools > Análisis GEOBOSQUES**.
3. Selecciona una capa vectorial de tu panel o dibuja un polígono de interés.
4. Elige el producto (ej. Alerta Temprana 2026).
5. (Opcional) Define un Buffer.
6. Selecciona una carpeta de salida y haz clic en **Ejecutar**.

## Licencia
Este proyecto se distribuye bajo la Licencia MIT. Consulta el archivo `LICENSE` para más detalles.
