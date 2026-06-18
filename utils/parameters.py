"""
Helpers para parámetros estandarizados de QgsProcessingAlgorithm.

Los 19 algoritmos GEE del plugin repiten casi los mismos parámetros:
  - INPUT_LAYER (capa vectorial opcional)
  - INPUT_EXTENT (recuadro opcional)
  - EPSG (CRS de destino)
  - EXPORT_METHOD (enum Local/Drive/GCS)
  - OUT_FOLDER (carpeta local)
  - GCS_BUCKET (bucket GCS)

Este módulo expone:
  add_aoi_params(algorithm)        # añade INPUT_LAYER + INPUT_EXTENT
  add_export_params(algorithm)     # añade EXPORT_METHOD + OUT_FOLDER + GCS_BUCKET
  add_crs_param(algorithm, default='EPSG:32718')  # añade EPSG
  STANDARD_PARAM_NAMES             # diccionario de nombres canónicos

Convención: usar los nombres canónicos (claves) para que `build_aoi()`
de `utils/aoi_builder` funcione sin pasar nombres custom.

Las funciones son aditivas: cada algoritmo añade SUS propios parámetros
específicos (year, product, etc.) además de los estándar.
"""

from qgis.core import (
    QgsProcessing,
    QgsProcessingParameterCrs,
    QgsProcessingParameterEnum,
    QgsProcessingParameterExtent,
    QgsProcessingParameterFolderDestination,
    QgsProcessingParameterMapLayer,
    QgsProcessingParameterString,
)

from .export_router import EXPORT_METHOD_LABELS


# Nombres canónicos — coinciden con los que ya usan los algoritmos.
STANDARD_PARAM_NAMES = {
    'input_layer':   'INPUT_LAYER',
    'input_extent':  'INPUT_EXTENT',
    'epsg':          'EPSG',
    'export_method': 'EXPORT_METHOD',
    'out_folder':    'OUT_FOLDER',
    'gcs_bucket':    'GCS_BUCKET',
}


def add_aoi_params(algorithm,
                   aoi_label='Área de interés (Capa Vectorial)',
                   extent_label='O dibujar un Recuadro'):
    """
    Añade los dos parámetros de AOI estándar al algoritmo.
    Ambos son opcionales; `build_aoi()` valida que al menos uno tenga valor.
    """
    algorithm.addParameter(
        QgsProcessingParameterMapLayer(
            STANDARD_PARAM_NAMES['input_layer'],
            algorithm.tr(aoi_label),
            [QgsProcessing.TypeVectorPolygon],
            optional=True,
        )
    )
    algorithm.addParameter(
        QgsProcessingParameterExtent(
            STANDARD_PARAM_NAMES['input_extent'],
            algorithm.tr(extent_label),
            optional=True,
        )
    )


def add_crs_param(algorithm, default='EPSG:32718',
                  label='CRS de Destino para la salida'):
    """
    Añade el parámetro de CRS de destino con default razonable
    (UTM 18S — Perú). El usuario puede sobrescribirlo.
    """
    algorithm.addParameter(
        QgsProcessingParameterCrs(
            STANDARD_PARAM_NAMES['epsg'],
            algorithm.tr(label),
            defaultValue=default,
        )
    )


def add_export_params(algorithm, out_folder_optional=False,
                      gcs_bucket_label='Nombre del Bucket GCS (sólo para GCS)'):
    """
    Añade los 3 parámetros estándar de exportación:
      - EXPORT_METHOD: enum Local/Drive/GCS
      - OUT_FOLDER: carpeta local
      - GCS_BUCKET: bucket GCS (opcional)
    """
    algorithm.addParameter(
        QgsProcessingParameterEnum(
            STANDARD_PARAM_NAMES['export_method'],
            algorithm.tr('Método de Exportación'),
            options=EXPORT_METHOD_LABELS,
            defaultValue=0,
        )
    )
    algorithm.addParameter(
        QgsProcessingParameterFolderDestination(
            STANDARD_PARAM_NAMES['out_folder'],
            algorithm.tr('Carpeta Local de Destino'),
            optional=out_folder_optional,
        )
    )
    algorithm.addParameter(
        QgsProcessingParameterString(
            STANDARD_PARAM_NAMES['gcs_bucket'],
            algorithm.tr(gcs_bucket_label),
            optional=True,
        )
    )


def read_export_params(algorithm, parameters, context):
    """
    Lee los 3 parámetros de exportación y devuelve un dict normalizado:
        {'method': int, 'out_folder': str, 'gcs_bucket': str}

    Conviene usarlo en `processAlgorithm` justo después de leer
    los parámetros específicos del algoritmo.
    """
    return {
        'method':     algorithm.parameterAsEnum(parameters, STANDARD_PARAM_NAMES['export_method'], context),
        'out_folder': algorithm.parameterAsString(parameters, STANDARD_PARAM_NAMES['out_folder'], context),
        'gcs_bucket': algorithm.parameterAsString(parameters, STANDARD_PARAM_NAMES['gcs_bucket'], context),
    }


def read_crs_param(algorithm, parameters, context, fallback='EPSG:4326'):
    """
    Lee el CRS de destino. Si no es válido, devuelve `fallback` como string.
    """
    crs = algorithm.parameterAsCrs(parameters, STANDARD_PARAM_NAMES['epsg'], context)
    if crs.isValid():
        return crs.authid()
    return fallback
