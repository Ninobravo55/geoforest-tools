import os
import json
import ee
import requests
import processing
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterExtent,
    QgsProcessingParameterEnum,
    QgsProcessingParameterCrs,
    QgsProcessingParameterFolderDestination,
    QgsProcessingParameterString,
    QgsProcessingParameterDateTime,
    QgsProcessingParameterBoolean,
    QgsProcessingException,
    QgsProject,
    QgsProcessingParameterMapLayer,
    QgsVectorLayer,
    QgsGraduatedSymbolRenderer,
    QgsSymbol,
    QgsRendererRange
)
from qgis.PyQt.QtCore import QCoreApplication
from qgis.PyQt.QtGui import QColor

from ..utils.aoi_builder import build_aoi, to_ee_geometry, safe_simplify
from ..utils.gee_init import ensure_gee_initialized
from ..utils.export_router import export_table, ExportMethod

class GediL4AAlgorithm(QgsProcessingAlgorithm):
    INPUT_LAYER = 'INPUT_LAYER'
    INPUT_EXTENT = 'INPUT_EXTENT'
    START_DATE = 'START_DATE'
    END_DATE = 'END_DATE'
    FILTER_QUALITY = 'FILTER_QUALITY'
    FILTER_DEGRADE = 'FILTER_DEGRADE'
    FILTER_AGBD = 'FILTER_AGBD'
    FILTER_REL_ERROR = 'FILTER_REL_ERROR'
    FILTER_SENSITIVITY = 'FILTER_SENSITIVITY'
    EPSG = 'EPSG'
    EXPORT_METHOD = 'EXPORT_METHOD'
    OUT_FOLDER = 'OUT_FOLDER'
    GCS_BUCKET = 'GCS_BUCKET'

    def __init__(self):
        super().__init__()
        self.export_methods = [
            "Descarga Directa (Vector Local GeoPackage .gpkg)",
            "Exportar a Google Drive",
            "Exportar a Google Cloud Storage (GCS)"
        ]

    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    def createInstance(self):
        return GediL4AAlgorithm()

    def name(self):
        return 'gedi_l4a_analysis'

    def displayName(self):
        return self.tr('GEDI L4A AGBD - Densidad de biomasa aérea')

    def group(self):
        return self.tr('Biomasa Forestal Global')

    def groupId(self):
        return 'biomasa_forestal_global'

    def shortHelpString(self):
        help_text = (
            "<b>Descripción del Proceso:</b><br>"
            "El producto GEDI Level 4A (GEDI L4A) proporciona predicciones de la densidad de biomasa aérea "
            "(AGBD, por sus siglas en inglés) estimadas a partir de observaciones LiDAR obtenidas por la misión "
            "Global Ecosystem Dynamics Investigation (GEDI) de la NASA.<br><br>"
            "<b>Productos disponibles:</b><br>"
            "<ul>"
            "<li><b>AGBD:</b> Densidad de biomasa aérea (Mg/ha).</li>"
            "<li><b>Carbono:</b> Densidad de carbono calculada como AGBD × 0.47 (Mg/ha).</li>"
            "</ul><br>"
            "<b>Sobre los filtros de calidad:</b><br>"
            "Se aplican filtros basados en <i>l4_quality_flag</i>, <i>degrade_flag</i>, y <i>sensitivity</i>, "
            "así como controles para evitar valores negativos de biomasa (<i>agbd > 0</i>) y limitar el error "
            "relativo de predicción al 50%.<br><br>"
            "<b>Información y Fuente de Datos:</b><br>"
            "Los datos provienen de: <a href='https://developers.google.com/earth-engine/datasets/catalog/LARSE_GEDI_GEDI04_A_002_MONTHLY'>LARSE/GEDI/GEDI04_A_002_MONTHLY</a>."
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
        
        self.addParameter(QgsProcessingParameterDateTime(self.START_DATE, self.tr('Fecha de Inicio'), type=QgsProcessingParameterDateTime.Date, defaultValue='2020-01-01'))
        self.addParameter(QgsProcessingParameterDateTime(self.END_DATE, self.tr('Fecha Final'), type=QgsProcessingParameterDateTime.Date, defaultValue='2020-12-31'))
        
        self.addParameter(QgsProcessingParameterBoolean(self.FILTER_QUALITY, self.tr('Calidad GEDI (l4_quality_flag=1)'), defaultValue=True))
        self.addParameter(QgsProcessingParameterBoolean(self.FILTER_DEGRADE, self.tr('Sin degradación (degrade_flag=0)'), defaultValue=True))
        self.addParameter(QgsProcessingParameterBoolean(self.FILTER_AGBD, self.tr('Elimina biomasa menor a cero (agbd > 0)'), defaultValue=False))
        self.addParameter(QgsProcessingParameterBoolean(self.FILTER_REL_ERROR, self.tr('Error Relativo de la Biomasa <= 50%'), defaultValue=False))
        self.addParameter(QgsProcessingParameterBoolean(self.FILTER_SENSITIVITY, self.tr('Sensibilidad calidad láser (Sensitivity >= 0.9)'), defaultValue=False))
        
        self.addParameter(QgsProcessingParameterCrs(self.EPSG, self.tr('CRS de Destino (Ej. UTM)'), defaultValue='EPSG:32718', optional=True))
        self.addParameter(QgsProcessingParameterEnum(self.EXPORT_METHOD, self.tr('Método de Exportación'), options=self.export_methods, defaultValue=0))
        self.addParameter(QgsProcessingParameterFolderDestination(self.OUT_FOLDER, self.tr('Carpeta Local de Destino'), optional=False))
        self.addParameter(QgsProcessingParameterString(self.GCS_BUCKET, self.tr('Nombre del Bucket GCS (Solo para GCS)'), optional=True))

    def processAlgorithm(self, parameters, context, feedback):
        # 1. AOI centralizado (utils/aoi_builder)
        aoi = build_aoi(self, parameters, context, feedback)
        geom_union = aoi.geom_4326
            
        geom_union_simp = safe_simplify(geom_union, 0.0001)
        geo_dict = json.loads(geom_union_simp.asJson())
        
        start_date = self.parameterAsDateTime(parameters, self.START_DATE, context).toString('yyyy-MM-dd')
        end_date = self.parameterAsDateTime(parameters, self.END_DATE, context).toString('yyyy-MM-dd')
        
        f_quality = self.parameterAsBool(parameters, self.FILTER_QUALITY, context)
        f_degrade = self.parameterAsBool(parameters, self.FILTER_DEGRADE, context)
        f_agbd = self.parameterAsBool(parameters, self.FILTER_AGBD, context)
        f_rel_error = self.parameterAsBool(parameters, self.FILTER_REL_ERROR, context)
        f_sens = self.parameterAsBool(parameters, self.FILTER_SENSITIVITY, context)
        
        crs_dest = self.parameterAsCrs(parameters, self.EPSG, context)
        target_crs_str = crs_dest.authid() if crs_dest.isValid() else 'EPSG:4326'
        
        export_method_idx = self.parameterAsEnum(parameters, self.EXPORT_METHOD, context)
        out_folder = self.parameterAsString(parameters, self.OUT_FOLDER, context)
        gcs_bucket = self.parameterAsString(parameters, self.GCS_BUCKET, context)
        
        out_dir = out_folder if out_folder else ""
        if not out_dir:
            raise QgsProcessingException("Debe seleccionar una Carpeta Local de Destino.")
            
        ensure_gee_initialized(feedback)
                
        # Geometría EE sin truncar GeometryCollection — fix B-01
        geom_ee_exact = to_ee_geometry(geom_union, ee, geo_dict)
            
        feedback.setProgress(10)
        
        asset_id = 'LARSE/GEDI/GEDI04_A_002_MONTHLY'
        out_filename = f"GEDIL4A_AGBD_{start_date.replace('-','')}_{end_date.replace('-','')}"
        
        feedback.pushInfo(f"Filtrando GEDI L4A desde {start_date} hasta {end_date}...")
        
        gedi_col = ee.ImageCollection(asset_id) \
            .filterBounds(geom_ee_exact) \
            .filterDate(start_date, end_date)
            
        def process_image(img):
            if f_quality: img = img.updateMask(img.select('l4_quality_flag').eq(1))
            if f_degrade: img = img.updateMask(img.select('degrade_flag').eq(0))
            if f_agbd: img = img.updateMask(img.select('agbd').gt(0))
            if f_rel_error: 
                rel_error = img.select('agbd_se').divide(img.select('agbd'))
                img = img.updateMask(rel_error.lte(0.5))
            if f_sens: img = img.updateMask(img.select('sensitivity').gte(0.9))
            
            agbd_band = img.select('agbd').rename('AGBD')
            carbono_band = img.select('agbd').multiply(0.47).rename('Carbono')
            extracted = agbd_band.addBands(carbono_band)
            extracted = extracted.updateMask(agbd_band.gt(0))

            date = ee.Date(img.get('system:time_start'))
            year = date.get('year')
            month = date.get('month')
            dStr = date.format('YYYY-MM-dd')
            
            pts = extracted.sample(
                region=geom_ee_exact,
                scale=25,
                geometries=True
            )
            
            def add_props(f):
                coords = f.geometry().coordinates()
                return f.set({
                    'Fecha': dStr,
                    'Ano': year,
                    'Mes': month,
                    'X': coords.get(0),
                    'Y': coords.get(1)
                })
            
            return pts.map(add_props)

        points_fc = gedi_col.map(process_image).flatten()
        
        feedback.setProgress(35)
        feedback.pushInfo("Calculando estadísticas zonales...")
        
        try:
            area_ha_val = geom_ee_exact.area().divide(10000).getInfo()
            
            reducers = ee.Reducer.mean() \
                .combine(ee.Reducer.max(), sharedInputs=True) \
                .combine(ee.Reducer.min(), sharedInputs=True) \
                .combine(ee.Reducer.stdDev(), sharedInputs=True) \
                .combine(ee.Reducer.count(), sharedInputs=True)
                
            stats = points_fc.reduceColumns(
                reducer=reducers,
                selectors=['AGBD']
            ).getInfo()
            
            mean_agbd = stats.get('mean', 0)
            max_agbd = stats.get('max', 0)
            min_agbd = stats.get('min', 0)
            std_agbd = stats.get('stdDev', 0)
            count_fps = stats.get('count', 0)
            
            mean_carbono = mean_agbd * 0.47 if mean_agbd else 0
            
            try:
                import pandas as pd
                out_excel = os.path.join(out_dir, f"{out_filename}_stats.xlsx")
                df = pd.DataFrame([
                    {"Métrica": "Área de interés", "Valor": round(area_ha_val, 2), "Unidad": "ha"},
                    {"Métrica": "AGBD promedio", "Valor": round(mean_agbd, 2) if mean_agbd else 0, "Unidad": "Mg/ha"},
                    {"Métrica": "AGBD máximo", "Valor": round(max_agbd, 2) if max_agbd else 0, "Unidad": "Mg/ha"},
                    {"Métrica": "AGBD mínimo", "Valor": round(min_agbd, 2) if min_agbd else 0, "Unidad": "Mg/ha"},
                    {"Métrica": "Desviación estándar", "Valor": round(std_agbd, 2) if std_agbd else 0, "Unidad": "Mg/ha"},
                    {"Métrica": "Carbono promedio", "Valor": round(mean_carbono, 2) if mean_carbono else 0, "Unidad": "MgC/ha"},
                    {"Métrica": "Footprints válidos", "Valor": count_fps, "Unidad": ""}
                ])
                df.to_excel(out_excel, index=False)
                feedback.pushInfo(f"Estadísticas exportadas a Excel: {out_excel}")
            except ImportError:
                import csv
                out_csv = os.path.join(out_dir, f"{out_filename}_stats.csv")
                with open(out_csv, mode='w', newline='', encoding='utf-8-sig') as f:
                    writer = csv.writer(f)
                    writer.writerow(["Métrica", "Valor", "Unidad"])
                    writer.writerow(["Área de interés", round(area_ha_val, 2), "ha"])
                    writer.writerow(["AGBD promedio", round(mean_agbd, 2) if mean_agbd else 0, "Mg/ha"])
                    writer.writerow(["AGBD máximo", round(max_agbd, 2) if max_agbd else 0, "Mg/ha"])
                    writer.writerow(["AGBD mínimo", round(min_agbd, 2) if min_agbd else 0, "Mg/ha"])
                    writer.writerow(["Desviación estándar", round(std_agbd, 2) if std_agbd else 0, "Mg/ha"])
                    writer.writerow(["Carbono promedio", round(mean_carbono, 2) if mean_carbono else 0, "MgC/ha"])
                    writer.writerow(["Footprints válidos", count_fps, ""])
                feedback.pushInfo(f"Estadísticas exportadas a CSV: {out_csv}")
        except Exception as e:
            feedback.pushInfo(f"No se pudieron generar las estadísticas: {str(e)}")

        feedback.setProgress(40)
        
        if export_method_idx != 0:
            export_table(
                method=export_method_idx, collection=points_fc,
                description=out_filename, file_format='SHP',
                drive_folder='GEE_Bosques_GEDI', gcs_bucket=gcs_bucket,
                feedback=feedback,
            )
            return {self.OUT_FOLDER: out_dir}
            
        else:
            feedback.pushInfo("Descargando Puntos Vectoriales desde GEE (GeoJSON)...")
            try:
                url = points_fc.getDownloadURL(filetype='geojson')
                r = requests.get(url, stream=True)
                r.raise_for_status()
                
                geojson_file = os.path.join(out_dir, f"{out_filename}.geojson")
                with open(geojson_file, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        if chunk: f.write(chunk)
                        
                feedback.setProgress(70)
                feedback.pushInfo(f"GeoJSON descargado. Reproyectando a {target_crs_str} y guardando como GeoPackage...")
                
                gpkg_file = os.path.join(out_dir, f"{out_filename}.gpkg")
                
                params_reprj = {
                    'INPUT': geojson_file,
                    'TARGET_CRS': target_crs_str,
                    'OUTPUT': gpkg_file
                }
                
                res = processing.run("native:reprojectlayer", params_reprj, context=context, feedback=feedback)
                gpkg_path = res['OUTPUT']
                
                feedback.pushInfo("Cargando Vector a QGIS y aplicando Simbología...")
                
                vlayer = QgsVectorLayer(gpkg_path, out_filename, "ogr")
                if vlayer.isValid():
                    myRangeList = []
                    field_name = "AGBD"
                    
                    ranges = [
                        (0, 70, QColor('#ffffcc'), '0 - 70 Mg/ha'),
                        (70, 140, QColor('#c2e699'), '70 - 140 Mg/ha'),
                        (140, 210, QColor('#78c679'), '140 - 210 Mg/ha'),
                        (210, 280, QColor('#31a354'), '210 - 280 Mg/ha'),
                        (280, 9999, QColor('#006837'), '> 280 Mg/ha')
                    ]

                    for r in ranges:
                        sym = QgsSymbol.defaultSymbol(vlayer.geometryType())
                        sym.setColor(r[2])
                        sym.symbolLayer(0).setStrokeColor(QColor('gray'))
                        sym.setSize(2.0)
                        myRangeList.append(QgsRendererRange(r[0], r[1], sym, r[3]))
                    
                    renderer = QgsGraduatedSymbolRenderer(field_name, myRangeList)
                    renderer.setMode(QgsGraduatedSymbolRenderer.Custom)
                    vlayer.setRenderer(renderer)
                    vlayer.triggerRepaint()
                    
                    QgsProject.instance().addMapLayer(vlayer)
                    
                    try:
                        os.remove(geojson_file)
                    except:
                        pass
                
                return {self.OUT_FOLDER: out_dir}
            
            except Exception as ee_err:
                feedback.pushInfo(f"Error en descarga directa. Exportando a Drive... Detalle: {str(ee_err)}")
                export_table(
                    method=ExportMethod.DRIVE, collection=points_fc,
                    description=out_filename, file_format='SHP',
                    drive_folder='GEE_Bosques_GEDI', feedback=feedback,
                )
                feedback.reportError(f"NOTA: El área generó muchos puntos. Se exportó a Drive como '{out_filename}'.", fatalError=False)
                return {self.OUT_FOLDER: out_dir}
