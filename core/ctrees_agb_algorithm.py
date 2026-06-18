import os
import csv
import ee
import requests
import processing
from qgis.core import (
    QgsProcessing, QgsProcessingAlgorithm, QgsProcessingParameterExtent,
    QgsProcessingParameterEnum, QgsProcessingParameterCrs,
    QgsProcessingParameterFolderDestination, QgsProcessingParameterString,
    QgsProcessingParameterNumber, QgsProcessingException, QgsProject,
    QgsProcessingParameterMapLayer, QgsRasterLayer
)
from qgis.PyQt.QtCore import QCoreApplication
import numpy as np

from ..utils.aoi_builder import build_aoi, to_ee_geometry
from ..utils.gee_init import ensure_gee_initialized

class CtreesAgbAlgorithm(QgsProcessingAlgorithm):
    INPUT_LAYER = 'INPUT_LAYER'
    INPUT_EXTENT = 'INPUT_EXTENT'
    YEAR = 'YEAR'
    EPSG = 'EPSG'
    EXPORT_METHOD = 'EXPORT_METHOD'
    OUT_FOLDER = 'OUT_FOLDER'
    GCS_BUCKET = 'GCS_BUCKET'

    def __init__(self):
        super().__init__()
        self.export_methods = [
            "Descarga Directa (QGIS)",
            "Exportar a Google Drive",
            "Exportar a Google Cloud Storage (GCS)"
        ]

    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    def createInstance(self):
        return CtreesAgbAlgorithm()

    def name(self):
        return 'ctrees_agb_analysis'

    def displayName(self):
        return self.tr('CTREES - Biomasa Global Suelo 100m')

    def group(self):
        return self.tr('Biomasa Forestal Global')

    def groupId(self):
        return 'biomasa_forestal_global'

    def shortHelpString(self):
        help_text = (
            "<b>Biomasa aérea global de CTrees a 100 m (2000–2025)</b><br><br>"
            "La estimación precisa y de alta resolución de la biomasa aérea forestal (AGB) es fundamental "
            "para cuantificar las reservas de carbono terrestres y fundamentar las políticas de mitigación del cambio climático. "
            "Sin embargo, los productos globales existentes siguen limitados por una resolución espacial baja y una cobertura "
            "temporal reducida. Este conjunto de datos de CTrees presenta un producto global de AGB de cobertura total con una "
            "resolución espacial nativa de 100 m que abarca el período 2000–2025, entrenado con una arquitectura de aprendizaje "
            "profundo DenseNet que utiliza etiquetas de referencia de AGB independientes derivadas de AGB obtenidas mediante ALS, "
            "muestras de parcelas de campo e inventarios forestales de la comunidad científica, inventarios regionales y nacionales "
            "seleccionados, y muestras de entrenamiento de AGB de manglares de regiones costeras globales.<br><br>"
            "El conjunto predictor integra series temporales de reflectancia superficial de Landsat, retrodispersión de banda L de "
            "ALOS PALSAR-½, métricas de altura del dosel de GEDI e ICESat-2 (utilizadas únicamente como capas estructurales auxiliares) "
            "y datos topográficos. En un conjunto de validación independiente de aproximadamente 1,01 millones de píxeles recopilados "
            "en ecorregiones, el producto alcanza un R² = 0,741, un RMSE = 59,5 Mg ha⁻¹ y un sesgo = −4,82 Mg ha⁻¹.<br><br>"
            "Para obtener más detalles sobre el conjunto de datos y la metodología subyacente, consulte el "
            "Informe de Datos de Carbono CTrees AGB.<br><br>"
            "<b>Cálculos Incluidos en el Polígono:</b><br>"
            "<ul>"
            "<li><b>Biomasa (AGB):</b> Extraída de la banda AGB (Mg/ha).</li>"
            "<li><b>Carbono:</b> AGB × 0.5 (MgC/ha).</li>"
            "<li><b>CO₂ Equivalente:</b> Carbono × 3.667 (tCO₂e/ha).</li>"
            "</ul><br>"
            "<b>Información y Fuente de Datos Oficial:</b><br>"
            "Fuente: <a href='https://gee-community-catalog.org/projects/ctrees_global/'>https://gee-community-catalog.org/projects/ctrees_global/</a>"
        )
        return self.tr(help_text)

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterMapLayer(self.INPUT_LAYER, self.tr('Opción 1: Seleccionar Capa Vectorial (Área de Interés)'), [QgsProcessing.TypeVectorPolygon], optional=True))
        self.addParameter(QgsProcessingParameterExtent(self.INPUT_EXTENT, self.tr('Opción 2: O dibujar un Recuadro Extensión'), optional=True))
        self.addParameter(QgsProcessingParameterNumber(self.YEAR, self.tr('Año a consultar'), type=QgsProcessingParameterNumber.Integer, defaultValue=2025))
        self.addParameter(QgsProcessingParameterCrs(self.EPSG, self.tr('CRS de Destino (Ej. UTM)'), defaultValue='EPSG:32718', optional=True))
        self.addParameter(QgsProcessingParameterEnum(self.EXPORT_METHOD, self.tr('Método de Exportación'), options=self.export_methods, defaultValue=0))
        self.addParameter(QgsProcessingParameterFolderDestination(self.OUT_FOLDER, self.tr('Carpeta Local de Destino'), optional=False))
        self.addParameter(QgsProcessingParameterString(self.GCS_BUCKET, self.tr('Nombre del Bucket GCS (Solo para GCS)'), optional=True))

    def processAlgorithm(self, parameters, context, feedback):
        year = self.parameterAsInt(parameters, self.YEAR, context)
        target_crs = self.parameterAsCrs(parameters, self.EPSG, context)
        target_crs_str = target_crs.authid()
        export_method_idx = self.parameterAsEnum(parameters, self.EXPORT_METHOD, context)
        out_folder = self.parameterAsString(parameters, self.OUT_FOLDER, context)
        gcs_bucket = self.parameterAsString(parameters, self.GCS_BUCKET, context)

        if not out_folder:
            raise QgsProcessingException("Debe seleccionar una Carpeta Local de Destino.")
            
        ensure_gee_initialized(feedback)

        # AOI centralizado (utils/aoi_builder)
        aoi = build_aoi(self, parameters, context, feedback)
        geom_union = aoi.geom_4326
        geo_dict = aoi.geo_dict

        # Geometría EE sin truncar GeometryCollection — fix B-01
        geom_ee_exact = to_ee_geometry(geom_union, ee, geo_dict)

        feedback.setProgress(10)
        feedback.pushInfo(f"Consultando Colección CTREES para el año {year}...")

        collection = ee.ImageCollection('projects/sat-io/open-datasets/CTREES-GLOBAL-AGB-100M')
        image = collection.filterDate(f'{year}-01-01', f'{year}-12-31').first()

        # Obtener factor de escala de biomasa (El usuario indicó que es 0.1)
        scale_factor = 0.1
        
        # Biomasa (AGB)
        agb = image.select('agb').multiply(scale_factor).rename('agb').clip(geom_ee_exact)

        # Enmascaramos <= 0 e -inf antes de descargar para que se vuelvan verdaderos NoData
        agb = agb.updateMask(agb.gt(0))
        agb = agb.unmask(-9999).toFloat()

        out_prefix = f"CTREES_{year}"
        
        if export_method_idx != 0:
            # Exportación a Drive/GCS centralizada (utils/export_router)
            from ..utils.export_router import export_image
            export_image(
                method=export_method_idx,
                image=agb,
                description=f"{out_prefix}_AGB",
                region=geom_ee_exact,
                scale=100,
                drive_folder='GEE_Bosques_CTREES',
                gcs_bucket=gcs_bucket,
                file_format='GeoTIFF',
                feedback=feedback,
            )
            return {self.OUT_FOLDER: out_folder}

        # Método 0: Descarga Directa
        feedback.pushInfo("Calculando y Descargando Raster AGB en Geográfico (EPSG:4326)...")
        try:
            url_agb = agb.getDownloadURL({
                'scale': 100,
                'crs': 'EPSG:4326',
                'region': geom_ee_exact,
                'format': 'GEO_TIFF'
            })
            r_agb = requests.get(url_agb, stream=True)
            r_agb.raise_for_status()
            
            tif_download = os.path.join(out_folder, f"{out_prefix}_AGB_raw.tif")
                
            with open(tif_download, 'wb') as f:
                for chunk in r_agb.iter_content(chunk_size=8192):
                    if chunk: f.write(chunk)
            
            feedback.setProgress(40)
            
            tif_agb = os.path.join(out_folder, f"{out_prefix}_AGB.tif")
            feedback.pushInfo(f"Asignando NoData (-9999) y proyectando a {target_crs_str}...")
            params_reprj = {
                'INPUT': tif_download,
                'SOURCE_CRS': 'EPSG:4326',
                'TARGET_CRS': target_crs_str,
                'NODATA': -9999,
                'EXTRA': '-srcnodata -9999',
                'OUTPUT': tif_agb
            }
            processing.run("gdal:warpreproject", params_reprj, context=context, feedback=feedback)
            try:
                os.remove(tif_download)
            except Exception:
                pass
                
            agb_layer = QgsRasterLayer(tif_agb, f"{out_prefix}_AGB")
            if not agb_layer.isValid():
                raise QgsProcessingException("El raster descargado no es válido.")
                
            feedback.setProgress(50)
            feedback.pushInfo("Calculando Carbono (AGB * 0.5)...")
            tif_carbon = os.path.join(out_folder, f"{out_prefix}_Carbono.tif")
            
            # Calculadora de Raster - Carbono
            # Si A es > 0 calculamos A*0.5, sino mantenemos -9999
            params_calc_c = {
                'INPUT_A': tif_agb,
                'BAND_A': 1,
                'FORMULA': '(A>0) * (A * 0.5) + (A<=0) * (-9999)',
                'EXTRA': '--NoDataValue=-9999',
                'OUTPUT': tif_carbon
            }
            processing.run("gdal:rastercalculator", params_calc_c, context=context, feedback=feedback)
            
            feedback.setProgress(60)
            feedback.pushInfo("Calculando CO2 Equivalente (AGB * 1.8335)...")
            tif_co2e = os.path.join(out_folder, f"{out_prefix}_CO2e.tif")
            
            # Calculadora de Raster - CO2e (0.5 * 3.667 = 1.8335)
            params_calc_co2 = {
                'INPUT_A': tif_agb,
                'BAND_A': 1,
                'FORMULA': '(A>0) * (A * 1.8335) + (A<=0) * (-9999)',
                'EXTRA': '--NoDataValue=-9999',
                'OUTPUT': tif_co2e
            }
            processing.run("gdal:rastercalculator", params_calc_co2, context=context, feedback=feedback)
            
            feedback.setProgress(70)
            
            # Calcular estadísticas zonales nativas
            feedback.pushInfo("Generando estadísticas...")
            stats_agb = processing.run("native:rasterlayerstatistics", {'INPUT': tif_agb, 'BAND': 1})
            stats_car = processing.run("native:rasterlayerstatistics", {'INPUT': tif_carbon, 'BAND': 1})
            stats_co2 = processing.run("native:rasterlayerstatistics", {'INPUT': tif_co2e, 'BAND': 1})
            
            csv_path = os.path.join(out_folder, f"{out_prefix}_Estadisticas.csv")
            with open(csv_path, 'w', newline='', encoding='utf-8') as csvfile:
                writer = csv.writer(csvfile)
                writer.writerow(['Producto', 'Minimo', 'Maximo', 'Promedio', 'Desviacion_Estandar'])
                writer.writerow(['AGB (Mg/ha)', stats_agb['MIN'], stats_agb['MAX'], stats_agb['MEAN'], stats_agb['STD_DEV']])
                writer.writerow(['Carbono (MgC/ha)', stats_car['MIN'], stats_car['MAX'], stats_car['MEAN'], stats_car['STD_DEV']])
                writer.writerow(['CO2e (tCO2e/ha)', stats_co2['MIN'], stats_co2['MAX'], stats_co2['MEAN'], stats_co2['STD_DEV']])
            
            feedback.setProgress(80)
            feedback.pushInfo("Generando Gráficos (Histogramas)...")
            try:
                import matplotlib.pyplot as plt
                from osgeo import gdal
                
                def get_raster_data(tif_path):
                    ds = gdal.Open(tif_path)
                    band = ds.GetRasterBand(1)
                    data = band.ReadAsArray()
                    nodata = band.GetNoDataValue()
                    if nodata is not None:
                        data = np.ma.masked_equal(data, nodata)
                        # También enmascarar -inf si es que existe
                        data = np.ma.masked_invalid(data)
                        return data.compressed()
                    return data.flatten()
                
                agb_data = get_raster_data(tif_agb)
                car_data = get_raster_data(tif_carbon)
                co2_data = get_raster_data(tif_co2e)
                
                if len(agb_data) > 0:
                    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
                    
                    y_agb, x_agb = np.histogram(agb_data, bins=30)
                    x_agb = (x_agb[:-1] + x_agb[1:]) / 2
                    y_car, x_car = np.histogram(car_data, bins=30)
                    x_car = (x_car[:-1] + x_car[1:]) / 2
                    y_co2, x_co2 = np.histogram(co2_data, bins=30)
                    x_co2 = (x_co2[:-1] + x_co2[1:]) / 2
                    
                    axes[0].bar(x_agb, y_agb, width=(x_agb[1]-x_agb[0])*0.8 if len(x_agb)>1 else 1, color='#31a354', edgecolor='black')
                    axes[0].set_title('Biomasa AGB (Mg/ha)')
                    axes[0].set_xlabel('AGB')
                    axes[0].set_ylabel('Frecuencia')
                    
                    axes[1].bar(x_car, y_agb, width=(x_car[1]-x_car[0])*0.8 if len(x_car)>1 else 1, color='#8856a7', edgecolor='black')
                    axes[1].set_title('Carbono (MgC/ha)')
                    axes[1].set_xlabel('Carbono')
                    
                    axes[2].bar(x_co2, y_agb, width=(x_co2[1]-x_co2[0])*0.8 if len(x_co2)>1 else 1, color='#e34a33', edgecolor='black')
                    axes[2].set_title('CO2 Equivalente (tCO2e/ha)')
                    axes[2].set_xlabel('CO2e')
                    
                    plt.tight_layout()
                    png_path = os.path.join(out_folder, f"{out_prefix}_Histogramas.png")
                    plt.savefig(png_path, dpi=300, bbox_inches='tight', facecolor='white')
                    plt.close(fig)
            except Exception as e:
                feedback.pushInfo(f"Aviso gráfico: No se pudo generar el gráfico ({str(e)}).")

            feedback.setProgress(100)
            
            # Cargar los Rasters Resultantes a QGIS
            from qgis.core import QgsColorRampShader, QgsRasterShader, QgsSingleBandPseudoColorRenderer
            from qgis.PyQt.QtGui import QColor

            def apply_symbology(layer, min_val, max_val, hex_colors):
                fnc = QgsColorRampShader()
                fnc.setColorRampType(QgsColorRampShader.Interpolated)
                lst = []
                lst.append(QgsColorRampShader.ColorRampItem(0, QColor(0,0,0,0), '0'))
                step = (max_val - min_val) / (len(hex_colors) - 1) if len(hex_colors) > 1 else 1
                for i, color in enumerate(hex_colors):
                    val = min_val + (i * step)
                    if val == 0: val = 0.0001
                    lst.append(QgsColorRampShader.ColorRampItem(val, QColor(color), str(round(val, 1))))
                fnc.setColorRampItemList(lst)
                shader = QgsRasterShader()
                shader.setRasterShaderFunction(fnc)
                renderer = QgsSingleBandPseudoColorRenderer(layer.dataProvider(), 1, shader)
                renderer.setClassificationMin(min_val)
                renderer.setClassificationMax(max_val)
                layer.setRenderer(renderer)
                layer.triggerRepaint()
            
            apply_symbology(agb_layer, 0, 400, ['#f7fcf5', '#d9f0d3', '#b2e0a8', '#84ca7e', '#5db96a', '#38a152', '#1f7f3b', '#0d5c26', '#00441b', '#023640', '#042864', '#08306b'])
            QgsProject.instance().addMapLayer(agb_layer)
            
            rl_carbon = QgsRasterLayer(tif_carbon, f"{out_prefix}_Carbono")
            if rl_carbon.isValid():
                apply_symbology(rl_carbon, 0, 200, ['#ffffcc', '#c2e699', '#78c679', '#31a354', '#006837'])
                QgsProject.instance().addMapLayer(rl_carbon)
            
            rl_co2 = QgsRasterLayer(tif_co2e, f"{out_prefix}_CO2e")
            if rl_co2.isValid():
                apply_symbology(rl_co2, 0, 800, ['#ffffcc', '#c2e699', '#78c679', '#31a354', '#006837'])
                QgsProject.instance().addMapLayer(rl_co2)

        except Exception as ee_err:
            feedback.reportError(f"Error en descarga o procesamiento local: {str(ee_err)}", fatalError=False)
            
        return {self.OUT_FOLDER: out_folder}
