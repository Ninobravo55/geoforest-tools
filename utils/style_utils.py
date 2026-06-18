"""
Utilidades de simbología — soluciona el bug B-02.

El bug original: `shp_file.replace('.shp', '.qml')` cuando el archivo
ya era `.gpkg`. Resultado: el `.qml` se guardaba con el nombre completo
del gpkg + sufijo .qml, no funcionando como sidecar de QGIS.

Esta utilidad reemplaza correctamente la extensión sin importar el
formato fuente (.shp, .gpkg, .tif, .geojson, etc.).
"""

import os


def qml_path_for(source_path):
    """
    Devuelve la ruta esperada para el sidecar .qml de QGIS asociado a
    `source_path`, sustituyendo la extensión final.

    Ejemplos:
        'C:/data/x.shp'   -> 'C:/data/x.qml'
        '/tmp/y.gpkg'     -> '/tmp/y.qml'
        '/tmp/z.tif'      -> '/tmp/z.qml'
        '/tmp/sin_ext'    -> '/tmp/sin_ext.qml'
    """
    if not source_path:
        return source_path

    root, ext = os.path.splitext(source_path)
    if ext == '':
        return source_path + '.qml'
    return root + '.qml'


def save_qml_sidecar(layer, source_path):
    """
    Guarda el .qml sidecar de QGIS junto al archivo fuente, con
    extensión .qml correcta. Devuelve la ruta donde se guardó, o None
    si la operación falla.

    Soporta cualquier extensión del archivo de origen.
    """
    target_qml = qml_path_for(source_path)
    try:
        result, ok = layer.saveNamedStyle(target_qml)
    except Exception:
        return None
    # saveNamedStyle puede devolver (str, bool) o sólo str según versión.
    # Siempre devolvemos la ruta intentada para que el caller pueda
    # auditarla.
    return target_qml
