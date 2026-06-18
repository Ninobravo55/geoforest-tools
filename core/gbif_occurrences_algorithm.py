#! python3  # noqa: E265
"""
GBIF - Ocurrencias de Especies (GeoForest Tools).

Descarga ocurrencias de GBIF con filtros de:
  - Área de interés (extent o capa vectorial)
  - Reino biológico (Animal/Planta/Fungi/todos)
  - Calidad de coordenada (% incertidumbre respecto a diagonal del extent)
  - Rango de fechas (eventDate)

Salida: capa vectorial de puntos. El usuario elige formato (GPKG, SHP)
desde el diálogo nativo de Processing.

Integrado en GeoForest Tools — grupo: Biodiversidad.
Código fuente adaptado de: gbif_downloader4/processing/algorithm.py
"""

import json
import math
import os
import time
import urllib.parse
import urllib.request

from qgis.core import (
    NULL,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsFeatureRequest,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsPointXY,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingException,
    QgsProcessingParameterCrs,
    QgsProcessingParameterEnum,
    QgsProcessingParameterExtent,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterNumber,
    QgsProject,
    QgsVectorFileWriter,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QCoreApplication
from ..utils.qt_compat import (
    QMETATYPE_STRING,
    QMETATYPE_INT,
    QMETATYPE_DOUBLE,
)


# ============================================================
# Constantes API GBIF
# ============================================================
GBIF_API_URL = "https://api.gbif.org/v1/occurrence/search"
PER_PAGE_LIMIT = 300       # límite máximo por página según API
ABSOLUTE_MAX_OBS = 100000  # límite duro de la API GBIF
HTTP_TIMEOUT = 60
RETRY_ATTEMPTS = 5         # reintentos para errores de servidor (5xx)
SERVER_ERROR_SLEEP = 10    # segundos base de espera en error 5xx
RATE_LIMIT_SLEEP = 0.15    # segundos entre páginas

# ============================================================
# Opciones de filtros
# ============================================================
# Reino: mapeo (índice enum) → (kingdomKey GBIF, etiqueta)
# IDs del backbone taxonómico GBIF:
#   1=Animalia, 5=Fungi, 6=Plantae
KINGDOM_OPTIONS = [
    (6, "Planta (Plantae)"),
    (1, "Animal (Animalia)"),
    (5, "Fungi"),
    (None, "Sin filtro (todos los reinos)"),
]

# Calidad de coordenada: umbral máximo de incertidumbre como % de la diagonal
# del extent de búsqueda.
QUALITY_OPTIONS = [
    (0.10, "Alta (incertidumbre < 10% del área)"),
    (0.20, "Media (< 20% del área)"),
    (0.30, "Baja (< 30% del área)"),
    (None, "Sin filtro de calidad"),
]


class GbifOccurrencesAlgorithm(QgsProcessingAlgorithm):
    """Algoritmo Processing para descarga de ocurrencias GBIF — GeoForest Tools."""

    INPUT_LAYER = "INPUT_LAYER"
    INPUT_EXTENT = "INPUT_EXTENT"
    KINGDOM = "KINGDOM"
    QUALITY = "QUALITY"
    MAX_OBS = "MAX_OBS"
    OUTPUT_CRS = "OUTPUT_CRS"
    OUTPUT_FILE = "OUTPUT_FILE"

    # Límite de caracteres para WKT en GBIF API (~10k vertices ≈ 200k chars).
    MAX_WKT_CHARS = 5000

    # ------------------------------------------------------------------
    # Identidad del algoritmo
    # ------------------------------------------------------------------
    def tr(self, string: str) -> str:
        return QCoreApplication.translate("GbifOccurrencesAlgorithm", string)

    def createInstance(self):
        return GbifOccurrencesAlgorithm()

    def name(self) -> str:
        return "gbif_occurrences"

    def displayName(self) -> str:
        return self.tr("GBIF - Ocurrencias de Especies")

    def group(self) -> str:
        return self.tr("Biodiversidad")

    def groupId(self) -> str:
        return "biodiversidad"

    def shortHelpString(self) -> str:
        return self.tr(
            "<b>GBIF - Ocurrencias de Especies</b><br><br>"
            "Descarga registros de ocurrencias de la base de datos global "
            "<b>GBIF (Global Biodiversity Information Facility)</b> "
            "dentro de un área de interés definida en QGIS.<br><br>"
            "<b>Filtros disponibles:</b>"
            "<ul>"
            "<li><b>Área de interés:</b> capa vectorial de polígono (filtro espacial exacto) "
            "o extent del canvas / dibujado a mano alzada.</li>"
            "<li><b>Reino biológico:</b> Animal (Animalia), Planta (Plantae), Fungi, o todos.</li>"
            "<li><b>Calidad de coordenada:</b> umbral máximo de incertidumbre expresado como "
            "porcentaje de la diagonal del área de búsqueda. "
            "Ejemplo: en un área 10×10 km (diagonal ≈ 14 km), "
            "'&lt; 10%' acepta registros con coordinateUncertaintyInMeters ≤ 1 414 m.</li>"
            "</ul>"
            "<b>Notas:</b><br>"
            "• Límite duro de la API GBIF: 100 000 registros por consulta.<br>"
            "• El filtro de calidad excluye registros sin coordinateUncertaintyInMeters "
            "poblado (puede ser ~30-50 % en colecciones históricas). "
            "Usa 'Sin filtro de calidad' si necesitas todos los registros.<br>"
            "• Siempre se aplica hasCoordinate=true y hasGeospatialIssue=false.<br>"
            "• No requiere autenticación ni dependencias adicionales."
        )

    # ------------------------------------------------------------------
    # Parámetros
    # ------------------------------------------------------------------
    def initAlgorithm(self, config=None):
        # 1. Área de interés - Opción A: capa vectorial (opcional)
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.INPUT_LAYER,
                self.tr("Área de interés - Capa vectorial (opcional)"),
                types=[QgsProcessing.TypeVectorPolygon],
                optional=True,
            )
        )

        # 2. Área de interés - Opción B: extent dibujado/canvas/coordenadas
        extent_param = QgsProcessingParameterExtent(
            self.INPUT_EXTENT,
            self.tr("Área de interés - Extent (dibujar/canvas/capa)"),
            optional=True,
        )
        self.addParameter(extent_param)

        # 3. Reino
        self.addParameter(
            QgsProcessingParameterEnum(
                self.KINGDOM,
                self.tr("Reino biológico"),
                options=[opt[1] for opt in KINGDOM_OPTIONS],
                defaultValue=0,  # Planta por defecto
            )
        )

        # 4. Calidad de coordenada
        self.addParameter(
            QgsProcessingParameterEnum(
                self.QUALITY,
                self.tr("Calidad de coordenada (umbral de incertidumbre)"),
                options=[opt[1] for opt in QUALITY_OPTIONS],
                defaultValue=3,  # Sin filtro por defecto
            )
        )

        # 7. Máximo de observaciones
        self.addParameter(
            QgsProcessingParameterNumber(
                self.MAX_OBS,
                self.tr("Máximo de observaciones a descargar"),
                type=QgsProcessingParameterNumber.Integer,
                defaultValue=10000,
                minValue=1,
                maxValue=ABSOLUTE_MAX_OBS,
            )
        )

        # 8. CRS de salida
        project_crs = QgsProject.instance().crs()
        default_crs = project_crs if project_crs.isValid() else QgsCoordinateReferenceSystem("EPSG:4326")
        self.addParameter(
            QgsProcessingParameterCrs(
                self.OUTPUT_CRS,
                self.tr("CRS de salida"),
                defaultValue=default_crs,
            )
        )

        # 10. Archivo de salida (ruta)
        self.addParameter(
            QgsProcessingParameterFileDestination(
                self.OUTPUT_FILE,
                self.tr("Archivo de salida"),
                fileFilter="GeoPackage (*.gpkg);;Shapefile (*.shp)",
            )
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _build_fields() -> QgsFields:
        """Define la estructura de campos de la capa resultante."""
        fields = QgsFields()
        defs = [
            ("gbif_id", QMETATYPE_STRING, 64),
            ("occ_status", QMETATYPE_STRING, 64),
            ("rank", QMETATYPE_STRING, 64),
            ("kingdom", QMETATYPE_STRING, 100),
            ("phylum", QMETATYPE_STRING, 100),
            ("class", QMETATYPE_STRING, 100),
            ("order", QMETATYPE_STRING, 100),
            ("family", QMETATYPE_STRING, 100),
            ("genus", QMETATYPE_STRING, 100),
            ("species", QMETATYPE_STRING, 200),
            ("sci_name", QMETATYPE_STRING, 254),
            ("taxon_key", QMETATYPE_STRING, 64),
            ("observer", QMETATYPE_STRING, 254),
            ("identifier", QMETATYPE_STRING, 254),
            ("event_date", QMETATYPE_STRING, 64),
            ("year", QMETATYPE_INT, 4),
            ("dataset_key", QMETATYPE_STRING, 64),
            ("uncertainty_m", QMETATYPE_DOUBLE, 12, 2),
            ("country", QMETATYPE_STRING, 64),
            ("basis", QMETATYPE_STRING, 64),
            ("gbif_url", QMETATYPE_STRING, 254),
            ("taxon_url", QMETATYPE_STRING, 254),
        ]
        for entry in defs:
            if len(entry) == 3:
                name, vtype, length = entry
                fields.append(QgsField(name, vtype, "", length))
            else:
                name, vtype, length, precision = entry
                fields.append(QgsField(name, vtype, "", length, precision))
        return fields

    @staticmethod
    def _extent_diagonal_meters(extent_4326, feedback) -> float:
        """Estima la diagonal del extent en metros (proyección UTM local)."""
        center_lon = (extent_4326.xMinimum() + extent_4326.xMaximum()) / 2.0
        center_lat = (extent_4326.yMinimum() + extent_4326.yMaximum()) / 2.0

        utm_zone = int((center_lon + 180) / 6) + 1
        epsg = 32600 + utm_zone if center_lat >= 0 else 32700 + utm_zone

        try:
            crs_utm = QgsCoordinateReferenceSystem(f"EPSG:{epsg}")
            crs_4326 = QgsCoordinateReferenceSystem("EPSG:4326")
            transform = QgsCoordinateTransform(crs_4326, crs_utm, QgsProject.instance())
            geom = QgsGeometry.fromRect(extent_4326)
            geom.transform(transform)
            bbox_utm = geom.boundingBox()
            width = bbox_utm.width()
            height = bbox_utm.height()
            return math.sqrt(width ** 2 + height ** 2)
        except Exception as e:
            feedback.pushDebugInfo(f"UTM transform failed ({e}), using sphere approx")
            dx_deg = extent_4326.width()
            dy_deg = extent_4326.height()
            lat_rad = math.radians(center_lat)
            dx_m = dx_deg * 111320 * math.cos(lat_rad)
            dy_m = dy_deg * 110540
            return math.sqrt(dx_m ** 2 + dy_m ** 2)

    @staticmethod
    def _build_query_params(
        extent_4326,
        kingdom_key,
        max_uncertainty_m: float,
        date_start_iso: str,
        date_end_iso: str,
        offset: int,
        limit: int,
    ) -> dict:
        """Construye los parámetros de la query GBIF."""
        params = {
            "hasCoordinate": "true",
            "hasGeospatialIssue": "false",
            "offset": offset,
            "limit": limit,
        }

        xmin = extent_4326.xMinimum()
        xmax = extent_4326.xMaximum()
        ymin = extent_4326.yMinimum()
        ymax = extent_4326.yMaximum()
        params["decimalLongitude"] = f"{xmin:.6f},{xmax:.6f}"
        params["decimalLatitude"] = f"{ymin:.6f},{ymax:.6f}"

        if kingdom_key is not None:
            params["kingdomKey"] = kingdom_key
        if max_uncertainty_m is not None:
            # Rango: sin límite inferior (*) hasta el umbral calculado
            params["coordinateUncertaintyInMeters"] = f"*,{int(max_uncertainty_m)}"
        # GBIF acepta rangos de fechas usando una coma (,) como separador.
        # El asterisco (*) se usa como comodín para extremos abiertos.
        if date_start_iso and date_end_iso:
            params["eventDate"] = f"{date_start_iso},{date_end_iso}"
        elif date_start_iso:
            params["eventDate"] = f"{date_start_iso},*"
        elif date_end_iso:
            params["eventDate"] = f"*,{date_end_iso}"
        return params

    @staticmethod
    def _aoi_from_layer(source, feedback):
        """Procesa la capa vectorial: disuelve features, reproyecta a 4326,
        y devuelve el extent y la geometría unificada exacta para filtrado local.
        """
        crs_4326 = QgsCoordinateReferenceSystem("EPSG:4326")
        source_crs = source.sourceCrs()
        transform = None
        if source_crs.isValid() and source_crs != crs_4326:
            transform = QgsCoordinateTransform(source_crs, crs_4326, QgsProject.instance())

        geoms = []
        for feat in source.getFeatures(QgsFeatureRequest()):
            g = feat.geometry()
            if g.isNull() or g.isEmpty():
                continue
            if transform is not None:
                g = QgsGeometry(g)
                g.transform(transform)
            geoms.append(g)

        if not geoms:
            raise QgsProcessingException("La capa vectorial no contiene geometrías válidas.")

        unified = QgsGeometry.unaryUnion(geoms)
        if unified.isNull() or unified.isEmpty():
            raise QgsProcessingException("No se pudo unificar la geometría de la capa.")

        extent_4326 = unified.boundingBox()

        # Usamos el bbox (extent) del polígono como filtro espacial en la API GBIF.
        xmin = extent_4326.xMinimum()
        xmax = extent_4326.xMaximum()
        ymin = extent_4326.yMinimum()
        ymax = extent_4326.yMaximum()
        feedback.pushInfo(
            f"  • Extent calculado de la capa vectorial (WGS84):\n"
            f"    Lon: {xmin:.6f} → {xmax:.6f}\n"
            f"    Lat: {ymin:.6f} → {ymax:.6f}"
        )

        # Devolvemos la geometría unificada para filtrar los puntos localmente después de descargar
        return extent_4326, unified

    @staticmethod
    def _fetch_page(params: dict) -> dict:
        """Llama a la API GBIF con retry inteligente.

        - Errores 4xx (cliente): falla inmediatamente con mensaje claro.
        - Errores 5xx (servidor): reintenta con backoff exponencial largo.
        - Timeout de red: reintenta con backoff corto.
        """
        import urllib.error
        url = GBIF_API_URL + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "QGIS-GeoForestTools-GBIF/1.0",
                "Accept": "application/json",
            },
        )
        last_err = None
        for attempt in range(RETRY_ATTEMPTS):
            try:
                with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                last_err = e
                if 400 <= e.code < 500:
                    # Error de cliente: no reintentar, reportar inmediatamente
                    raise QgsProcessingException(
                        f"Error HTTP {e.code} en GBIF API (parámetro inválido): {e.reason}\n"
                        f"URL: {url}"
                    )
                # Error de servidor (5xx): esperar más tiempo
                if attempt < RETRY_ATTEMPTS - 1:
                    wait = SERVER_ERROR_SLEEP * (2 ** attempt)  # 10s, 20s, 40s...
                    time.sleep(wait)
            except Exception as e:
                last_err = e
                if attempt < RETRY_ATTEMPTS - 1:
                    time.sleep(2 ** attempt)
        raise QgsProcessingException(
            f"Error tras {RETRY_ATTEMPTS} intentos en GBIF API: {last_err}"
        )

    @staticmethod
    def _obs_to_feature(obs: dict, fields: QgsFields, crs_transform=None, filter_geom_4326=None):
        """Convierte un registro GBIF a QgsFeature. Retorna None si no es válido o está fuera del polígono."""
        lat = obs.get("decimalLatitude")
        lon = obs.get("decimalLongitude")
        if lat is None or lon is None:
            return None

        feat = QgsFeature(fields)
        geom = QgsGeometry.fromPointXY(QgsPointXY(float(lon), float(lat)))

        # Filtro de intersección con polígono exacto (en EPSG:4326)
        if filter_geom_4326 is not None:
            if not geom.intersects(filter_geom_4326):
                return None

        if crs_transform is not None:
            try:
                geom.transform(crs_transform)
            except Exception:
                return None
        feat.setGeometry(geom)

        def s(key):
            v = obs.get(key)
            return str(v) if v is not None else NULL

        if "genericName" in obs:
            parts = [obs["genericName"]]
            if "specificEpithet" in obs:
                parts.append(obs["specificEpithet"])
                if "infraspecificEpithet" in obs:
                    parts.extend(["subsp.", obs["infraspecificEpithet"]])
            sci_name = " ".join(parts)
        else:
            sci_name = obs.get("scientificName", NULL)

        if "identifiedBy" in obs:
            identifier = str(obs["identifiedBy"])
        elif "identifiers" in obs:
            ids = [d.get("identifier", "") for d in obs["identifiers"] if "identifier" in d]
            identifier = ", ".join(ids) if ids else NULL
        else:
            identifier = NULL

        date_val = obs.get("eventDate") or obs.get("verbatimEventDate") or NULL
        year_val = obs.get("year")
        try:
            year_val = int(year_val) if year_val is not None else NULL
        except (TypeError, ValueError):
            year_val = NULL

        uncertainty = obs.get("coordinateUncertaintyInMeters")
        try:
            uncertainty = float(uncertainty) if uncertainty is not None else NULL
        except (TypeError, ValueError):
            uncertainty = NULL

        gbif_key = obs.get("key", "")
        taxon_key = obs.get("acceptedTaxonKey", "")

        feat.setAttributes([
            str(gbif_key) if gbif_key else NULL,
            s("occurrenceStatus"),
            s("taxonRank"),
            s("kingdom"),
            s("phylum"),
            s("class"),
            s("order"),
            s("family"),
            s("genus"),
            s("species"),
            sci_name,
            str(taxon_key) if taxon_key else NULL,
            s("recordedBy"),
            identifier,
            str(date_val) if date_val != NULL else NULL,
            year_val,
            s("datasetKey"),
            uncertainty,
            s("countryCode"),
            s("basisOfRecord"),
            f"https://www.gbif.org/occurrence/{gbif_key}" if gbif_key else NULL,
            f"https://www.gbif.org/species/{taxon_key}" if taxon_key else NULL,
        ])
        return feat

    # ------------------------------------------------------------------
    # Ejecución
    # ------------------------------------------------------------------
    def processAlgorithm(self, parameters, context, feedback):
        # --- Leer parámetros ---
        kingdom_idx = self.parameterAsEnum(parameters, self.KINGDOM, context)
        quality_idx = self.parameterAsEnum(parameters, self.QUALITY, context)
        max_obs = self.parameterAsInt(parameters, self.MAX_OBS, context)
        output_crs = self.parameterAsCrs(parameters, self.OUTPUT_CRS, context)
        output_path = self.parameterAsFileOutput(parameters, self.OUTPUT_FILE, context)

        kingdom_key, kingdom_label = KINGDOM_OPTIONS[kingdom_idx]
        quality_pct, quality_label = QUALITY_OPTIONS[quality_idx]
        
        ext = os.path.splitext(output_path)[1].lower()
        if ext == ".shp":
            driver_name = "ESRI Shapefile"
            ext_clean = "shp"
        else:
            driver_name = "GPKG"
            ext_clean = "gpkg"
            if not ext:
                output_path += ".gpkg"

        crs_4326 = QgsCoordinateReferenceSystem("EPSG:4326")

        # --- Resolver área de interés (capa vectorial > extent) ---
        layer_source = self.parameterAsSource(parameters, self.INPUT_LAYER, context)
        extent_4326 = None
        exact_geom_4326 = None
        aoi_mode = ""

        if layer_source is not None:
            feedback.pushInfo("Área de interés: capa vectorial")
            extent_4326, exact_geom_4326 = self._aoi_from_layer(layer_source, feedback)
            aoi_mode = "capa vectorial (filtro local por polígono exacto)"
        else:
            extent = self.parameterAsExtent(parameters, self.INPUT_EXTENT, context)
            if extent is None or extent.isEmpty():
                raise QgsProcessingException(
                    "Debe proporcionar un área de interés: capa vectorial o extent."
                )
            extent_crs = self.parameterAsExtentCrs(parameters, self.INPUT_EXTENT, context)
            if extent_crs.isValid() and extent_crs != crs_4326:
                tr = QgsCoordinateTransform(extent_crs, crs_4326, QgsProject.instance())
                geom = QgsGeometry.fromRect(extent)
                geom.transform(tr)
                extent_4326 = geom.boundingBox()
            else:
                extent_4326 = extent
            aoi_mode = "extent rectangular"
            feedback.pushInfo(f"Área de interés: extent ({extent_4326.toString(4)})")

        # --- Calcular umbral de incertidumbre en metros ---
        max_uncertainty_m = None
        if quality_pct is not None:
            diagonal_m = self._extent_diagonal_meters(extent_4326, feedback)
            max_uncertainty_m = diagonal_m * quality_pct
            feedback.pushInfo(
                f"Diagonal del área: {diagonal_m:,.0f} m | "
                f"Umbral de incertidumbre: ≤ {max_uncertainty_m:,.0f} m "
                f"({int(quality_pct * 100)}%)"
            )

        date_start_iso = ""
        date_end_iso = ""

        feedback.pushInfo(
            f"\nFiltros aplicados:\n"
            f"  • AOI: {aoi_mode}\n"
            f"  • Reino: {kingdom_label} (kingdomKey={kingdom_key})\n"
            f"  • Calidad: {quality_label}\n"
            f"  • Máx. registros: {max_obs:,}"
        )

        # --- Consulta inicial para conteo ---
        feedback.setProgressText("Consultando total disponible en GBIF...")
        count_params = self._build_query_params(
            extent_4326, kingdom_key, max_uncertainty_m,
            date_start_iso, date_end_iso, offset=0, limit=1,
        )
        debug_url = GBIF_API_URL + "?" + urllib.parse.urlencode(count_params)
        feedback.pushDebugInfo(f"URL query: {debug_url}")
        try:
            count_data = self._fetch_page(count_params)
        except QgsProcessingException:
            raise
        except Exception as e:
            raise QgsProcessingException(f"Error consultando GBIF: {e}")

        total_available = count_data.get("count", 0)

        # --- Preparar transform de salida (4326 → CRS elegido) ---
        if output_crs.isValid() and output_crs != crs_4326:
            out_transform = QgsCoordinateTransform(
                crs_4326, output_crs, QgsProject.instance()
            )
            feedback.pushInfo(
                f"  • CRS de salida: {output_crs.authid()} ({output_crs.description()})"
            )
        else:
            out_transform = None
            output_crs = crs_4326
            feedback.pushInfo("  • CRS de salida: EPSG:4326 (sin reproyección)")

        # --- Normalizar extensión del archivo de salida ---
        if not output_path.lower().endswith(f".{ext_clean}"):
            output_path = os.path.splitext(output_path)[0] + f".{ext_clean}"

        # --- Crear writer (streaming directo al archivo final) ---
        fields = self._build_fields()
        save_options = QgsVectorFileWriter.SaveVectorOptions()
        save_options.driverName = driver_name
        save_options.fileEncoding = "UTF-8"

        if driver_name == "ESRI Shapefile":
            feedback.pushInfo(
                "  • Shapefile: nombres de campo truncados a 10 caracteres por el driver OGR. "
                "Use GeoPackage si requiere nombres completos."
            )

        writer = QgsVectorFileWriter.create(
            fileName=output_path,
            fields=fields,
            geometryType=QgsWkbTypes.Point,
            srs=output_crs,
            transformContext=context.transformContext(),
            options=save_options,
        )
        if writer.hasError() != QgsVectorFileWriter.NoError:
            raise QgsProcessingException(
                f"Error al crear archivo de salida: {writer.errorMessage()}"
            )

        if total_available == 0:
            # Construir resumen de filtros activos para orientar al usuario
            filtros_activos = []
            if kingdom_key is not None:
                filtros_activos.append(f"Reino: {kingdom_label}")
            if max_uncertainty_m is not None:
                filtros_activos.append(f"Calidad: {quality_label}")
            filtros_str = "\n".join(f"    • {f}" for f in filtros_activos) if filtros_activos else "    • (ninguno adicional)"

            feedback.reportError(
                "\n"
                "╔══════════════════════════════════════════════════════════════╗\n"
                "║   ⚠  SIN OCURRENCIAS EN EL ÁREA DE ESTUDIO                 ║\n"
                "╚══════════════════════════════════════════════════════════════╝\n"
                "\nGBIF no encontró registros con los filtros aplicados:\n"
                + filtros_str + "\n"
                "\nSugerencias para obtener resultados:\n"
                "  1. Cambia el Reino biológico (prueba 'Sin filtro' para todos\n"
                "     los organismos, o selecciona Animal/Fungi).\n"
                "  2. Amplía el rango de fechas o usa 'Sin filtro de calidad'.\n"
                "  3. Verifica que el área de interés esté en coordenadas WGS84 o tenga proyección.\n"
                "  4. Consulta: https://www.gbif.org/occurrence/search\n",
                fatalError=False,
            )
            del writer
            return {
                self.OUTPUT_FILE: output_path,
                "TOTAL_AVAILABLE": 0,
                "DOWNLOADED": 0,
            }

        to_download = min(max_obs, total_available, ABSOLUTE_MAX_OBS)
        feedback.pushInfo(
            f"\n✓ GBIF reporta {total_available:,} registros disponibles.\n"
            f"  Se descargarán: {to_download:,}"
        )
        if total_available > to_download:
            feedback.pushWarning(
                f"⚠ Existen {total_available - to_download:,} registros adicionales "
                f"que NO se descargarán (límite del usuario o de la API)."
            )

        # --- Descarga paginada (streaming al archivo) ---
        total_pages = math.ceil(to_download / PER_PAGE_LIMIT)
        downloaded = 0
        skipped = 0
        offset = 0

        for page in range(total_pages):
            if feedback.isCanceled():
                feedback.pushInfo("Cancelado por el usuario.")
                break

            limit = min(PER_PAGE_LIMIT, to_download - downloaded)
            feedback.setProgressText(
                f"Página {page + 1}/{total_pages} ({downloaded:,}/{to_download:,} registros)"
            )

            params = self._build_query_params(
                extent_4326, kingdom_key, max_uncertainty_m,
                date_start_iso, date_end_iso, offset=offset, limit=limit,
            )
            data = self._fetch_page(params)
            results = data.get("results", [])
            if not results:
                feedback.pushInfo("Sin más resultados disponibles.")
                break

            for obs in results:
                if feedback.isCanceled():
                    break
                feat = self._obs_to_feature(
                    obs, fields, crs_transform=out_transform, filter_geom_4326=exact_geom_4326
                )
                if feat is None:
                    skipped += 1
                    continue
                if not writer.addFeature(feat):
                    skipped += 1
                    continue
                downloaded += 1

            offset += limit
            feedback.setProgress(int((downloaded / to_download) * 100))
            time.sleep(RATE_LIMIT_SLEEP)

        del writer  # cierra y hace flush del archivo

        feedback.pushInfo(
            f"\n✓ Descarga finalizada.\n"
            f"  Archivo: {output_path}\n"
            f"  Registros válidos: {downloaded:,}\n"
            f"  Descartados (sin coords o error): {skipped:,}"
        )

        # Cargar la capa en el proyecto al finalizar
        layer_name = os.path.splitext(os.path.basename(output_path))[0]
        context.addLayerToLoadOnCompletion(
            output_path,
            QgsProcessingContext.LayerDetails(
                layer_name, context.project(), self.OUTPUT_FILE
            ),
        )

        return {
            self.OUTPUT_FILE: output_path,
            "TOTAL_AVAILABLE": total_available,
            "DOWNLOADED": downloaded,
        }
