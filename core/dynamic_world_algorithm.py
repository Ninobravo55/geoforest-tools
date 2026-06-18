import os
import json
import ee
import requests
import pandas as pd
from qgis.core import QgsRasterLayer, QgsColorRampShader, QgsRasterShader, QgsSingleBandPseudoColorRenderer
from qgis.PyQt.QtGui import QColor

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
    QgsProcessingParameterDateTime
)
from qgis.PyQt.QtCore import QCoreApplication, QDateTime

from ..utils.aoi_builder import build_aoi, to_ee_geometry, safe_simplify
from ..utils.gee_init import ensure_gee_initialized
from ..utils.export_router import export_image, ExportMethod

class DynamicWorldAlgorithm(QgsProcessingAlgorithm):
    INPUT_LAYER = 'INPUT_LAYER'
    INPUT_EXTENT = 'INPUT_EXTENT'
    START_DATE = 'START_DATE'
    END_DATE = 'END_DATE'
    FILTER_METHOD = 'FILTER_METHOD'
    EPSG = 'EPSG'
    EXPORT_METHOD = 'EXPORT_METHOD'
    OUT_FOLDER = 'OUT_FOLDER'
    GCS_BUCKET = 'GCS_BUCKET'

    def __init__(self):
        super().__init__()
        self.export_methods = [
            "Descarga Directa (Raster)",
            "Exportar a Google Drive",
            "Exportar a Google Cloud Storage (GCS)"
        ]
        self.filter_methods = [
            "Mayor área válida (Menos nubes)",
            "Imagen más reciente"
        ]

    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    def createInstance(self):
        return DynamicWorldAlgorithm()

    def name(self):
        return 'dynamic_world_analysis'

    def displayName(self):
        return self.tr('Dinámica cobertura mundial S2')

    def group(self):
        return self.tr('Dinámica de cobertura')

    def groupId(self):
        return 'dinamica_cobertura'

    def shortHelpString(self):
        help_text = (
            "<b>Descripción del Proceso:</b><br>"
            "Dynamic World es un conjunto de datos de cobertura y uso del suelo (LULC) global y casi en tiempo real (NRT) "
            "con una resolución de 10 m que incluye probabilidades de clase y etiquetas informativas para nueve clases. "
            "Las predicciones de Dynamic World están disponibles para la colección L1C de Sentinel-2 desde el 27/6/2015 "
            "hasta la actualidad. La frecuencia de revisita de Sentinel-2 es de entre 2 y 5 días, según la latitud. "
            "Las predicciones de Dynamic World se generan para las imágenes L1C de Sentinel-2 con CLOUDY_PIXEL_PERCENTAGE <= 35%.<br><br>"
            "<b>Fuente y Citación:</b><br>"
            "<a href='https://developers.google.com/earth-engine/datasets/catalog/GOOGLE_DYNAMICWORLD_V1'>https://developers.google.com/earth-engine/datasets/catalog/GOOGLE_DYNAMICWORLD_V1</a>"
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
        
        default_end = QDateTime.currentDateTime()
        default_start = default_end.addDays(-30)
        
        self.addParameter(QgsProcessingParameterDateTime(self.START_DATE, self.tr('Fecha de Inicio'), type=QgsProcessingParameterDateTime.Date, defaultValue=default_start))
        self.addParameter(QgsProcessingParameterDateTime(self.END_DATE, self.tr('Fecha de Fin'), type=QgsProcessingParameterDateTime.Date, defaultValue=default_end))
        
        self.addParameter(QgsProcessingParameterEnum(self.FILTER_METHOD, self.tr('Método de Selección de Imagen'), options=self.filter_methods, defaultValue=0))
        self.addParameter(QgsProcessingParameterCrs(self.EPSG, self.tr('CRS de Destino'), defaultValue='EPSG:32718', optional=True))
        self.addParameter(QgsProcessingParameterEnum(self.EXPORT_METHOD, self.tr('Método de Exportación Raster'), options=self.export_methods, defaultValue=0))
        self.addParameter(QgsProcessingParameterFolderDestination(self.OUT_FOLDER, self.tr('Carpeta Local de Destino (Obligatoria para Estadísticas Excel)'), optional=False))
        self.addParameter(QgsProcessingParameterString(self.GCS_BUCKET, self.tr('Nombre del Bucket GCS (Solo para GCS)'), optional=True))

    def processAlgorithm(self, parameters, context, feedback):
        start_date = self.parameterAsDateTime(parameters, self.START_DATE, context).toString('yyyy-MM-dd')
        end_date = self.parameterAsDateTime(parameters, self.END_DATE, context).toString('yyyy-MM-dd')
        filter_idx = self.parameterAsEnum(parameters, self.FILTER_METHOD, context)
        crs_dest = self.parameterAsCrs(parameters, self.EPSG, context)
        target_crs_str = crs_dest.authid() if crs_dest.isValid() else 'EPSG:4326'
        export_method_idx = self.parameterAsEnum(parameters, self.EXPORT_METHOD, context)
        out_folder = self.parameterAsString(parameters, self.OUT_FOLDER, context)
        gcs_bucket = self.parameterAsString(parameters, self.GCS_BUCKET, context)
        
        out_dir = out_folder if out_folder else ""
        if not out_dir:
            raise QgsProcessingException("Debe seleccionar una Carpeta Local de Destino para guardar el reporte Excel.")
            
        # AOI centralizado (utils/aoi_builder)
        aoi = build_aoi(self, parameters, context, feedback)
        geom_union = aoi.geom_4326
            
        geom_union_simp = safe_simplify(geom_union, 0.0001)
        geo_dict = json.loads(geom_union_simp.asJson())
        
        ensure_gee_initialized(feedback)
                
        # Geometría EE sin truncar GeometryCollection — fix B-01
        geom_ee_exact = to_ee_geometry(geom_union_simp, ee, geo_dict)
            
        feedback.pushInfo("Aplicando Buffer INTERNO de seguridad en GEE de 20m...")
        geom_ee_buffer = geom_ee_exact.buffer(20, 1)
        
        feedback.setProgress(10)
        
        feedback.pushInfo(f"Filtrando Colección Dynamic World entre {start_date} y {end_date}...")
        dwCol = ee.ImageCollection('GOOGLE/DYNAMICWORLD/V1') \
            .filterDate(start_date, end_date) \
            .filterBounds(geom_ee_buffer)
            
        try:
            count = dwCol.size().getInfo()
            if count == 0:
                raise QgsProcessingException("No se encontraron imágenes en el rango de fechas y área seleccionados.")
        except Exception as e:
            if "No se encontraron imágenes" in str(e):
                raise e
        
        if filter_idx == 0:
            feedback.pushInfo("Seleccionando la imagen con mayor área válida (Menos nubes)...")
            def calculate_valid_area(img):
                valid = img.select('label').mask()
                area_valida = valid.multiply(ee.Image.pixelArea()).reduceRegion(
                    reducer=ee.Reducer.sum(),
                    geometry=geom_ee_buffer,
                    scale=10,
                    maxPixels=1e13
                ).get('label')
                return img.set('area_valida', area_valida)
                
            dwCoverage = dwCol.map(calculate_valid_area)
            bestImage = ee.Image(dwCoverage.sort('area_valida', False).first())
        else:
            feedback.pushInfo("Seleccionando la imagen más reciente...")
            bestImage = ee.Image(dwCol.sort('system:time_start', False).first())
            
        bestImage = bestImage.clip(geom_ee_buffer)
        
        try:
            # Get the date of the selected image
            fecha = ee.Date(bestImage.get('system:time_start')).format('YYYYMMdd').getInfo()
            feedback.pushInfo(f"Imagen seleccionada de fecha: {fecha}")
        except:
            fecha = "Reciente"
            feedback.pushInfo("No se pudo extraer la fecha exacta de la imagen.")
            
        out_filename = f"DynamicWorld_{fecha}"
        
        img_label = bestImage.select('label')
        
        feedback.setProgress(30)
        feedback.pushInfo("Calculando Áreas por Clase en GEE...")
        
        try:
            area_image = ee.Image.pixelArea().addBands(img_label)
            
            stats = area_image.reduceRegion(
                reducer=ee.Reducer.sum().group(groupField=1, groupName='class'),
                geometry=geom_ee_buffer,
                scale=10,
                maxPixels=1e13
            ).getInfo()
            
            groups = stats.get('groups', [])
            
            class_names = {
                0: 'Agua',
                1: 'Bosque',
                2: 'Pastizal',
                3: 'Vegetación inundada',
                4: 'Cultivos',
                5: 'Matorral y arbustos',
                6: 'Área construida',
                7: 'Suelo desnudo',
                8: 'Nieve y hielo'
            }
            
            areas_ha = {k: 0.0 for k in class_names.keys()}
            for g in groups:
                c = int(g['class'])
                if c in areas_ha:
                    areas_ha[c] = g['sum'] / 10000.0
                    
            excel_data = {
                "Valor Raster (Clase)": list(class_names.keys()),
                "Clase DW": list(class_names.values()),
                "Área (Hectáreas)": [areas_ha[k] for k in class_names.keys()]
            }
            df = pd.DataFrame(excel_data)
            excel_path = os.path.join(out_dir, f"{out_filename}_Areas.xlsx")
            df.to_excel(excel_path, index=False)
            feedback.pushInfo(f"Archivo Excel de Áreas generado exitosamente: {excel_path}")
            
        except Exception as stat_err:
            feedback.reportError(f"Ocurrió un error al calcular áreas en GEE: {str(stat_err)}", fatalError=False)
            
        feedback.setProgress(60)
        img_for_export = img_label.toByte()
        
        if export_method_idx != 0:
            export_image(
                method=export_method_idx, image=img_for_export,
                description=out_filename, region=geom_ee_buffer, scale=10,
                crs=target_crs_str, drive_folder='DynamicWorld',
                gcs_bucket=gcs_bucket, fileNamePrefix=out_filename,
                feedback=feedback,
            )
            return {self.OUT_FOLDER: out_dir}
        else:
            feedback.pushInfo("Descargando Raster desde GEE...")
            try:
                url = img_for_export.getDownloadURL({
                    'name': out_filename,
                    'crs': target_crs_str,
                    'scale': 10,
                    'region': geom_ee_buffer,
                    'format': 'GEO_TIFF'
                })
                r = requests.get(url, stream=True)
                r.raise_for_status()
                
                tif_file = os.path.join(out_dir, f"{out_filename}.tif")
                with open(tif_file, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=1024):
                        if chunk: f.write(chunk)
                        
                if not os.path.exists(tif_file): raise QgsProcessingException("Error al guardar el archivo TIFF.")
                
                feedback.pushInfo("Cargando Raster a QGIS y aplicando Simbología...")
                
                rlayer = QgsRasterLayer(tif_file, out_filename)
                if rlayer.isValid():
                    fnc = QgsColorRampShader()
                    fnc.setColorRampType(QgsColorRampShader.Exact)
                    lst = [
                        QgsColorRampShader.ColorRampItem(0, QColor('#419bdf'), 'Agua'),
                        QgsColorRampShader.ColorRampItem(1, QColor('#397d49'), 'Bosque'),
                        QgsColorRampShader.ColorRampItem(2, QColor('#88b053'), 'Pastizal'),
                        QgsColorRampShader.ColorRampItem(3, QColor('#7a87c6'), 'Vegetación inundada'),
                        QgsColorRampShader.ColorRampItem(4, QColor('#e49635'), 'Cultivos'),
                        QgsColorRampShader.ColorRampItem(5, QColor('#dfc35a'), 'Matorral y arbustos'),
                        QgsColorRampShader.ColorRampItem(6, QColor('#c4281b'), 'Área construida'),
                        QgsColorRampShader.ColorRampItem(7, QColor('#a59b8f'), 'Suelo desnudo'),
                        QgsColorRampShader.ColorRampItem(8, QColor('#b39fe1'), 'Nieve y hielo')
                    ]
                    fnc.setColorRampItemList(lst)
                    shader = QgsRasterShader()
                    shader.setRasterShaderFunction(fnc)
                    renderer = QgsSingleBandPseudoColorRenderer(rlayer.dataProvider(), 1, shader)
                    renderer.setClassificationMin(0)
                    renderer.setClassificationMax(8)
                    rlayer.setRenderer(renderer)
                    rlayer.triggerRepaint()
                    
                    QgsProject.instance().addMapLayer(rlayer)
                    
                return {self.OUT_FOLDER: out_dir}
            except Exception as ee_err:
                feedback.pushInfo(f"Área demasiado grande para raster directo. Exportando a Drive... {str(ee_err)}")
                export_image(
                    method=ExportMethod.DRIVE, image=img_for_export,
                    description=out_filename, region=geom_ee_buffer, scale=10,
                    crs=target_crs_str, drive_folder='DynamicWorld',
                    fileNamePrefix=out_filename, feedback=feedback,
                )
                feedback.pushInfo(f"El área es demasiado grande para descarga directa a 10m. Exportación a Drive iniciada: {out_filename}")
                feedback.reportError(f"NOTA: Área inmensa. El raster '{out_filename}' fue enviado a Google Drive.", fatalError=False)
                return {self.OUT_FOLDER: out_dir}
