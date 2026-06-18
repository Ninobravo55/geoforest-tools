"""
Export router — wrappers unificados para `ee.batch.Export.*`.

Centraliza el patrón duplicado en ~30 instancias del código:

    if export_method_idx == 1:
        task = ee.batch.Export.image.toDrive(
            image=img, description=name, folder='GEE_Folder',
            scale=scale, region=geom, crs=crs, maxPixels=1e13,
        )
        task.start()
        feedback.pushInfo("Tarea iniciada en Drive...")
    elif export_method_idx == 2:
        task = ee.batch.Export.image.toCloudStorage(
            image=img, description=name, bucket=gcs_bucket,
            scale=scale, region=geom, crs=crs, maxPixels=1e13,
        )
        task.start()
        feedback.pushInfo("Tarea iniciada en GCS...")

Reemplazo:
    export_image(method=method, image=img, description=name,
                 region=geom, scale=scale, crs=crs,
                 drive_folder='GEE_Folder', gcs_bucket=gcs_bucket,
                 feedback=feedback)

Beneficios:
  - Una sola fuente de defaults (maxPixels, fileFormat, etc.).
  - Validación uniforme (ej: GCS sin bucket → error claro, no NPE).
  - Mensajes consistentes en `feedback`.
  - Punto único para añadir reintentos/caché/telemetría en el futuro.

NO depende de Earth Engine en import time — `ee` se importa diferido.
"""

from enum import IntEnum

from qgis.core import QgsProcessingException


# ---------------------------------------------------------------------------
# Enum de métodos de exportación  (concuerda con el orden de los enums de
# Processing en initAlgorithm: 0=Local, 1=Drive, 2=GCS)
# ---------------------------------------------------------------------------

class ExportMethod(IntEnum):
    LOCAL = 0
    DRIVE = 1
    GCS = 2


EXPORT_METHOD_LABELS = [
    'Descarga directa local',
    'Google Drive',
    'Google Cloud Storage (GCS)',
]


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_DRIVE_FOLDER = 'GEE_GeoForest'
DEFAULT_MAX_PIXELS = int(1e13)
DEFAULT_SCALE = 30  # metros — Hansen GFW y similares


# ---------------------------------------------------------------------------
# Validación
# ---------------------------------------------------------------------------

def validate_export_args(method, gcs_bucket=None, out_folder=None):
    """
    Valida que los argumentos correspondan al método elegido.
    Lanza QgsProcessingException con mensaje accionable.
    """
    method = ExportMethod(method)
    if method == ExportMethod.LOCAL and not out_folder:
        raise QgsProcessingException(
            "Método 'Descarga directa local' requiere una Carpeta Local de Destino."
        )
    if method == ExportMethod.GCS and not gcs_bucket:
        raise QgsProcessingException(
            "Método 'Google Cloud Storage' requiere especificar el nombre del Bucket GCS."
        )


# ---------------------------------------------------------------------------
# Exportación de imágenes
# ---------------------------------------------------------------------------

def export_image(method, image, description, region,
                 scale=DEFAULT_SCALE, crs='EPSG:4326',
                 drive_folder=DEFAULT_DRIVE_FOLDER, gcs_bucket=None,
                 max_pixels=DEFAULT_MAX_PIXELS, feedback=None,
                 file_format=None, **kwargs):
    """
    Lanza una exportación de imagen a Drive o GCS según `method`.

    Para method=LOCAL devuelve None (la descarga local NO se hace por
    `ee.batch.Export.*` sino con `getDownloadURL()`; ese caso lo maneja
    cada algoritmo porque depende del flujo post-descarga).

    Para Drive/GCS devuelve el `ee.batch.Task` ya iniciado, para que
    el algoritmo pueda hacer trackeo si lo desea.
    """
    import ee
    method = ExportMethod(method)

    if method == ExportMethod.LOCAL:
        return None  # local lo maneja el caller

    common = dict(
        image=image,
        description=description,
        region=region,
        scale=scale,
        maxPixels=max_pixels,
    )
    if crs is not None:
        common['crs'] = crs  # crs=None => proyección nativa de la imagen
    if file_format is not None:
        common['fileFormat'] = file_format
    common.update(kwargs)  # permite override puntual

    if method == ExportMethod.DRIVE:
        task = ee.batch.Export.image.toDrive(folder=drive_folder, **common)
        if feedback is not None:
            feedback.pushInfo(
                f"Tarea iniciada en Google Drive — carpeta '{drive_folder}', "
                f"descripción '{description}'."
            )
    elif method == ExportMethod.GCS:
        task = ee.batch.Export.image.toCloudStorage(
            bucket=gcs_bucket, **common
        )
        if feedback is not None:
            feedback.pushInfo(
                f"Tarea iniciada en GCS — bucket '{gcs_bucket}', "
                f"descripción '{description}'."
            )
    else:
        raise QgsProcessingException(f"Método de exportación no soportado: {method}")

    task.start()
    if feedback is not None:
        feedback.pushInfo(
            "Revisa el estado en: https://code.earthengine.google.com/tasks"
        )
    return task


# ---------------------------------------------------------------------------
# Exportación de tablas (FeatureCollection)
# ---------------------------------------------------------------------------

def export_table(method, collection, description, region=None,
                 file_format='SHP', drive_folder=DEFAULT_DRIVE_FOLDER,
                 gcs_bucket=None, feedback=None, **kwargs):
    """
    Lanza una exportación de FeatureCollection a Drive o GCS.

    Para method=LOCAL devuelve None (descarga vía getDownloadURL).
    """
    import ee
    method = ExportMethod(method)

    if method == ExportMethod.LOCAL:
        return None

    common = dict(
        collection=collection,
        description=description,
        fileFormat=file_format,
    )
    common.update(kwargs)

    if method == ExportMethod.DRIVE:
        task = ee.batch.Export.table.toDrive(folder=drive_folder, **common)
        if feedback is not None:
            feedback.pushInfo(
                f"Tarea (tabla) iniciada en Google Drive — carpeta "
                f"'{drive_folder}', descripción '{description}'."
            )
    elif method == ExportMethod.GCS:
        task = ee.batch.Export.table.toCloudStorage(
            bucket=gcs_bucket, **common
        )
        if feedback is not None:
            feedback.pushInfo(
                f"Tarea (tabla) iniciada en GCS — bucket '{gcs_bucket}', "
                f"descripción '{description}'."
            )
    else:
        raise QgsProcessingException(f"Método de exportación no soportado: {method}")

    task.start()
    if feedback is not None:
        feedback.pushInfo(
            "Revisa el estado en: https://code.earthengine.google.com/tasks"
        )
    return task
