import os
import ee
import zipfile
import requests
import datetime
import pandas as pd
import geopandas as gpd
import processing
from ..utils.aoi_builder import build_aoi, to_ee_geometry
from ..utils.gee_init import ensure_gee_initialized
from ..utils.export_router import export_image, ExportMethod

from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterExtent,
    QgsProcessingParameterEnum,
    QgsProcessingParameterCrs,
    QgsProcessingParameterFolderDestination,
    QgsProcessingParameterString,
    QgsProcessingException,
    QgsProject,
    QgsProcessingParameterMapLayer,
    QgsVectorLayer,
    QgsCategorizedSymbolRenderer,
    QgsRendererCategory,
    QgsSymbol
)
from qgis.PyQt.QtCore import QCoreApplication
from qgis.PyQt.QtGui import QColor

from .early_warning_charts import EarlyWarningCharts


class EarlyWarningAlgorithm(QgsProcessingAlgorithm):
    INPUT_LAYER = 'INPUT_LAYER'
    INPUT_EXTENT = 'INPUT_EXTENT'
    PRODUCT = 'PRODUCT'
    DATE_RANGE = 'DATE_RANGE'
    EPSG = 'EPSG'
    EXPORT_METHOD = 'EXPORT_METHOD'
    OUT_FOLDER = 'OUT_FOLDER'
    GCS_BUCKET = 'GCS_BUCKET'

    def __init__(self):
        super().__init__()
        self.productos = [
            "Alerta GLAD-L",
            "Alerta GLAD-S2",
            "Alerta RADD"
        ]
        self.date_ranges = [
            "Todo el año",
            "7 días últimos",
            "15 días últimos",
            "1 mes último",
            "2 meses últimos",
            "3 meses últimos",
            "6 meses últimos"
        ]
        self.export_methods = [
            "Descarga Directa (Vector + Gráficos Locales)",
            "Exportar a Google Drive",
            "Exportar a Google Cloud Storage (GCS)"
        ]

    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    def createInstance(self):
        return EarlyWarningAlgorithm()

    def name(self):
        return 'early_warning_analysis'

    def displayName(self):
        return self.tr('Alerta temprana Deforestación GLAD-L/S2/S1')

    def group(self):
        return self.tr('Alerta de Perturbación')

    def groupId(self):
        return 'alerta_perturbacion'

    def shortHelpString(self):
        help_text = (
            "<b>GLAD-L (Landsat)</b><br>"
            "Sistema de alertas tempranas de deforestación basado en imágenes Landsat de 30 m de resolución. "
            "Detecta pérdidas de cobertura arbórea en regiones tropicales y se actualiza aproximadamente cada 8 días."
            "<br><br>"
            "<b>GLAD-S2 (Sentinel-2)</b><br>"
            "Sistema de monitoreo forestal basado en imágenes Sentinel-2 de 10 m de resolución. "
            "Permite detectar cambios forestales con mayor detalle, especialmente en la Amazonía, "
            "mediante actualizaciones cada 5 días."
            "<br><br>"
            "<b>RADD (Radar for Detecting Deforestation)</b><br>"
            "Sistema de alertas de deforestación basado en imágenes radar Sentinel-1. "
            "Puede detectar cambios forestales incluso en presencia de nubes, siendo ideal para regiones "
            "tropicales húmedas, resolución 10 m y frecuencia de 6 a 12 días.<br>"
            "Fuente: <a href='https://www.globalforestwatch.org/blog/data-and-tools/integrated-deforestation-alerts/'>"
            "Global Forest Watch</a>"
        )
        return self.tr(help_text)


    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterMapLayer(
                self.INPUT_LAYER,
                self.tr('Opción 1: Seleccionar Capa Vectorial (Desde el Panel)'),
                types=[QgsProcessing.TypeVectorPolygon],
                optional=True
            )
        )
        self.addParameter(
            QgsProcessingParameterExtent(
                self.INPUT_EXTENT,
                self.tr('Opción 2: Dibujar o Seleccionar Extensión (Recuadro)'),
                optional=True
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.PRODUCT,
                self.tr('Producto global distribución alertas'),
                options=self.productos,
                defaultValue=0
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.DATE_RANGE,
                self.tr('Rango de fecha análisis'),
                options=self.date_ranges,
                defaultValue=0
            )
        )
        self.addParameter(
            QgsProcessingParameterCrs(
                self.EPSG,
                self.tr('Sistema de coordenadas de exportación (EPSG destino)'),
                defaultValue='EPSG:32718'
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.EXPORT_METHOD,
                self.tr('Método de Exportación'),
                options=self.export_methods,
                defaultValue=0
            )
        )
        self.addParameter(
            QgsProcessingParameterFolderDestination(
                self.OUT_FOLDER,
                self.tr('Carpeta local de destino (Para Descarga Directa)'),
                optional=True
            )
        )
        self.addParameter(
            QgsProcessingParameterString(
                self.GCS_BUCKET,
                self.tr('Nombre del Bucket GCS (Solo si usa Cloud Storage)'),
                optional=True
            )
        )

    def checkParameterValues(self, parameters, context):
        input_layer = self.parameterAsLayer(parameters, self.INPUT_LAYER, context)
        input_extent = self.parameterAsExtent(parameters, self.INPUT_EXTENT, context)

        if input_layer is None and input_extent.isNull():
            return False, self.tr('Debe proporcionar una capa de entrada o definir una extensión.')

        export_method_idx = self.parameterAsEnum(parameters, self.EXPORT_METHOD, context)
        if export_method_idx == 0:  # Descarga Directa
            out_folder = self.parameterAsString(parameters, self.OUT_FOLDER, context)
            if not out_folder:
                return False, self.tr('Debe seleccionar una carpeta local para la descarga directa.')

        if export_method_idx == 2:  # GCS
            gcs_bucket = self.parameterAsString(parameters, self.GCS_BUCKET, context)
            if not gcs_bucket:
                return False, self.tr('Debe especificar el nombre del bucket de GCS.')

        return super().checkParameterValues(parameters, context)

    def _init_gee(self, feedback=None):
        """
        Delega en el wrapper centralizado. NO llama a ee.Authenticate()
        dentro del hilo de Processing (eso bloquearía QGIS — fix B-05).
        """
        ensure_gee_initialized(feedback=feedback)

    def processAlgorithm(self, parameters, context, feedback):
        self._init_gee(feedback)

        producto_idx = self.parameterAsEnum(parameters, self.PRODUCT, context)
        producto_nombre = self.productos[producto_idx]
        date_range_idx = self.parameterAsEnum(parameters, self.DATE_RANGE, context)
        epsg_crs = self.parameterAsCrs(parameters, self.EPSG, context)
        export_method_idx = self.parameterAsEnum(parameters, self.EXPORT_METHOD, context)
        out_folder = self.parameterAsString(parameters, self.OUT_FOLDER, context)
        gcs_bucket = self.parameterAsString(parameters, self.GCS_BUCKET, context)

        # 1. AOI centralizado (utils/aoi_builder)
        aoi = build_aoi(self, parameters, context, feedback)
        geom_union = aoi.geom_4326

        # Geometría EE sin truncar GeometryCollection — fix B-01
        geom_ee = to_ee_geometry(geom_union, ee, aoi.geo_dict)

        geom_ee_buffer = geom_ee.buffer(1000)

        # 2. Fechas dinámicas
        now = datetime.datetime.now()
        current_year = now.year
        short_year = str(current_year)[-2:]
        year_str = str(current_year)

        # Establecer la fecha de inicio del filtro según selección
        if date_range_idx == 0: # Todo el año
            start_date = datetime.datetime(current_year, 1, 1)
        elif date_range_idx == 1: # 7 días últimos
            start_date = now - datetime.timedelta(days=7)
        elif date_range_idx == 2: # 15 días últimos
            start_date = now - datetime.timedelta(days=15)
        elif date_range_idx == 3: # 1 mes último
            start_date = now - pd.DateOffset(months=1)
        elif date_range_idx == 4: # 2 meses últimos
            start_date = now - pd.DateOffset(months=2)
        elif date_range_idx == 5: # 3 meses últimos
            start_date = now - pd.DateOffset(months=3)
        elif date_range_idx == 6: # 6 meses últimos
            start_date = now - pd.DateOffset(months=6)

        # Variables para GEE
        ee_start_date = ee.Date(start_date.strftime('%Y-%m-%d'))
        ee_end_date = ee.Date(now.strftime('%Y-%m-%d'))

        feedback.pushInfo(f"Procesando {producto_nombre} desde {start_date.strftime('%Y-%m-%d')} hasta {now.strftime('%Y-%m-%d')}")

        if producto_nombre == "Alerta GLAD-L":
            collection = ee.ImageCollection('projects/glad/alert/UpdResult')
            image = collection.mosaic().clip(geom_ee_buffer)
            conf_band = f'conf{short_year}'
            date_band = f'alertDate{short_year}'
            
            # Start of the year Julian
            start_julian = (start_date - datetime.datetime(current_year, 1, 1)).days + 1
            if start_julian < 1: start_julian = 1
            
            conf_img = image.select(conf_band)
            date_img = image.select(date_band)
            
            # Filtro de confianza (> 0) y fecha (>= start_julian)
            mask = conf_img.gt(0).And(date_img.gte(start_julian))
            img_for_vector = date_img.updateMask(mask).rename('Fecha')
            label_prop = 'Fecha'
            out_filename = f"ATD_GLAD_L_{year_str}"
            scale = 30
            
        elif producto_nombre == "Alerta GLAD-S2":
            S2_COLLECTION = 'projects/glad/S2alert'
            conf_img = ee.Image(f'{S2_COLLECTION}/alert').clip(geom_ee_buffer)
            date_img = ee.Image(f'{S2_COLLECTION}/alertDate').clip(geom_ee_buffer)
            
            # GLAD S2 date is days since 2018-12-31
            base_s2_date = datetime.datetime(2018, 12, 31)
            start_s2_days = (start_date - base_s2_date).days
            
            mask = conf_img.gt(0).And(date_img.gte(start_s2_days))
            img_for_vector = date_img.updateMask(mask).rename('Fecha')
            label_prop = 'Fecha'
            out_filename = f"ATD_GLAD_S2_{year_str}"
            scale = 10
            
        elif producto_nombre == "Alerta RADD":
            radd = ee.ImageCollection('projects/radar-wur/raddalert/v1')
            latest_radd_alert = ee.Image(radd.filterMetadata('layer','contains','alert')\
                              .filterMetadata('geography','contains','sa')\
                              .sort('system:time_end', False).first())
            
            conf_img = latest_radd_alert.select('Alert').clip(geom_ee_buffer)
            date_img = latest_radd_alert.select('Date').clip(geom_ee_buffer)
            
            # RADD date is YYJJJ
            start_radd_yy = int(str(start_date.year)[-2:])
            start_radd_jjj = (start_date - datetime.datetime(start_date.year, 1, 1)).days + 1
            start_radd_val = (start_radd_yy * 1000) + start_radd_jjj
            
            mask = conf_img.gt(0).And(date_img.gte(start_radd_val))
            img_for_vector = date_img.updateMask(mask).rename('Fecha')
            label_prop = 'Fecha'
            out_filename = f"ATD_RADD_{year_str}"
            scale = 10

        feedback.pushInfo("Verificando existencia de alertas en la zona...")
        try:
            stats = img_for_vector.reduceRegion(
                reducer=ee.Reducer.max(),
                geometry=geom_ee_buffer,
                scale=scale,
                maxPixels=1e10,
                tileScale=4
            ).getInfo()
            if not stats or stats.get('Fecha') is None:
                raise QgsProcessingException("No se encontraron alertas en el área y rango de fechas seleccionados.")
        except Exception as e:
            if "No se encontraron alertas" in str(e):
                raise e
            # Si hay un error de GEE (por ejemplo, memory limit), simplemente continuamos e intentamos descargar.
            feedback.pushInfo(f"Nota: No se pudo verificar previamente ({str(e)}), continuando con la descarga...")

        feedback.pushInfo("Solicitando URL de descarga del Raster a GEE...")
        
        task_name = f"{out_filename}_task"
        
        # 3. Exportación
        if export_method_idx == 0:  # Descarga Directa
            shp_path = os.path.join(out_folder, f"{out_filename}.shp")
            zip_path = os.path.join(out_folder, f"{out_filename}.zip")

            try:
                url = img_for_vector.getDownloadURL({
                    'name': out_filename,
                    'scale': scale,
                    'crs': 'EPSG:4326',
                    'region': geom_ee_buffer,
                    'format': 'GEO_TIFF'
                })
                
                feedback.pushInfo("Descargando archivo ZIP del Raster...")
                response = requests.get(url, stream=True)
                response.raise_for_status()
                with open(zip_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk: f.write(chunk)
            except Exception as e:
                feedback.pushInfo(f"El área es demasiado grande o compleja para descarga directa (Error: {str(e)}). Cambiando a exportación hacia Google Drive...")
                export_image(
                    method=ExportMethod.DRIVE, image=img_for_vector,
                    description=task_name, region=geom_ee_buffer, scale=scale,
                    crs='EPSG:4326', drive_folder="GeoForest_Exports",
                    fileNamePrefix=out_filename, feedback=feedback,
                )
                feedback.reportError(f"NOTA: El área es muy extensa. El raster '{out_filename}' fue enviado a Google Drive.", fatalError=False)
                return {}

            feedback.pushInfo("Extrayendo Raster GeoTIFF...")
            if not zipfile.is_zipfile(zip_path):
                with open(zip_path, 'rb') as f:
                    header = f.read(200)
                if header.startswith(b'II*\x00') or header.startswith(b'MM\x00*'):
                    tif_path_direct = os.path.join(out_folder, f"{out_filename}.tif")
                    import shutil
                    shutil.move(zip_path, tif_path_direct)
                else:
                    error_text = header.decode('utf-8', errors='ignore')
                    raise QgsProcessingException(f"El archivo descargado no es un ZIP válido. Respuesta de GEE: {error_text}")
            else:
                with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                    zip_ref.extractall(out_folder)
                os.remove(zip_path)
            
            tif_path = None
            for f in os.listdir(out_folder):
                if f.endswith(".tif") and out_filename in f:
                    tif_path = os.path.join(out_folder, f)
                    break
                    
            if not tif_path:
                raise QgsProcessingException("No se encontró el archivo .tif extraído.")

            feedback.pushInfo("Vectorizando raster localmente en QGIS...")
            vector_params = {
                'INPUT_RASTER': tif_path,
                'RASTER_BAND': 1,
                'FIELD_NAME': label_prop,
                'OUTPUT': shp_path
            }
            processing.run("native:pixelstopolygons", vector_params, context=context, feedback=feedback)
            
            if not os.path.exists(shp_path):
                raise QgsProcessingException("Hubo un error al vectorizar el raster localmente.")

            feedback.pushInfo("Post-procesando datos locales (Cálculo de Área y Fechas)...")
            # Post-procesamiento con GeoPandas
            gdf = gpd.read_file(shp_path)
            
            if gdf.empty:
                raise QgsProcessingException("El archivo descargado no contiene geometrías de alerta.")
                
            # Reproyectar
            target_epsg = epsg_crs.authid()
            gdf_utm = gdf.to_crs(target_epsg)
            
            # Calcular fechas
            def decode_date(val):
                try:
                    val = int(val)
                    if val == 0: return pd.NaT
                    if "RADD" in producto_nombre:
                        y = 2000 + (val // 1000)
                        j = val % 1000
                        if j == 0: return pd.NaT
                        return datetime.datetime(y, 1, 1) + datetime.timedelta(days=j - 1)
                    elif "GLAD-S2" in producto_nombre:
                        base_s2 = datetime.datetime(2018, 12, 31)
                        return base_s2 + datetime.timedelta(days=val)
                    else: # GLAD-L
                        return datetime.datetime(current_year, 1, 1) + datetime.timedelta(days=val - 1)
                except:
                    return pd.NaT

            gdf_utm['Fecha_alerta'] = gdf_utm[label_prop].apply(decode_date)
            # Filtrar fechas inválidas o fuera del rango (por si acaso el filtro en GEE dejó bordes)
            gdf_utm = gdf_utm.dropna(subset=['Fecha_alerta'])
            gdf_utm = gdf_utm[(gdf_utm['Fecha_alerta'] >= start_date) & (gdf_utm['Fecha_alerta'] <= now)]
            
            if gdf_utm.empty:
                raise QgsProcessingException("No hay alertas dentro del rango de fecha seleccionado después del filtrado estricto.")

            gdf_utm['num_mes'] = gdf_utm['Fecha_alerta'].dt.month
            
            meses_map = {1: 'Enero', 2: 'Febrero', 3: 'Marzo', 4: 'Abril', 5: 'Mayo', 6: 'Junio',
                         7: 'Julio', 8: 'Agosto', 9: 'Setiembre', 10: 'Octubre', 11: 'Noviembre', 12: 'Diciembre'}
            
            gdf_utm['mes'] = gdf_utm['num_mes'].map(meses_map)
            gdf_utm['año'] = gdf_utm['Fecha_alerta'].dt.year
            
            # Area in Hectares
            gdf_utm['area_ha'] = gdf_utm.geometry.area / 10000.0
            
            # Convert Fecha_alerta to string to save in SHP
            gdf_utm['Fecha_alerta'] = gdf_utm['Fecha_alerta'].dt.strftime('%Y-%m-%d')

            # Guardar SHP actualizado
            final_shp_path = os.path.join(out_folder, f"{out_filename}_procesado.shp")
            gdf_utm.to_file(final_shp_path)

            # Generar estadísticas y gráficos
            EarlyWarningCharts.generate_summary_and_chart(gdf_utm, out_folder, out_filename, producto_nombre)

            # Cargar a QGIS
            vlayer = QgsVectorLayer(final_shp_path, out_filename, "ogr")
            if vlayer.isValid():
                self._apply_symbology(vlayer)
                QgsProject.instance().addMapLayer(vlayer)
            
            return {self.OUT_FOLDER: out_folder}

        elif export_method_idx != 0:  # Drive o GCS
            export_image(
                method=export_method_idx, image=img_for_vector,
                description=task_name, region=geom_ee_buffer, scale=scale,
                crs='EPSG:4326', drive_folder="GeoForest_Exports",
                gcs_bucket=gcs_bucket, fileNamePrefix=out_filename,
                feedback=feedback,
            )
            return {}

    def _apply_symbology(self, layer):
        # Apply categorized symbology based on 'num_mes'
        field_name = 'num_mes'
        field_idx = layer.fields().indexOf(field_name)
        if field_idx == -1: return

        unique_values = layer.uniqueValues(field_idx)
        categories = []
        
        # Color ramp mapping (simple gradient from yellow to red)
        colors = ["#ffeda0", "#fed976", "#feb24c", "#fd8d3c", "#fc4e2a", "#e31a1c", "#bd0026"]
        color_idx = 0

        meses_map = {1: 'Enero', 2: 'Febrero', 3: 'Marzo', 4: 'Abril', 5: 'Mayo', 6: 'Junio',
                     7: 'Julio', 8: 'Agosto', 9: 'Setiembre', 10: 'Octubre', 11: 'Noviembre', 12: 'Diciembre'}

        for val in sorted(list(unique_values)):
            color = QColor(colors[color_idx % len(colors)])
            symbol = QgsSymbol.defaultSymbol(layer.geometryType())
            symbol.setColor(color)
            symbol.setOpacity(0.8)
            label = meses_map.get(val, str(val))
            category = QgsRendererCategory(val, symbol, label)
            categories.append(category)
            color_idx += 1

        renderer = QgsCategorizedSymbolRenderer(field_name, categories)
        layer.setRenderer(renderer)
        layer.triggerRepaint()
