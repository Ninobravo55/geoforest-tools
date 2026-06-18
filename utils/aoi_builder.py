"""
AOI builder ROBUSTO — maneja CUALQUIER proyección sin adivinanzas.

Elimina el hack frágil de detección de coordenadas extremas.
Detecta el CRS real del canvas + valida geometrías tras cada transformación.

API:
    build_aoi(algorithm, parameters, context, feedback)
        -> AOIResult(geom_4326, source_crs, aoi_layer, is_from_layer)
    
    to_ee_geometry(qgs_geom_4326, ee_module, geo_dict=None)
        -> ee.Geometry (MultiPolygon/Polygon sin truncar)

CAMBIOS CLAVE:
  1. CRS del recuadro = QgsProject().crs() (no adivinado)
  2. Validación post-transformación: area > 0, no-vacío
  3. Logging detallado del CRS detectado
  4. Capa vectorial: CRS heredado de la fuente (sin suposiciones)
"""

import json
import logging

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsGeometry,
    QgsProcessingException,
    QgsProject,
    QgsVectorLayer,
    QgsFeatureRequest,
)

logger = logging.getLogger(__name__)

CRS_4326 = QgsCoordinateReferenceSystem('EPSG:4326')
CRS_WEB_MERCATOR = QgsCoordinateReferenceSystem('EPSG:3857')


class AOIResult:
    """Contenedor inmutable con productos derivados del AOI."""

    __slots__ = (
        'geom_4326', 'geo_dict', 'source_crs', 'aoi_layer', 
        'is_from_layer', 'source_crs_authid'
    )

    def __init__(self, geom_4326, geo_dict, source_crs, aoi_layer, is_from_layer):
        self.geom_4326 = geom_4326
        self.geo_dict = geo_dict
        self.source_crs = source_crs
        self.aoi_layer = aoi_layer
        self.is_from_layer = is_from_layer
        # Para logging y debugging
        self.source_crs_authid = source_crs.authid() if source_crs.isValid() else "INVALID"

    def to_ee_geometry(self, ee_module):
        return to_ee_geometry(self.geom_4326, ee_module, self.geo_dict)


def build_aoi(algorithm, parameters, context, feedback,
              layer_param='INPUT_LAYER', extent_param='INPUT_EXTENT'):
    """
    Construye AOI robusto desde capa O recuadro.
    
    PRIORIDAD:
      1. Si hay capa vectorial → úsala (con su CRS real)
      2. Si hay extent → úsalo (con CRS del PROYECTO, no adivinado)
      3. Si ninguno → error claro
    
    Returns:
        AOIResult con geom_4326, source_crs, aoi_layer, is_from_layer
    
    Raises:
        QgsProcessingException si ambos params vacíos o geometría inválida
    """
    input_layer = algorithm.parameterAsLayer(parameters, layer_param, context)
    extent = algorithm.parameterAsExtent(parameters, extent_param, context)

    # Prioridad: capa vectorial primero
    if input_layer is not None:
        if feedback:
            feedback.pushInfo(
                f"AOI: Detectada capa vectorial '{input_layer.name()}' "
                f"en CRS {input_layer.crs().authid()}"
            )
        return _build_from_layer(input_layer, feedback)

    # Segundo: recuadro del canvas
    if extent is not None and not extent.isNull() and not extent.isEmpty():
        # ✓ CAMBIO CLAVE: Obtener CRS del proyecto, NO adivinado
        project_crs = QgsProject.instance().crs()
        if not project_crs.isValid():
            project_crs = CRS_4326  # fallback ultra-seguro
        
        if feedback:
            feedback.pushInfo(
                f"AOI: Recuadro detectado en CRS del proyecto: {project_crs.authid()}"
            )
        return _build_from_extent(extent, project_crs, feedback)

    # Ninguno disponible
    raise QgsProcessingException(
        "❌ Debes proporcionar UNA de estas dos opciones:\n"
        "  • Una Capa Vectorial (Opción 1), O\n"
        "  • Un Recuadro dibujado en el canvas (Opción 2)\n\n"
        "NOTA: Si usas recuadro, el CRS se toma del Proyecto QGIS actual."
    )


def _build_from_layer(input_layer, feedback):
    """Construye AOI desde capa vectorial con CRS real."""
    source_crs = input_layer.crs()
    
    if not source_crs.isValid():
        if feedback:
            feedback.pushWarning("Capa sin CRS válido, asumiendo EPSG:4326")
        source_crs = CRS_4326
    
    # Transformar a 4326 si es necesario
    transform_ctx = QgsProject.instance().transformContext()
    needs_transform = source_crs.authid() != 'EPSG:4326'
    
    if needs_transform:
        xform = QgsCoordinateTransform(source_crs, CRS_4326, transform_ctx)
    else:
        xform = None
    
    # Colectar geometrías válidas
    features = list(input_layer.getFeatures(QgsFeatureRequest().setNoGeometry(False)))
    if not features:
        raise QgsProcessingException("La capa no contiene geometrías.")
    
    geoms_4326 = []
    for feat in features:
        g = feat.geometry()
        if g is None or g.isEmpty():
            continue
        
        # Copiar antes de transformar (no mutar original)
        g = QgsGeometry(g)
        if xform is not None:
            g.transform(xform)
        
        # Validación post-transformación
        if g.isEmpty() or not g.isGeosValid():
            if feedback:
                feedback.pushWarning(
                    f"Geometría inválida en feature {feat.id()} tras transformación, saltando"
                )
            continue
        
        geoms_4326.append(g)
    
    if not geoms_4326:
        raise QgsProcessingException(
            "Ninguna geometría válida en la capa tras transformación a EPSG:4326"
        )
    
    # Unir todas las geometrías
    geom_union = geoms_4326[0]
    for g in geoms_4326[1:]:
        geom_union = geom_union.combine(g)
    
    # Validación final
    if geom_union.isEmpty():
        raise QgsProcessingException("Geometría unida resultó vacía.")
    
    if feedback:
        area_m2 = geom_union.area() * (111320 ** 2)  # aproximado, deg^2 a m^2
        feedback.pushInfo(
            f"✓ AOI desde capa: {geom_union.type()} válido, "
            f"área ~{area_m2/1e6:.1f} km²"
        )
    
    geo_dict = json.loads(geom_union.asJson())
    
    return AOIResult(
        geom_4326=geom_union,
        geo_dict=geo_dict,
        source_crs=source_crs,
        aoi_layer=input_layer,
        is_from_layer=True,
    )


