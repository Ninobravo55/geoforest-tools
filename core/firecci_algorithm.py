import os
import json
import ee
import requests
import pandas as pd
import geopandas as gpd
import datetime as dt
from qgis.core import QgsVectorLayer, QgsProject, QgsProcessingException
from qgis.PyQt.QtGui import QColor

from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterExtent,
    QgsProcessingParameterEnum,
    QgsProcessingParameterCrs,
    QgsProcessingParameterFolderDestination,
    QgsProcessingParameterString,
    QgsProcessingParameterMapLayer,
    QgsProcessingParameterDateTime
)
from qgis.PyQt.QtCore import QCoreApplication, QDateTime

from .early_warning_charts import EarlyWarningCharts

from ..utils.aoi_builder import build_aoi, to_ee_geometry, safe_simplify
from ..utils.gee_init import ensure_gee_initialized
from ..utils.export_router import export_table, ExportMethod

class FireCCIAlgorithm(QgsProcessingAlgorithm):
    INPUT_LAYER = 'INPUT_LAYER'
    INPUT_EXTENT = 'INPUT_EXTENT'
    START_DATE = 'START_DATE'
    END_DATE = 'END_DATE'
    EPSG = 'EPSG'
    EXPORT_METHOD = 'EXPORT_METHOD'
    OUT_FOLDER = 'OUT_FOLDER'
    GCS_BUCKET = 'GCS_BUCKET'

    def __init__(self):
        super().__init__()
        self.export_methods = [
            "Descarga Directa (Vector + Gráficos)",
            "Exportar a Google Drive",
            "Exportar a Google Cloud Storage (GCS)"
        ]

    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    def createInstance(self):
        return FireCCIAlgorithm()

    def name(self):
        return 'firecci_analysis'

    def displayName(self):
        return self.tr('Áreas Quemadas FireCCI ESA')

    def group(self):
        return self.tr('Monitoreo de incendio')

    def groupId(self):
        return 'monitoreo_incendio'

    def shortHelpString(self):
        help_text = (
            "<b>Descripción del Proceso:</b><br>"
            "El producto de píxeles de área quemada de MODIS Fire_cci versión 5.1 (FireCCI51) es un conjunto de datos "
            "global mensual con una resolución espacial de ~250 m que contiene información sobre el área quemada y "
            "datos auxiliares. Se basa en la reflectancia de la superficie en la banda del infrarrojo cercano (NIR) "
            "del instrumento MODIS a bordo del satélite Terra, así como en la información de incendios activos del "
            "mismo sensor de los satélites Terra y Aqua.<br><br>"
            "Esta herramienta extrae las áreas quemadas del proyecto FireCCI (ESA) "
            "empleando Google Earth Engine (GEE). Filtra las imágenes por fecha y área de interés.<br>"
            "Genera polígonos de las áreas afectadas, además de estadísticas "
            "<b>Disponibilidad temporal:</b><br>"
            "Los datos de esta colección están disponibles únicamente desde el <b>2001-01-01</b> hasta el <b>2020-12-01</b>.<br><br>"
            "<b>Nota:</b> El conjunto de datos FireCCI v5.1 proporciona el día juliano de quema. "
            "Para un correcto cálculo de fechas en la descarga directa, se asume que las fechas consultadas "
            "pertenecen al año de la fecha de inicio.<br><br>"
            "<b>Fuente:</b> <a href='https://developers.google.com/earth-engine/datasets/catalog/ESA_CCI_FireCCI_5_1?hl'>https://developers.google.com/earth-engine/datasets/catalog/ESA_CCI_FireCCI_5_1</a>"
        )
        return self.tr(help_text)

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterMapLayer(
                self.INPUT_LAYER,
                self.tr('Opción 1: Seleccionar Capa Vectorial (Desde el Panel)'),
                [QgsProcessing.TypeVectorPolygon],
                optional=True
            )
        )
        self.addParameter(QgsProcessingParameterExtent(self.INPUT_EXTENT, self.tr('Opción 2: O dibujar un Recuadro'), optional=True))
        
        default_start = QDateTime(2019, 1, 1, 0, 0)
        default_end = QDateTime(2020, 12, 1, 0, 0)
        
        self.addParameter(QgsProcessingParameterDateTime(self.START_DATE, self.tr('Fecha de Inicio (Disponible 2001-2020)'), type=QgsProcessingParameterDateTime.Date, defaultValue=default_start))
        self.addParameter(QgsProcessingParameterDateTime(self.END_DATE, self.tr('Fecha de Fin (Disponible 2001-2020)'), type=QgsProcessingParameterDateTime.Date, defaultValue=default_end))
        
        self.addParameter(QgsProcessingParameterCrs(self.EPSG, self.tr('CRS de Destino'), defaultValue='EPSG:32718', optional=True))
        self.addParameter(QgsProcessingParameterEnum(self.EXPORT_METHOD, self.tr('Método de Exportación'), options=self.export_methods, defaultValue=0))
        self.addParameter(QgsProcessingParameterFolderDestination(self.OUT_FOLDER, self.tr('Carpeta Local de Destino (Obligatoria para Descarga Directa)'), optional=True))
        self.addParameter(QgsProcessingParameterString(self.GCS_BUCKET, self.tr('Nombre del Bucket GCS (Solo para GCS)'), optional=True))

    def processAlgorithm(self, parameters, context, feedback):
        start_date_qdt = self.parameterAsDateTime(parameters, self.START_DATE, context)
        end_date_qdt = self.parameterAsDateTime(parameters, self.END_DATE, context)
        start_date = start_date_qdt.toString('yyyy-MM-dd')
        end_date = end_date_qdt.toString('yyyy-MM-dd')
        
        start_year = start_date_qdt.date().year()
        
        crs_dest = self.parameterAsCrs(parameters, self.EPSG, context)
        target_crs_str = crs_dest.authid() if crs_dest.isValid() else 'EPSG:4326'
        export_method_idx = self.parameterAsEnum(parameters, self.EXPORT_METHOD, context)
        out_folder = self.parameterAsString(parameters, self.OUT_FOLDER, context)
        gcs_bucket = self.parameterAsString(parameters, self.GCS_BUCKET, context)
        
        if export_method_idx == 0 and not out_folder:
            raise QgsProcessingException("Debe seleccionar una Carpeta Local de Destino para la descarga directa.")
            
        # AOI centralizado (utils/aoi_builder)
        aoi = build_aoi(self, parameters, context, feedback)
        geom_union = aoi.geom_4326
            
        geom_union_simp = safe_simplify(geom_union, 0.0001)
        geo_dict = json.loads(geom_union_simp.asJson())
        
        ensure_gee_initialized(feedback)
                
        # Geometría EE sin truncar GeometryCollection — fix B-01
        geom_ee_exact = to_ee_geometry(geom_union_simp, ee, geo_dict)
            
        geom_ee = geom_ee_exact
        
        feedback.setProgress(10)
        feedback.pushInfo(f"Procesando FireCCI desde {start_date} hasta {end_date}...")
        
        dataset = ee.ImageCollection('ESA/CCI/FireCCI/5_1') \
            .filterDate(start_date, end_date) \
            .filterBounds(geom_ee)
            
        try:
            count = dataset.size().getInfo()
            if count == 0:
                raise QgsProcessingException("No se encontraron imágenes en el rango de fechas seleccionado.")
        except Exception as e:
            if "No se encontraron imágenes" in str(e):
                raise e
                
        max_ba = dataset.select('BurnDate').max().clip(geom_ee)
        burned = max_ba.updateMask(max_ba.gt(0))
        burned_py = burned.reproject(crs=target_crs_str, scale=250)
        
        feedback.setProgress(30)
        feedback.pushInfo("Vectorizando áreas quemadas en GEE...")
        
        burned_Fecha = burned_py.updateMask(burned_py).reduceToVectors(
            geometry=geom_ee,
            crs=burned_py.projection(),
            scale=250,
            geometryType='polygon',
            reducer=ee.Reducer.countEvery(),
            eightConnected=False,
            labelProperty='valor',
            maxPixels=1e13
        )
        
        out_filename = f"Area_quema_ESA_{start_year}"
        
        if export_method_idx == 0:  # Descarga Directa
            feedback.setProgress(60)
            feedback.pushInfo("Descargando vector desde GEE (GeoJSON)...")
            try:
                url = burned_Fecha.getDownloadURL(
                    filetype='geojson',
                    filename=out_filename
                )
                r = requests.get(url, stream=True)
                r.raise_for_status()
                
                geojson_path = os.path.join(out_folder, f"{out_filename}.geojson")
                with open(geojson_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        if chunk: f.write(chunk)
                        
                feedback.pushInfo("Post-procesando datos localmente (Cálculo de Área y Fechas)...")
                gdf = gpd.read_file(geojson_path)
                
                if gdf.empty:
                    raise QgsProcessingException("No se encontraron áreas quemadas en la zona y rango indicados.")
                    
                gdf_utm = gdf.to_crs(target_crs_str)
                
                def juliano_a_fecha(dia_juliano):
                    try:
                        return dt.datetime(start_year, 1, 1) + dt.timedelta(days=int(dia_juliano) - 1)
                    except:
                        return pd.NaT
                        
                gdf_utm['Fecha'] = gdf_utm['valor'].apply(juliano_a_fecha)
                gdf_utm = gdf_utm.dropna(subset=['Fecha'])
                
                gdf_utm['Mes_Num'] = gdf_utm['Fecha'].dt.month
                gdf_utm['num_mes'] = gdf_utm['Mes_Num'] # Para compatibilidad con EarlyWarningCharts
                
                meses_map = {1: 'Enero', 2: 'Febrero', 3: 'Marzo', 4: 'Abril', 5: 'Mayo', 6: 'Junio',
                             7: 'Julio', 8: 'Agosto', 9: 'Setiembre', 10: 'Octubre', 11: 'Noviembre', 12: 'Diciembre'}
                gdf_utm['Mes'] = gdf_utm['Mes_Num'].map(meses_map)
                
                gdf_utm['Fecha_str'] = gdf_utm['Fecha'].dt.strftime('%Y-%m-%d')
                gdf_utm['area_ha'] = gdf_utm.geometry.area / 10000.0
                
                gdf_save = gdf_utm.drop(columns=['Fecha'])
                
                shp_path = os.path.join(out_folder, f"{out_filename}.shp")
                gdf_save.to_file(shp_path)
                
                feedback.pushInfo("Generando estadísticas y gráficos...")
                EarlyWarningCharts.generate_summary_and_chart(gdf_utm, out_folder, out_filename, "FireCCI ESA")
                
                # Cargar a QGIS
                vlayer = QgsVectorLayer(shp_path, out_filename, "ogr")
                if vlayer.isValid():
                    from qgis.core import QgsCategorizedSymbolRenderer, QgsRendererCategory, QgsSymbol
                    
                    field_name = 'Mes_Num'
                    field_idx = vlayer.fields().indexOf(field_name)
                    if field_idx != -1:
                        unique_values = vlayer.uniqueValues(field_idx)
                        categories = []
                        colors = ["#ffeda0", "#fed976", "#feb24c", "#fd8d3c", "#fc4e2a", "#e31a1c", "#bd0026"]
                        color_idx = 0
                        
                        for val in sorted(list(unique_values)):
                            color = QColor(colors[color_idx % len(colors)])
                            symbol = QgsSymbol.defaultSymbol(vlayer.geometryType())
                            symbol.setColor(color)
                            symbol.setOpacity(0.8)
                            label = meses_map.get(val, str(val))
                            category = QgsRendererCategory(val, symbol, label)
                            categories.append(category)
                            color_idx += 1

                        renderer = QgsCategorizedSymbolRenderer(field_name, categories)
                        vlayer.setRenderer(renderer)
                        vlayer.triggerRepaint()

                    QgsProject.instance().addMapLayer(vlayer)
                    
            except Exception as e:
                feedback.reportError(f"Error en descarga directa o vectorización: {str(e)}", fatalError=False)
                feedback.pushInfo("Intentando Exportar a Google Drive como alternativa...")
                export_table(
                    method=ExportMethod.DRIVE, collection=burned_Fecha,
                    description=out_filename, file_format='SHP',
                    drive_folder="GEE_Quema", fileNamePrefix=out_filename,
                    feedback=feedback,
                )
                feedback.pushInfo(f"Tarea exportación a Drive iniciada: {out_filename}")
                
        else:  # Drive (1) o GCS (2)
            export_table(
                method=export_method_idx, collection=burned_Fecha,
                description=out_filename, file_format='SHP',
                drive_folder="GEE_Quema", gcs_bucket=gcs_bucket,
                fileNamePrefix=out_filename, feedback=feedback,
            )

        return {self.OUT_FOLDER: out_folder}
