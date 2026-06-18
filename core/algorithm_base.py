"""
Clase base opcional para algoritmos GEE de GeoForest Tools.

`GeoForestGEEAlgorithmBase` encapsula los patrones que aparecen en
TODOS los algoritmos GEE del plugin:
  - Parámetros estándar (AOI, CRS, método de export)
  - Inicialización de Earth Engine
  - Construcción del AOI y conversión a ee.Geometry
  - Enrutamiento de exportación

Está pensada para las herramientas FUTURAS. Los 19 algoritmos
existentes NO heredan de esta clase para no romper nada — siguen
usando los helpers (`build_aoi`, `ensure_gee_initialized`) por
composición, que es lo que ya tienen.

Para escribir una herramienta nueva con esta base, basta con:

    from ..core.algorithm_base import GeoForestGEEAlgorithmBase
    from ..utils.export_router import export_image

    class MiNuevoAlgoritmo(GeoForestGEEAlgorithmBase):
        def name(self): return 'mi_algoritmo'
        def displayName(self): return 'Mi Algoritmo'
        def group(self): return 'Biomasa'
        def groupId(self): return 'biomasa'

        def initAlgorithm(self, config=None):
            self.add_standard_parameters()
            # ... parámetros propios del algoritmo

        def processAlgorithm(self, parameters, context, feedback):
            aoi = self.build_aoi_from_params(parameters, context, feedback)
            ee_geom = aoi.to_ee_geometry(self.ee)
            export = self.read_export_params(parameters, context)
            # ... lógica propia ...
            export_image(method=export['method'], image=img,
                         description='out', region=ee_geom,
                         drive_folder='GEE_MiAlgo',
                         gcs_bucket=export['gcs_bucket'],
                         feedback=feedback)
            return {}
        def createInstance(self): return MiNuevoAlgoritmo()

En menos de 30 líneas tenés una herramienta completa que comparte
toda la infraestructura del plugin.
"""

from qgis.core import QgsProcessingAlgorithm

from ..utils.aoi_builder import build_aoi
from ..utils.gee_init import ensure_gee_initialized
from ..utils.parameters import (
    add_aoi_params,
    add_crs_param,
    add_export_params,
    read_crs_param,
    read_export_params,
)


class GeoForestGEEAlgorithmBase(QgsProcessingAlgorithm):
    """
    Base opcional para algoritmos que consumen Earth Engine.

    Hereda toda la mecánica de Processing y añade:
      - `add_standard_parameters()` para `initAlgorithm`
      - `build_aoi_from_params()` y `init_gee()` para `processAlgorithm`
      - `read_export_params()`, `read_crs_param()` para leer parámetros
      - propiedad `ee` que importa el módulo Earth Engine bajo demanda

    Los algoritmos existentes NO se migran automáticamente — esta clase
    es para herramientas nuevas o para refactor explícito futuro.
    """

    # Defaults razonables — el subclase los puede sobrescribir
    DEFAULT_CRS = 'EPSG:32718'  # UTM 18S, Perú
    GROUP = 'GeoForest Tools'
    GROUP_ID = 'geoforesttools'

    # -- Helpers para subclases ---------------------------------------------

    @property
    def ee(self):
        """Import diferido de Earth Engine. Falla con mensaje claro si
        la librería `ee` no está instalada."""
        try:
            import ee
            return ee
        except ImportError as exc:
            raise ImportError(
                "La librería 'earthengine-api' no está instalada. "
                "Ve a 'GeoForest Tools → Instalar Dependencias'."
            ) from exc

    # -- Construcción de parámetros estándar --------------------------------

    def add_standard_parameters(self, with_crs=True, with_export=True,
                                crs_default=None):
        """
        Añade los parámetros AOI + CRS + Export al algoritmo. Llamar
        desde `initAlgorithm` antes de añadir los parámetros propios.
        """
        add_aoi_params(self)
        if with_crs:
            add_crs_param(self, default=crs_default or self.DEFAULT_CRS)
        if with_export:
            add_export_params(self)

    # -- Helpers para processAlgorithm --------------------------------------

    def init_gee(self, feedback=None):
        """Asegura que Earth Engine esté inicializado. Idempotente."""
        ensure_gee_initialized(feedback=feedback)

    def build_aoi_from_params(self, parameters, context, feedback):
        """Construye el AOI estándar; devuelve un `AOIResult`."""
        return build_aoi(self, parameters, context, feedback)

    def read_export_params(self, parameters, context):
        """Lee EXPORT_METHOD, OUT_FOLDER, GCS_BUCKET y devuelve dict."""
        return read_export_params(self, parameters, context)

    def read_crs(self, parameters, context, fallback='EPSG:4326'):
        """Lee el CRS de destino como string EPSG:XXXX."""
        return read_crs_param(self, parameters, context, fallback=fallback)

    # -- Convenciones Processing --------------------------------------------

    def tr(self, string):
        from qgis.PyQt.QtCore import QCoreApplication
        return QCoreApplication.translate(self.__class__.__name__, string)

    def group(self):
        return self.GROUP

    def groupId(self):
        return self.GROUP_ID

    # createInstance, name, displayName son responsabilidad del subclase