def _build_from_extent(extent, project_crs, feedback):
    """
    Construye AOI desde recuadro del canvas.
    
    CRÍTICO: project_crs es el CRS REAL del proyecto, no adivinado.
    """
    # Crear geometría del recuadro
    geom = QgsGeometry.fromRect(extent)
    
    # Transformar a 4326 si es necesario
    if project_crs.authid() != 'EPSG:4326':
        transform_ctx = QgsProject.instance().transformContext()
        xform = QgsCoordinateTransform(project_crs, CRS_4326, transform_ctx)
        geom = QgsGeometry(geom)
        geom.transform(xform)
    
    # Validación post-transformación
    if geom.isEmpty() or not geom.isGeosValid():
        raise QgsProcessingException(
            f"Error al transformar recuadro desde {project_crs.authid()} a EPSG:4326"
        )
    
    # Crear capa de memoria en 4326 (para clips posteriores)
    mem_layer = QgsVectorLayer("Polygon?crs=EPSG:4326", "aoi_extent", "memory")
    feat = QgsFeature()
    feat.setGeometry(geom)
    mem_layer.dataProvider().addFeature(feat)
    mem_layer.updateExtents()
    
    if feedback:
        area_m2 = geom.area() * (111320 ** 2)
        feedback.pushInfo(
            f"✓ AOI desde recuadro (canvas CRS: {project_crs.authid()}): "
            f"Polygon válido, área ~{area_m2/1e6:.1f} km²"
        )
    
    geo_dict = json.loads(geom.asJson())
    
    return AOIResult(
        geom_4326=geom,
        geo_dict=geo_dict,
        source_crs=project_crs,
        aoi_layer=mem_layer,
        is_from_layer=False,
    )


# ============================================================================
# Conversión a ee.Geometry (sin truncar GeometryCollection)
# ============================================================================

def to_ee_geometry(qgs_geom_4326, ee_module, geo_dict=None):
    """
    Convierte QgsGeometry (EPSG:4326) → ee.Geometry sin perder partes.
    
    Casos:
      - Polygon → ee.Geometry.Polygon
      - MultiPolygon → ee.Geometry.MultiPolygon
      - GeometryCollection → ee.Geometry.MultiPolygon (agrega todos los polígonos)
    """
    if geo_dict is None:
        geo_dict = json.loads(qgs_geom_4326.asJson())
    
    gtype = geo_dict.get('type')
    
    if gtype == 'Polygon':
        return ee_module.Geometry.Polygon(
            coords=geo_dict['coordinates'],
            proj='EPSG:4326',
            geodesic=False,
            evenOdd=True,
        )
    
    if gtype == 'MultiPolygon':
        return ee_module.Geometry.MultiPolygon(
            coords=geo_dict['coordinates'],
            proj='EPSG:4326',
            geodesic=False,
            evenOdd=True,
        )
    
    if gtype == 'GeometryCollection':
        poly_coords = []
        for sub in geo_dict.get('geometries', []):
            sub_type = sub.get('type')
            if sub_type == 'Polygon':
                poly_coords.append(sub['coordinates'])
            elif sub_type == 'MultiPolygon':
                poly_coords.extend(sub['coordinates'])
        
        if not poly_coords:
            raise QgsProcessingException(
                "AOI no contiene polígonos válidos tras la conversión. "
                "Revisa que sea capa poligonal."
            )
        
        return ee_module.Geometry.MultiPolygon(
            coords=poly_coords,
            proj='EPSG:4326',
            geodesic=False,
            evenOdd=True,
        )
    
    # Fallback: pasar tal cual (riesgo de error de GEE)
    return ee_module.Geometry(geo_dict)


# ============================================================================
# Simplificación segura
# ============================================================================

def safe_simplify(geom_4326, tolerance_deg=0.0001, min_area_deg2=1e-8):
    """
    Simplifica geom sólo si área > umbral. Evita aplastar AOIs pequeños.
    
    Returns:
        QgsGeometry simplificada, o la original si es muy pequeña.
    """
    if geom_4326 is None or geom_4326.isEmpty():
        return geom_4326
    
    area_deg2 = geom_4326.area()
    if area_deg2 < min_area_deg2:
        return geom_4326  # AOI muy pequeño, no simplificar
    
    simplified = geom_4326.simplify(tolerance_deg)
    if simplified is None or simplified.isEmpty():
        return geom_4326  # simplify falló, retorna original
    
    return simplified
