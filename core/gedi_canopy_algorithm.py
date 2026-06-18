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

class GediCanopyAlgorithm(QgsProcessingAlgorithm):
    INPUT_LAYER = 'INPUT_LAYER'
    INPUT_EXTENT = 'INPUT_EXTENT'
    START_DATE = 'START_DATE'
    END_DATE = 'END_DATE'
    PRODUCT = 'PRODUCT'
    EPSG = 'EPSG'
    EXPORT_METHOD = 'EXPORT_METHOD'
    OUT_FOLDER = 'OUT_FOLDER'
    GCS_BUCKET = 'GCS_BUCKET'

    def __init__(self):
        super().__init__()
        self.productos_ui = [
            "RH75: Altura del 75% de la energía",
            "RH90: Altura del 90% de la energía",
            "RH98: Altura máxima del dosel",
            "RH100: Altura máxima detectada"
        ]
        self.productos_id = [
            "rh75",
            "rh90",
            "rh98",
            "rh100"
        ]
        self.export_methods = [
            "Descarga Directa (Vector Local GeoPackage .gpkg)",
            "Exportar a Google Drive",
            "Exportar a Google Cloud Storage (GCS)"
        ]

    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    def createInstance(self):
        return GediCanopyAlgorithm()

    def name(self):
        return 'gedi_canopy_analysis'

    def displayName(self):
        return self.tr('GEDI L2A - Altura Forestal')

    def group(self):
        return self.tr('Altura Forestal Global')

    def groupId(self):
        return 'altura_forestal_global'

    def shortHelpString(self):
        help_text = (
            "<b>Descripción del Proceso:</b><br>"
            "El producto GEDI Level 2A (GEDI L2A) proporciona métricas detalladas de altura de la vegetación "
            "derivadas de observaciones LiDAR adquiridas por la misión Global Ecosystem Dynamics Investigation (GEDI) "
            "de la NASA. Operando desde la Estación Espacial Internacional (ISS), GEDI utiliza pulsos láser para "
            "medir la estructura vertical de los ecosistemas terrestres y generar información precisa sobre la altura "
            "de la vegetación a escala global.<br><br>"
            "El conjunto de datos LARSE/GEDI/GEDI02_A_002_MONTHLY disponible en Google Earth Engine corresponde a una "
            "colección mensual que contiene métricas de altura relativas derivadas de las formas de onda LiDAR. "
            "Estas métricas describen cómo se distribuye verticalmente la energía reflejada por la vegetación y "
            "permiten caracterizar la estructura tridimensional de los bosques con una resolución aproximada de 25 metros.<br><br>"
            "La principal información proporcionada por GEDI L2A son las métricas RH (Relative Height), que representan "
            "la altura a la cual se acumula un determinado porcentaje de la energía retornada por el pulso láser.<br><br>"
            "<b>Entre las métricas más utilizadas destacan:</b><br>"
            "<ul>"
            "<li><b>RH75:</b> Altura del 75% de la energía.</li>"
            "<li><b>RH90:</b> Representa la altura dominante del dosel, Altura del 90% de la energía.</li>"
            "<li><b>RH98:</b> Altura máxima del Dosel.</li>"
            "<li><b>RH100:</b> Altura máxima detectada por el sistema LiDAR.</li>"
            "</ul>"
            "<b>Información y Fuente de Datos:</b><br>"
            "Los datos provienen del proyecto GEDI alojado en GEE: <a href='https://developers.google.com/earth-engine/datasets/catalog/LARSE_GEDI_GEDI02_A_002_MONTHLY?hl'>LARSE/GEDI/GEDI02_A_002_MONTHLY</a>."
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
        
        # Filtro de Fechas
        self.addParameter(QgsProcessingParameterDateTime(self.START_DATE, self.tr('Fecha de Inicio'), type=QgsProcessingParameterDateTime.Date, defaultValue='2020-01-01'))
        self.addParameter(QgsProcessingParameterDateTime(self.END_DATE, self.tr('Fecha Final'), type=QgsProcessingParameterDateTime.Date, defaultValue='2020-12-31'))
        
        self.addParameter(QgsProcessingParameterEnum(self.PRODUCT, self.tr('Producto GEDI'), options=self.productos_ui, defaultValue=2)) # Default RH98
        self.addParameter(QgsProcessingParameterCrs(self.EPSG, self.tr('CRS de Destino (Ej. UTM)'), defaultValue='EPSG:32718', optional=True))
        self.addParameter(QgsProcessingParameterEnum(self.EXPORT_METHOD, self.tr('Método de Exportación Raster'), options=self.export_methods, defaultValue=0))
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
        geom_ee_exact = to_ee_geometry(geom_union_simp, ee, geo_dict)
            
        feedback.setProgress(10)
        
        asset_id = 'LARSE/GEDI/GEDI02_A_002_MONTHLY'
        out_filename = f"GEDI_{producto_nombre}_{start_date.replace('-','')}_{end_date.replace('-','')}"
        
        feedback.pushInfo(f"Filtrando GEDI L2A desde {start_date} hasta {end_date}...")
        
        gedi_col = ee.ImageCollection(asset_id) \
            .filterBounds(geom_ee_exact) \
            .filterDate(start_date, end_date) \
            .select(producto_nombre)
            
        # Transformar colección de imágenes a FeatureCollection de puntos manteniendo fecha
        def sample_image(img):
            rh = img.select(producto_nombre)
            
            clase = ee.Image(0).rename('Clase')
            clase = clase.where(rh.lt(5), 1)
            clase = clase.where(rh.gte(5).And(rh.lt(10)), 2)
            clase = clase.where(rh.gte(10).And(rh.lt(15)), 3)
            clase = clase.where(rh.gte(15).And(rh.lt(20)), 4)
            clase = clase.where(rh.gte(20).And(rh.lt(25)), 5)
            clase = clase.where(rh.gte(25).And(rh.lt(30)), 6)
            clase = clase.where(rh.gte(30).And(rh.lt(40)), 7)
            clase = clase.where(rh.gte(40), 8)
            
            extracted = rh.addBands(clase)
            
            date = ee.Date(img.get('system:time_start'))
            year = date.get('year')
            month = date.get('month')
            dStr = date.format('YYYY-MM-dd')
            
            # Sampleo a 25m (resolución nativa de la grilla de este asset GEDI)
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

        points_fc = gedi_col.map(sample_image).flatten()
        
        feedback.setProgress(35)
        feedback.pushInfo("Calculando frecuencias de clases...")
        
        try:
            histogram = points_fc.reduceColumns(
                reducer=ee.Reducer.frequencyHistogram(),
                selectors=['Clase']
            ).getInfo()
            
            freq_dict = histogram.get('histogram', {})
            total_fps = sum(freq_dict.values())
            
            rangos = {
                1: ('< 5', 'Vegetación baja, pastizales, cultivos, arbustos'),
                2: ('5 - 10', 'Regeneración temprana, matorrales altos'),
                3: ('10 - 15', 'Bosque secundario joven'),
                4: ('15 - 20', 'Bosque secundario intermedio'),
                5: ('20 - 25', 'Bosque en recuperación avanzada'),
                6: ('25 - 30', 'Bosque maduro'),
                7: ('30 - 40', 'Bosque alto y bien conservado'),
                8: ('> 40', 'Bosque muy alto o bosque primario denso')
            }
            
            stats_list = []
            for c_id in range(1, 9):
                count = freq_dict.get(str(c_id), 0)
                if count == 0:
                    count = freq_dict.get(c_id, 0)
                    
                pct = (count / total_fps * 100) if total_fps > 0 else 0
                rango, desc = rangos[c_id]
                stats_list.append({
                    'Clase': c_id,
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
                
            # Generar gráfico PNG profesional con matplotlib
            try:
                import matplotlib.pyplot as plt
                
                rangos_labels = [row['Rango'] for row in stats_list]
                frecuencias = [row['Frecuencia'] for row in stats_list]
                
                # Estilo profesional (fallback a ggplot si el de seaborn no existe)
                if 'seaborn-v0_8-whitegrid' in plt.style.available:
                    plt.style.use('seaborn-v0_8-whitegrid')
                elif 'seaborn-whitegrid' in plt.style.available:
                    plt.style.use('seaborn-whitegrid')
                else:
                    plt.style.use('ggplot')
                
                fig, ax = plt.subplots(figsize=(10, 6))
                
                # Crear barras con un color verde profesional
                bars = ax.bar(rangos_labels, frecuencias, color='#2ca25f', edgecolor='#005a32', linewidth=1.2)
                
                # Títulos y etiquetas
                ax.set_title('Frecuencia de Footprints por Rango de Altura del Dosel', fontsize=14, fontweight='bold', pad=20, color='#333333')
                ax.set_xlabel('Rango de Altura (m)', fontsize=12, fontweight='bold', labelpad=12, color='#333333')
                ax.set_ylabel('Número de Footprints', fontsize=12, fontweight='bold', labelpad=12, color='#333333')
                
                # Añadir las etiquetas de valor sobre las barras
                max_frec = max(frecuencias) if frecuencias else 1
                for bar in bars:
                    yval = bar.get_height()
                    if yval > 0:
                        ax.text(bar.get_x() + bar.get_width()/2, yval + (max_frec*0.015), 
                                f'{int(yval)}', ha='center', va='bottom', fontsize=10, fontweight='bold', color='#111111')
                
                # Ajustes de ejes
                plt.xticks(rotation=45, ha='right', fontsize=11)
                plt.yticks(fontsize=11)
                
                # Remover bordes superior y derecho para un aspecto más limpio
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
                desc_dict = ee.Dictionary({
                    '1': 'Vegetación baja, pastizales, cultivos, arbustos',
                    '2': 'Regeneración temprana, matorrales altos',
                    '3': 'Bosque secundario joven',
                    '4': 'Bosque secundario intermedio',
                    '5': 'Bosque en recuperación avanzada',
                    '6': 'Bosque maduro',
                    '7': 'Bosque alto y bien conservado',
                    '8': 'Bosque muy alto o bosque primario denso'
                })
                rango_dict = ee.Dictionary({
                    '1': '< 5',
                    '2': '5 - 10',
                    '3': '10 - 15',
                    '4': '15 - 20',
                    '5': '20 - 25',
                    '6': '25 - 30',
                    '7': '30 - 40',
                    '8': '> 40'
                })
                return f.set({
                    'rango': rango_dict.get(c_str, 'Desconocido'),
                    'descrip': desc_dict.get(c_str, 'Desconocido')
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
                # Descargamos GeoJSON a memoria temporalmente
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
                        QgsField("rango", QMETATYPE_STRING),
                        QgsField("descrip", QMETATYPE_STRING)
                    ])
                    vlayer.updateFields()
                    
                    idx_rango = vlayer.fields().indexOf("rango")
                    idx_desc = vlayer.fields().indexOf("descrip")
                    
                    vlayer.startEditing()
                    for f in vlayer.getFeatures():
                        c_id = int(f['Clase']) if f['Clase'] else 0
                        rango_val, desc_val = ("Desconocido", "Desconocido")
                        for row in stats_list:
                            if row['Clase'] == c_id:
                                rango_val = row['Rango']
                                desc_val = row['Descripción']
                                break
                        vlayer.changeAttributeValue(f.id(), idx_rango, rango_val)
                        vlayer.changeAttributeValue(f.id(), idx_desc, desc_val)
                    vlayer.commitChanges()

                    # Aplicar simbología graduada (amarillo a verde oscuro)
                    myRangeList = []
                    ranges = [
                        (0, 10, QColor('#ffff00'), '0 - 10 m'),
                        (10, 20, QColor('#80ff00'), '10 - 20 m'),
                        (20, 30, QColor('#008000'), '20 - 30 m'),
                        (30, 100, QColor('#004000'), '> 30 m')
                    ]
                    for r in ranges:
                        sym = QgsSymbol.defaultSymbol(vlayer.geometryType())
                        sym.setColor(r[2])
                        sym.symbolLayer(0).setStrokeColor(r[2])
                        sym.setSize(1.5)
                        myRangeList.append(QgsRendererRange(r[0], r[1], sym, r[3]))
                    
                    renderer = QgsGraduatedSymbolRenderer(producto_nombre, myRangeList)
                    renderer.setMode(QgsGraduatedSymbolRenderer.Custom)
                    vlayer.setRenderer(renderer)
                    vlayer.triggerRepaint()
                    
                    QgsProject.instance().addMapLayer(vlayer)
                    
                    try:
                        os.remove(geojson_file)
                    except:
                        pass
                
                return {self.OUT_FOLDER: out_dir}
            
            except Exception:
                feedback.pushInfo("El área generó demasiados puntos para descarga directa. Exportando a Drive...")
                
                def add_ee_strings(f):
                    c_str = ee.Number(f.get('Clase')).format('%d')
                    desc_dict = ee.Dictionary({
                        '1': 'Vegetación baja, pastizales, cultivos, arbustos',
                        '2': 'Regeneración temprana, matorrales altos',
                        '3': 'Bosque secundario joven',
                        '4': 'Bosque secundario intermedio',
                        '5': 'Bosque en recuperación avanzada',
                        '6': 'Bosque maduro',
                        '7': 'Bosque alto y bien conservado',
                        '8': 'Bosque muy alto o bosque primario denso'
                    })
                    rango_dict = ee.Dictionary({
                        '1': '< 5',
                        '2': '5 - 10',
                        '3': '10 - 15',
                        '4': '15 - 20',
                        '5': '20 - 25',
                        '6': '25 - 30',
                        '7': '30 - 40',
                        '8': '> 40'
                    })
                    return f.set({
                        'rango': rango_dict.get(c_str, 'Desconocido'),
                        'descrip': desc_dict.get(c_str, 'Desconocido')
                    })
                    
                export_table(
                    method=ExportMethod.DRIVE, collection=points_fc.map(add_ee_strings),
                    description=out_filename, file_format='SHP',
                    drive_folder='GEE_Bosques_GEDI', feedback=feedback,
                )
                feedback.reportError(f"NOTA: Exceso de puntos. El archivo Shapefile '{out_filename}' fue enviado a Google Drive.", fatalError=False)
                return {self.OUT_FOLDER: out_dir}
