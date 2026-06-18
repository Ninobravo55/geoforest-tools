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

class GediL2BAlgorithm(QgsProcessingAlgorithm):
    INPUT_LAYER = 'INPUT_LAYER'
    INPUT_EXTENT = 'INPUT_EXTENT'
    START_DATE = 'START_DATE'
    END_DATE = 'END_DATE'
    FILTER_QUALITY = 'FILTER_QUALITY'
    FILTER_DEGRADE = 'FILTER_DEGRADE'
    FILTER_SENSITIVITY = 'FILTER_SENSITIVITY'
    FILTER_COVER = 'FILTER_COVER'
    FILTER_FHD = 'FILTER_FHD'
    FILTER_PAI = 'FILTER_PAI'
    PRODUCT = 'PRODUCT'
    EPSG = 'EPSG'
    EXPORT_METHOD = 'EXPORT_METHOD'
    OUT_FOLDER = 'OUT_FOLDER'
    GCS_BUCKET = 'GCS_BUCKET'

    def __init__(self):
        super().__init__()
        self.productos_ui = [
            "Cover: Cobertura del dosel (%)",
            "PAI: Índice de área vegetal",
            "PAVD: Densidad volumétrica de vegetación"
        ]
        self.productos_id = [
            "cover",
            "pai",
            "pavd"
        ]
        self.export_methods = [
            "Descarga Directa (Vector Local GeoPackage .gpkg)",
            "Exportar a Google Drive",
            "Exportar a Google Cloud Storage (GCS)"
        ]

    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    def createInstance(self):
        return GediL2BAlgorithm()

    def name(self):
        return 'gedi_l2b_analysis'

    def displayName(self):
        return self.tr('GEDI L2B - Estructura Vertical y Métrica de Cobertura')

    def group(self):
        return self.tr('Biomasa Forestal Global')

    def groupId(self):
        return 'biomasa_forestal_global'

    def shortHelpString(self):
        help_text = (
            "<b>Descripción del Proceso:</b><br>"
            "El producto GEDI Level 2B (GEDI L2B) proporciona información detallada sobre la estructura vertical de "
            "la vegetación y las características del dosel forestal a partir de observaciones LiDAR obtenidas por la "
            "misión Global Ecosystem Dynamics Investigation (GEDI) de la NASA.<br><br>"
            "<b>Productos disponibles:</b><br>"
            "<ul>"
            "<li><b>Cover:</b> Cobertura del dosel (0–100%).</li>"
            "<li><b>PAI:</b> Índice de área vegetal (m²/m²).</li>"
            "<li><b>PAVD Total:</b> Densidad volumétrica acumulada de vegetación (m²/m³).</li>"
            "</ul>"
            "<b>Sobre los filtros de calidad:</b><br>"
            "En aplicaciones de análisis forestal y monitoreo ambiental, se recomienda aplicar "
            "filtros de calidad utilizando las variables <i>l2b_quality_flag</i>, <i>degrade_flag</i> y <i>sensitivity</i>, "
            "garantizando que las métricas utilizadas provengan de observaciones confiables y científicamente robustas.<br><br>"
            "<b>Información y Fuente de Datos:</b><br>"
            "Los datos provienen de: <a href='https://developers.google.com/earth-engine/datasets/catalog/LARSE_GEDI_GEDI02_B_002?hl'>LARSE/GEDI/GEDI02_B_002_MONTHLY</a>."
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
        
        self.addParameter(QgsProcessingParameterBoolean(self.FILTER_QUALITY, self.tr('Calidad GEDI (quality_flag = 1)'), defaultValue=True))
        self.addParameter(QgsProcessingParameterBoolean(self.FILTER_DEGRADE, self.tr('Sin degradación (degrade_flag = 0)'), defaultValue=True))
        self.addParameter(QgsProcessingParameterBoolean(self.FILTER_SENSITIVITY, self.tr('Sensibilidad alta (sensitivity >= 0.95)'), defaultValue=False))
        self.addParameter(QgsProcessingParameterBoolean(self.FILTER_COVER, self.tr('Cobertura vegetal > 0 (cover > 0)'), defaultValue=False))
        self.addParameter(QgsProcessingParameterBoolean(self.FILTER_FHD, self.tr('FHD válido (fhd_normal > 0)'), defaultValue=False))
        self.addParameter(QgsProcessingParameterBoolean(self.FILTER_PAI, self.tr('PAI válido (pai > 0)'), defaultValue=False))
        
        self.addParameter(QgsProcessingParameterEnum(self.PRODUCT, self.tr('Producto GEDI L2B'), options=self.productos_ui, defaultValue=0))
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
        f_sens = self.parameterAsBool(parameters, self.FILTER_SENSITIVITY, context)
        f_cover = self.parameterAsBool(parameters, self.FILTER_COVER, context)
        f_fhd = self.parameterAsBool(parameters, self.FILTER_FHD, context)
        f_pai = self.parameterAsBool(parameters, self.FILTER_PAI, context)
        
        producto_idx = self.parameterAsEnum(parameters, self.PRODUCT, context)
        producto_nombre = self.productos_id[producto_idx]
        
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
        
        asset_id = 'LARSE/GEDI/GEDI02_B_002_MONTHLY'
        out_filename = f"GEDIL2B_{producto_nombre}_{start_date.replace('-','')}_{end_date.replace('-','')}"
        
        feedback.pushInfo(f"Filtrando GEDI L2B desde {start_date} hasta {end_date}...")
        
        gedi_col = ee.ImageCollection(asset_id) \
            .filterBounds(geom_ee_exact) \
            .filterDate(start_date, end_date)
            
        def process_image(img):
            if f_quality: img = img.updateMask(img.select('l2b_quality_flag').eq(1))
            if f_degrade: img = img.updateMask(img.select('degrade_flag').eq(0))
            if f_sens: img = img.updateMask(img.select('sensitivity').gte(0.95))
            if f_cover: img = img.updateMask(img.select('cover').gt(0))
            if f_fhd: img = img.updateMask(img.select('fhd_normal').gt(0))
            if f_pai: img = img.updateMask(img.select('pai').gt(0))
            
            if producto_nombre == 'cover':
                val = img.select('cover').multiply(100).rename('Cover')
                clase = ee.Image(0).rename('Clase')
                clase = clase.where(val.lt(20), 1)
                clase = clase.where(val.gte(20).And(val.lt(40)), 2)
                clase = clase.where(val.gte(40).And(val.lt(60)), 3)
                clase = clase.where(val.gte(60).And(val.lt(80)), 4)
                clase = clase.where(val.gte(80), 5)
                extracted = val.addBands(clase)
                
                desc_dict = ee.Dictionary({'1':'Cobertura muy baja', '2':'Cobertura baja', '3':'Cobertura media', '4':'Cobertura alta', '5':'Cobertura muy alta'})
                rango_dict = ee.Dictionary({'1':'< 20', '2':'20 - 40', '3':'40 - 60', '4':'60 - 80', '5':'> 80'})
                clase_str_dict = ee.Dictionary({'1':'1', '2':'2', '3':'3', '4':'4', '5':'5'})

            elif producto_nombre == 'pai':
                val = img.select('pai').rename('PAI')
                clase = ee.Image(0).rename('Clase')
                clase = clase.where(val.lt(1), 1)
                clase = clase.where(val.gte(1).And(val.lt(2)), 2)
                clase = clase.where(val.gte(2).And(val.lt(4)), 3)
                clase = clase.where(val.gte(4).And(val.lt(6)), 4)
                clase = clase.where(val.gte(6), 5)
                extracted = val.addBands(clase)
                
                desc_dict = ee.Dictionary({'1':'Vegetación escasa', '2':'Bosque abierto', '3':'Bosque secundario', '4':'Bosque maduro', '5':'Bosque denso'})
                rango_dict = ee.Dictionary({'1':'< 1', '2':'1 - 2', '3':'2 - 4', '4':'4 - 6', '5':'> 6'})
                clase_str_dict = ee.Dictionary({'1':'Muy bajo', '2':'Bajo', '3':'Medio', '4':'Alto', '5':'Muy alto'})

            else: # pavd
                val = img.select('pavd_z.*').reduce(ee.Reducer.sum()).rename('PAVD')
                clase = ee.Image(0).rename('Clase')
                clase = clase.where(val.lt(0.20), 1)
                clase = clase.where(val.gte(0.20).And(val.lt(0.40)), 2)
                clase = clase.where(val.gte(0.40).And(val.lt(0.60)), 3)
                clase = clase.where(val.gte(0.60).And(val.lt(0.80)), 4)
                clase = clase.where(val.gte(0.80).And(val.lt(1.00)), 5)
                clase = clase.where(val.gte(1.00).And(val.lt(1.20)), 6)
                clase = clase.where(val.gte(1.20).And(val.lt(1.50)), 7)
                clase = clase.where(val.gte(1.50), 8)
                extracted = val.addBands(clase)
                
                desc_dict = ee.Dictionary({'1':'Vegetación muy escasa', '2':'Vegetación baja', '3':'Bosque abierto', '4':'Bosque secundario', '5':'Bosque desarrollado', '6':'Bosque maduro', '7':'Bosque denso', '8':'Bosque muy denso y complejo'})
                rango_dict = ee.Dictionary({'1':'< 0.20', '2':'0.20 - 0.40', '3':'0.40 - 0.60', '4':'0.60 - 0.80', '5':'0.80 - 1.00', '6':'1.00 - 1.20', '7':'1.20 - 1.50', '8':'> 1.50'})
                clase_str_dict = ee.Dictionary({'1':'1', '2':'2', '3':'3', '4':'4', '5':'5', '6':'6', '7':'7', '8':'8'})
            
            # Re-mask extracted to ensure valid pixels only using the first band (the value band)
            band_name = 'Cover' if producto_nombre == 'cover' else ('PAI' if producto_nombre == 'pai' else 'PAVD')
            extracted = extracted.updateMask(extracted.select(band_name).gt(0))

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
        feedback.pushInfo("Calculando frecuencias de clases...")
        
        try:
            histogram = points_fc.reduceColumns(
                reducer=ee.Reducer.frequencyHistogram(),
                selectors=['Clase']
            ).getInfo()
            
            freq_dict = histogram.get('histogram', {})
            total_fps = sum(freq_dict.values())
            
            if producto_nombre == 'cover':
                rangos = {
                    1: ('< 20', 'Cobertura muy baja', '1'),
                    2: ('20 - 40', 'Cobertura baja', '2'),
                    3: ('40 - 60', 'Cobertura media', '3'),
                    4: ('60 - 80', 'Cobertura alta', '4'),
                    5: ('> 80', 'Cobertura muy alta', '5')
                }
                xlabel = 'Rango Cover (%)'
                title = 'Frecuencia de Footprints por Rango de Cover'
            elif producto_nombre == 'pai':
                rangos = {
                    1: ('< 1', 'Vegetación escasa', 'Muy bajo'),
                    2: ('1 - 2', 'Bosque abierto', 'Bajo'),
                    3: ('2 - 4', 'Bosque secundario', 'Medio'),
                    4: ('4 - 6', 'Bosque maduro', 'Alto'),
                    5: ('> 6', 'Bosque denso', 'Muy alto')
                }
                xlabel = 'Rango PAI'
                title = 'Frecuencia de Footprints por Rango de PAI'
            else: # pavd
                rangos = {
                    1: ('< 0.20', 'Vegetación muy escasa', '1'),
                    2: ('0.20 - 0.40', 'Vegetación baja', '2'),
                    3: ('0.40 - 0.60', 'Bosque abierto', '3'),
                    4: ('0.60 - 0.80', 'Bosque secundario', '4'),
                    5: ('0.80 - 1.00', 'Bosque desarrollado', '5'),
                    6: ('1.00 - 1.20', 'Bosque maduro', '6'),
                    7: ('1.20 - 1.50', 'Bosque denso', '7'),
                    8: ('> 1.50', 'Bosque muy denso y complejo', '8')
                }
                xlabel = 'Rango PAVD Total (m²/m³)'
                title = 'Frecuencia de Footprints por Rango de PAVD Total'
                
            stats_list = []
            for c_id in range(1, len(rangos)+1):
                count = freq_dict.get(str(c_id), 0)
                if count == 0: count = freq_dict.get(c_id, 0)
                pct = (count / total_fps * 100) if total_fps > 0 else 0
                rango, desc, clase_label = rangos[c_id]
                stats_list.append({
                    'Clase': clase_label,
                    'Rango': rango,
                    'Descripción': desc,
                    'Frecuencia': count,
                    '%': round(pct, 2)
                })
                
            import csv
            out_csv = os.path.join(out_dir, f"{out_filename}_frecuencias.csv")
            try:
                with open(out_csv, mode='w', newline='', encoding='utf-8-sig') as f:
                    writer = csv.writer(f)
                    writer.writerow(["Clase", "Rango", "Descripción", "Frecuencia", "%"])
                    for row in stats_list:
                        writer.writerow([row['Clase'], row['Rango'], row['Descripción'], row['Frecuencia'], row['%']])
                feedback.pushInfo(f"Frecuencias exportadas a CSV: {out_csv}")
            except Exception as csv_err:
                feedback.pushInfo(f"No se pudo guardar el CSV: {str(csv_err)}")
                
            try:
                import matplotlib.pyplot as plt
                
                rangos_labels = [row['Rango'] for row in stats_list]
                frecuencias = [row['Frecuencia'] for row in stats_list]
                
                if 'seaborn-v0_8-whitegrid' in plt.style.available:
                    plt.style.use('seaborn-v0_8-whitegrid')
                elif 'seaborn-whitegrid' in plt.style.available:
                    plt.style.use('seaborn-whitegrid')
                else:
                    plt.style.use('ggplot')
                
                fig, ax = plt.subplots(figsize=(10, 6))
                
                bars = ax.bar(rangos_labels, frecuencias, color='#2ca25f', edgecolor='#005a32', linewidth=1.2)
                
                ax.set_title(title, fontsize=14, fontweight='bold', pad=20, color='#333333')
                ax.set_xlabel(xlabel, fontsize=12, fontweight='bold', labelpad=12, color='#333333')
                ax.set_ylabel('Número de Footprints', fontsize=12, fontweight='bold', labelpad=12, color='#333333')
                
                max_frec = max(frecuencias) if frecuencias else 1
                for bar in bars:
                    yval = bar.get_height()
                    if yval > 0:
                        ax.text(bar.get_x() + bar.get_width()/2, yval + (max_frec*0.015), 
                                f'{int(yval)}', ha='center', va='bottom', fontsize=10, fontweight='bold', color='#111111')
                
                plt.xticks(rotation=45, ha='right', fontsize=11)
                plt.yticks(fontsize=11)
                
                ax.spines['top'].set_visible(False)
                ax.spines['right'].set_visible(False)
                if 'seaborn' not in plt.style.available:
                    ax.spines['left'].set_visible(False)
                    ax.spines['bottom'].set_visible(False)
                
                plt.tight_layout()
                
                out_png = os.path.join(out_dir, f"{out_filename}_grafico_frecuencias.png")
                plt.savefig(out_png, dpi=300, bbox_inches='tight', facecolor='white')
                plt.close(fig)
                
                feedback.pushInfo(f"Gráfico PNG generado exitosamente: {out_png}")
            except ImportError:
                feedback.pushInfo("Aviso: No se pudo generar el gráfico PNG porque no está instalada la librería 'matplotlib'.")
            except Exception as plt_err:
                feedback.pushInfo(f"Aviso: Ocurrió un error al generar el gráfico PNG: {str(plt_err)}")
                
        except Exception as e:
            feedback.pushInfo(f"No se pudieron generar las frecuencias: {str(e)}")

        feedback.setProgress(40)
        
        if export_method_idx != 0:
            def add_ee_strings(f):
                c_str = ee.Number(f.get('Clase')).format('%d')
                if producto_nombre == 'cover':
                    desc_dict = ee.Dictionary({'1':'Cobertura muy baja', '2':'Cobertura baja', '3':'Cobertura media', '4':'Cobertura alta', '5':'Cobertura muy alta'})
                    rango_dict = ee.Dictionary({'1':'< 20', '2':'20 - 40', '3':'40 - 60', '4':'60 - 80', '5':'> 80'})
                    clase_str_dict = ee.Dictionary({'1':'1', '2':'2', '3':'3', '4':'4', '5':'5'})
                elif producto_nombre == 'pai':
                    desc_dict = ee.Dictionary({'1':'Vegetación escasa', '2':'Bosque abierto', '3':'Bosque secundario', '4':'Bosque maduro', '5':'Bosque denso'})
                    rango_dict = ee.Dictionary({'1':'< 1', '2':'1 - 2', '3':'2 - 4', '4':'4 - 6', '5':'> 6'})
                    clase_str_dict = ee.Dictionary({'1':'Muy bajo', '2':'Bajo', '3':'Medio', '4':'Alto', '5':'Muy alto'})
                else:
                    desc_dict = ee.Dictionary({'1':'Vegetación muy escasa', '2':'Vegetación baja', '3':'Bosque abierto', '4':'Bosque secundario', '5':'Bosque desarrollado', '6':'Bosque maduro', '7':'Bosque denso', '8':'Bosque muy denso y complejo'})
                    rango_dict = ee.Dictionary({'1':'< 0.20', '2':'0.20 - 0.40', '3':'0.40 - 0.60', '4':'0.60 - 0.80', '5':'0.80 - 1.00', '6':'1.00 - 1.20', '7':'1.20 - 1.50', '8':'> 1.50'})
                    clase_str_dict = ee.Dictionary({'1':'1', '2':'2', '3':'3', '4':'4', '5':'5', '6':'6', '7':'7', '8':'8'})
                return f.set({
                    'Clase_Nom': clase_str_dict.get(c_str, 'Desc'),
                    'rango': rango_dict.get(c_str, 'Desc'),
                    'descrip': desc_dict.get(c_str, 'Desc')
                })
            points_fc_export = points_fc.map(add_ee_strings)
            
            export_table(
                method=export_method_idx, collection=points_fc_export,
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
                    from qgis.core import QgsField
                    from ..utils.qt_compat import QMETATYPE_STRING
                    
                    vlayer.dataProvider().addAttributes([
                        QgsField("Clase_Nom", QMETATYPE_STRING),
                        QgsField("rango", QMETATYPE_STRING),
                        QgsField("descrip", QMETATYPE_STRING)
                    ])
                    vlayer.updateFields()
                    
                    idx_cnom = vlayer.fields().indexOf("Clase_Nom")
                    idx_rango = vlayer.fields().indexOf("rango")
                    idx_desc = vlayer.fields().indexOf("descrip")
                    
                    vlayer.startEditing()
                    for f in vlayer.getFeatures():
                        c_id = int(f['Clase']) if f['Clase'] else 0
                        if c_id in rangos:
                            rango_val, desc_val, clase_nom = rangos[c_id]
                        else:
                            rango_val, desc_val, clase_nom = ("Desconocido", "Desconocido", str(c_id))
                            
                        vlayer.changeAttributeValue(f.id(), idx_cnom, clase_nom)
                        vlayer.changeAttributeValue(f.id(), idx_rango, rango_val)
                        vlayer.changeAttributeValue(f.id(), idx_desc, desc_val)
                    vlayer.commitChanges()

                    myRangeList = []
                    field_name = "Cover" if producto_nombre == 'cover' else ("PAI" if producto_nombre == 'pai' else "PAVD")
                    
                    if producto_nombre == 'cover':
                        ranges = [
                            (0, 20, QColor('#ffffcc'), '0 - 20%'),
                            (20, 40, QColor('#c2e699'), '20 - 40%'),
                            (40, 60, QColor('#78c679'), '40 - 60%'),
                            (60, 80, QColor('#31a354'), '60 - 80%'),
                            (80, 100, QColor('#006837'), '80 - 100%')
                        ]
                    elif producto_nombre == 'pai':
                        ranges = [
                            (0, 1.6, QColor('#ffffcc'), '0 - 1.6'),
                            (1.6, 3.2, QColor('#c2e699'), '1.6 - 3.2'),
                            (3.2, 4.8, QColor('#78c679'), '3.2 - 4.8'),
                            (4.8, 6.4, QColor('#31a354'), '4.8 - 6.4'),
                            (6.4, 8.0, QColor('#006837'), '6.4 - 8.0')
                        ]
                    else: # pavd
                        ranges = [
                            (0, 0.75, QColor('white'), '0 - 0.75'),
                            (0.75, 1.5, QColor('yellow'), '0.75 - 1.5'),
                            (1.5, 2.25, QColor('green'), '1.5 - 2.25'),
                            (2.25, 3.0, QColor('darkgreen'), '2.25 - 3.0')
                        ]

                    for r in ranges:
                        sym = QgsSymbol.defaultSymbol(vlayer.geometryType())
                        sym.setColor(r[2])
                        # Borde sutil
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
                
                def add_ee_strings(f):
                    c_str = ee.Number(f.get('Clase')).format('%d')
                    if producto_nombre == 'cover':
                        desc_dict = ee.Dictionary({'1':'Cobertura muy baja', '2':'Cobertura baja', '3':'Cobertura media', '4':'Cobertura alta', '5':'Cobertura muy alta'})
                        rango_dict = ee.Dictionary({'1':'< 20', '2':'20 - 40', '3':'40 - 60', '4':'60 - 80', '5':'> 80'})
                        clase_str_dict = ee.Dictionary({'1':'1', '2':'2', '3':'3', '4':'4', '5':'5'})
                    elif producto_nombre == 'pai':
                        desc_dict = ee.Dictionary({'1':'Vegetación escasa', '2':'Bosque abierto', '3':'Bosque secundario', '4':'Bosque maduro', '5':'Bosque denso'})
                        rango_dict = ee.Dictionary({'1':'< 1', '2':'1 - 2', '3':'2 - 4', '4':'4 - 6', '5':'> 6'})
                        clase_str_dict = ee.Dictionary({'1':'Muy bajo', '2':'Bajo', '3':'Medio', '4':'Alto', '5':'Muy alto'})
                    else:
                        desc_dict = ee.Dictionary({'1':'Vegetación muy escasa', '2':'Vegetación baja', '3':'Bosque abierto', '4':'Bosque secundario', '5':'Bosque desarrollado', '6':'Bosque maduro', '7':'Bosque denso', '8':'Bosque muy denso y complejo'})
                        rango_dict = ee.Dictionary({'1':'< 0.20', '2':'0.20 - 0.40', '3':'0.40 - 0.60', '4':'0.60 - 0.80', '5':'0.80 - 1.00', '6':'1.00 - 1.20', '7':'1.20 - 1.50', '8':'> 1.50'})
                        clase_str_dict = ee.Dictionary({'1':'1', '2':'2', '3':'3', '4':'4', '5':'5', '6':'6', '7':'7', '8':'8'})
                    return f.set({
                        'Clase_Nom': clase_str_dict.get(c_str, 'Desc'),
                        'rango': rango_dict.get(c_str, 'Desc'),
                        'descrip': desc_dict.get(c_str, 'Desc')
                    })
                
                export_table(
                    method=ExportMethod.DRIVE, collection=points_fc.map(add_ee_strings),
                    description=out_filename, file_format='SHP',
                    drive_folder='GEE_Bosques_GEDI', feedback=feedback,
                )
                feedback.reportError(f"NOTA: El área generó muchos puntos. Se exportó a Drive como '{out_filename}'.", fatalError=False)
                return {self.OUT_FOLDER: out_dir}
