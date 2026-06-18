import os
import csv
import ee
import requests
import processing
from qgis.core import (
    QgsProcessing, QgsProcessingAlgorithm, QgsProcessingParameterExtent,
    QgsProcessingParameterEnum, QgsProcessingParameterCrs,
    QgsProcessingParameterFolderDestination, QgsProcessingParameterString,
    QgsProcessingParameterBoolean, QgsProcessingException, QgsProject,
    QgsProcessingParameterMapLayer, QgsVectorLayer, QgsRasterLayer, QgsField
)
from qgis.PyQt.QtCore import QCoreApplication
from ..utils.qt_compat import (
    QMETATYPE_STRING,
    QMETATYPE_DOUBLE,
)

from ..utils.aoi_builder import build_aoi, to_ee_geometry
from ..utils.gee_init import ensure_gee_initialized
from ..utils.export_router import export_image, export_table

class GediL4BAlgorithm(QgsProcessingAlgorithm):
    INPUT_LAYER = 'INPUT_LAYER'
    INPUT_EXTENT = 'INPUT_EXTENT'
    FILTER_QF = 'FILTER_QF'
    FILTER_MU = 'FILTER_MU'
    FILTER_PE = 'FILTER_PE'
    FILTER_NS = 'FILTER_NS'
    FILTER_NC = 'FILTER_NC'
    FILTER_SE = 'FILTER_SE'
    EPSG = 'EPSG'
    EXPORT_METHOD = 'EXPORT_METHOD'
    OUT_FOLDER = 'OUT_FOLDER'
    GCS_BUCKET = 'GCS_BUCKET'

    def __init__(self):
        super().__init__()
        self.export_methods = [
            "Descarga Directa (Raster + Vector GeoPackage .gpkg)",
            "Exportar a Google Drive",
            "Exportar a Google Cloud Storage (GCS)"
        ]

    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    def createInstance(self):
        return GediL4BAlgorithm()

    def name(self):
        return 'gedi_l4b_analysis'

    def displayName(self):
        return self.tr('GEDI L4B - Biomasa Aérea Media (AGBD) 1km')

    def group(self):
        return self.tr('Biomasa Forestal Global')

    def groupId(self):
        return 'biomasa_forestal_global'

    def shortHelpString(self):
        help_text = (
            "<b>Descripción del Proceso:</b><br>"
            "El producto GEDI Level 4B (GEDI L4B) proporciona una superficie continua agregada de biomasa aérea a 1 km "
            "de resolución, construida a partir de los footprints L4A (periodo 2019-2021). No son puntos, sino una grilla Raster.<br><br>"
            "<b>Filtros aplicados a la extracción:</b><br>"
            "Calidad GEDI (QF=1), Biomasa Positiva (MU>0), Error Porcentual (PE<=50), Número de Muestras (NS>=10), "
            "Número de Trayectorias (NC>=2) y Error Estándar (SE<=50 Mg/ha).<br><br>"
            "<b>Cálculos Incluidos en el Polígono:</b><br>"
            "<ul>"
            "<li><b>MU Promedio:</b> Biomasa media de la zona clasificada (Mg/ha).</li>"
            "<li><b>Biomasa Total:</b> MU Promedio × Área en ha (Mg).</li>"
            "<li><b>Carbono Total:</b> Biomasa Total × 0.47 (MgC).</li>"
            "<li><b>CO₂ Equivalente:</b> Carbono Total × 44/12 (tCO₂e).</li>"
            "</ul><br>"
            "<b>Información y Fuente de Datos Oficial:</b><br>"
            "Los datos provienen de: <a href='https://developers.google.com/earth-engine/datasets/catalog/LARSE_GEDI_GEDI04_B_002?hl'>LARSE/GEDI/GEDI04_B_002</a>."
        )
        return self.tr(help_text)

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterMapLayer(self.INPUT_LAYER, self.tr('Opción 1: Seleccionar Capa Vectorial (Área de Interés)'), [QgsProcessing.TypeVectorPolygon], optional=True))
        self.addParameter(QgsProcessingParameterExtent(self.INPUT_EXTENT, self.tr('Opción 2: O dibujar un Recuadro Extensión'), optional=True))
        
        # Filters
        self.addParameter(QgsProcessingParameterBoolean(self.FILTER_QF, self.tr('Calidad GEDI (QF = 1)'), defaultValue=True))
        self.addParameter(QgsProcessingParameterBoolean(self.FILTER_MU, self.tr('Biomasa positiva (MU > 0)'), defaultValue=True))
        self.addParameter(QgsProcessingParameterBoolean(self.FILTER_PE, self.tr('Error porcentual <= 50% (PE <= 50)'), defaultValue=False))
        self.addParameter(QgsProcessingParameterBoolean(self.FILTER_NS, self.tr('Número de muestras >= 10 (NS >= 10)'), defaultValue=False))
        self.addParameter(QgsProcessingParameterBoolean(self.FILTER_NC, self.tr('Número de trayectorias >= 2 (NC >= 2)'), defaultValue=False))
        self.addParameter(QgsProcessingParameterBoolean(self.FILTER_SE, self.tr('Error estándar <= 50 Mg/ha (SE <= 50)'), defaultValue=False))
        
        self.addParameter(QgsProcessingParameterCrs(self.EPSG, self.tr('CRS de Destino (Ej. UTM)'), defaultValue='EPSG:32718', optional=True))
        self.addParameter(QgsProcessingParameterEnum(self.EXPORT_METHOD, self.tr('Método de Exportación'), options=self.export_methods, defaultValue=0))
        self.addParameter(QgsProcessingParameterFolderDestination(self.OUT_FOLDER, self.tr('Carpeta Local de Destino'), optional=False))
        self.addParameter(QgsProcessingParameterString(self.GCS_BUCKET, self.tr('Nombre del Bucket GCS (Solo para GCS)'), optional=True))

    def processAlgorithm(self, parameters, context, feedback):
        f_qf = self.parameterAsBoolean(parameters, self.FILTER_QF, context)
        f_mu = self.parameterAsBoolean(parameters, self.FILTER_MU, context)
        f_pe = self.parameterAsBoolean(parameters, self.FILTER_PE, context)
        f_ns = self.parameterAsBoolean(parameters, self.FILTER_NS, context)
        f_nc = self.parameterAsBoolean(parameters, self.FILTER_NC, context)
        f_se = self.parameterAsBoolean(parameters, self.FILTER_SE, context)
        
        target_crs = self.parameterAsCrs(parameters, self.EPSG, context)
        target_crs_str = target_crs.authid() if target_crs.isValid() else 'EPSG:4326'
        
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
        
        img = ee.Image('LARSE/GEDI/GEDI04_B_002')
        
        if f_qf: img = img.updateMask(img.select('QF').eq(1))
        if f_mu: img = img.updateMask(img.select('MU').gt(0))
        if f_pe: img = img.updateMask(img.select('PE').lte(50))
        if f_ns: img = img.updateMask(img.select('NS').gte(10))
        if f_nc: img = img.updateMask(img.select('NC').gte(2))
        if f_se: img = img.updateMask(img.select('SE').lte(50))
        
        img = img.clip(geom_ee_exact)
        mu = img.select('MU').rename('Biomasa_MU')
        
        clase = ee.Image(0).rename('Clase')
        clase = clase.where(mu.lt(20), 1)
        clase = clase.where(mu.gte(20).And(mu.lt(50)), 2)
        clase = clase.where(mu.gte(50).And(mu.lt(100)), 3)
        clase = clase.where(mu.gte(100).And(mu.lt(150)), 4)
        clase = clase.where(mu.gte(150).And(mu.lt(200)), 5)
        clase = clase.where(mu.gte(200).And(mu.lt(250)), 6)
        clase = clase.where(mu.gte(250).And(mu.lt(300)), 7)
        clase = clase.where(mu.gte(300), 8)
        
        mu_mask = mu.gt(0)
        mu = mu.updateMask(mu_mask)
        clase = clase.updateMask(mu_mask)
        
        feedback.setProgress(30)
        feedback.pushInfo("Generando Polígonos de Clases de Biomasa en GEE (reduceToVectors)...")
        
        polygons = clase.reduceToVectors(
            geometry=geom_ee_exact,
            crs=img.projection(),
            scale=1000,
            geometryType='polygon',
            eightConnected=False,
            labelProperty='Clase'
        )
        
        poly_stats = mu.reduceRegions(
            collection=polygons,
            reducer=ee.Reducer.mean(),
            scale=1000
        )
        
        def process_poly(f):
            return f.set({'MU_prom': f.get('mean')})
            
        vector_final = poly_stats.map(process_poly)
        
        out_filename = "GEDIL4B_BiomasaAgregada"
        
        if export_method_idx != 0:
            export_vec = vector_final
            
            # crs=None preserva la proyección nativa (el código original
            # omitía crs en el export); maxPixels=1e10 como en el original.
            export_image(
                method=export_method_idx, image=mu,
                description=f"{out_filename}_MU", region=geom_ee_exact,
                scale=1000, crs=None, drive_folder='GEE_Bosques_GEDI',
                gcs_bucket=gcs_bucket, max_pixels=int(1e10), feedback=feedback,
            )
            export_table(
                method=export_method_idx, collection=export_vec,
                description=f"{out_filename}_Poligonos", file_format='SHP',
                drive_folder='GEE_Bosques_GEDI', gcs_bucket=gcs_bucket,
                feedback=feedback,
            )
            destino = "Google Drive" if export_method_idx == 1 else "GCS"
            feedback.pushInfo(f"Tareas (Raster y Vector) enviadas a {destino}.")
            return {self.OUT_FOLDER: out_folder}
            
        else:
            feedback.pushInfo("Descargando Rasters de Biomasa (MU)...")
            try:
                url_mu = mu.getDownloadURL({'scale': 1000, 'crs': 'EPSG:4326', 'region': geom_ee_exact, 'format': 'GEO_TIFF'})
                r_mu = requests.get(url_mu, stream=True)
                r_mu.raise_for_status()
                
                tif_mu = os.path.join(out_folder, f"{out_filename}_MU.tif")
                with open(tif_mu, 'wb') as f:
                    for chunk in r_mu.iter_content(chunk_size=8192):
                        if chunk: f.write(chunk)
                
                feedback.pushInfo("Descargando Vector de Clases de Biomasa (GeoJSON)...")
                url_vec = vector_final.getDownloadURL(filetype='geojson')
                r_vec = requests.get(url_vec, stream=True)
                r_vec.raise_for_status()
                geojson_file = os.path.join(out_folder, f"{out_filename}.geojson")
                with open(geojson_file, 'wb') as f:
                    for chunk in r_vec.iter_content(chunk_size=8192):
                        if chunk: f.write(chunk)
                        
                feedback.setProgress(70)
                feedback.pushInfo(f"Reproyectando Vector a {target_crs_str} y calculando atributos...")
                
                gpkg_file = os.path.join(out_folder, f"{out_filename}_Poligonos.gpkg")
                params_reprj = {
                    'INPUT': geojson_file,
                    'TARGET_CRS': target_crs_str,
                    'OUTPUT': gpkg_file
                }
                res = processing.run("native:reprojectlayer", params_reprj, context=context, feedback=feedback)
                gpkg_path = res['OUTPUT']
                
                vlayer = QgsVectorLayer(gpkg_path, f"{out_filename}_Poligonos", "ogr")
                if vlayer.isValid():
                    vlayer.dataProvider().addAttributes([
                        QgsField("rango", QMETATYPE_STRING),
                        QgsField("descrip", QMETATYPE_STRING),
                        QgsField("Area_ha", QMETATYPE_DOUBLE),
                        QgsField("Biomasa_T", QMETATYPE_DOUBLE),
                        QgsField("Carbono_T", QMETATYPE_DOUBLE),
                        QgsField("CO2e_T", QMETATYPE_DOUBLE)
                    ])
                    vlayer.updateFields()
                    
                    idx_rango = vlayer.fields().indexOf("rango")
                    idx_desc = vlayer.fields().indexOf("descrip")
                    idx_area = vlayer.fields().indexOf("Area_ha")
                    idx_bio = vlayer.fields().indexOf("Biomasa_T")
                    idx_car = vlayer.fields().indexOf("Carbono_T")
                    idx_co2 = vlayer.fields().indexOf("CO2e_T")
                    
                    rangos = {
                        1: ('< 20', 'Vegetación muy escasa'),
                        2: ('20 - 50', 'Matorral'),
                        3: ('50 - 100', 'Bosque joven'),
                        4: ('100 - 150', 'Bosque secundario'),
                        5: ('150 - 200', 'Bosque desarrollado'),
                        6: ('200 - 250', 'Bosque maduro'),
                        7: ('250 - 300', 'Bosque denso'),
                        8: ('> 300', 'Bosque tropical muy denso')
                    }
                    
                    from qgis.core import QgsDistanceArea
                    da = QgsDistanceArea()
                    da.setSourceCrs(target_crs, context.transformContext())
                    da.setEllipsoid(context.project().crs().ellipsoidAcronym())
                    
                    freq_dict = {}
                    
                    vlayer.startEditing()
                    for f in vlayer.getFeatures():
                        c_id = int(f['Clase']) if f['Clase'] else 0
                        rango_val, desc_val = rangos.get(c_id, ("Desconocido", "Desconocido"))
                        
                        area_m2 = da.measureArea(f.geometry())
                        area_ha = area_m2 / 10000.0
                        
                        mu_prom = f['MU_prom'] if f['MU_prom'] else 0.0
                        bio_t = area_ha * mu_prom
                        car_t = bio_t * 0.47
                        co2_t = car_t * (44.0/12.0)
                        
                        vlayer.changeAttributeValue(f.id(), idx_rango, rango_val)
                        vlayer.changeAttributeValue(f.id(), idx_desc, desc_val)
                        vlayer.changeAttributeValue(f.id(), idx_area, area_ha)
                        vlayer.changeAttributeValue(f.id(), idx_bio, bio_t)
                        vlayer.changeAttributeValue(f.id(), idx_car, car_t)
                        vlayer.changeAttributeValue(f.id(), idx_co2, co2_t)
                        
                        if c_id not in freq_dict: freq_dict[c_id] = 0.0
                        freq_dict[c_id] += area_ha
                        
                    vlayer.commitChanges()
                    
                    QgsProject.instance().addMapLayer(vlayer)
                    
                    # Cargar Raster en QGIS
                    rlayer = QgsRasterLayer(tif_mu, f"{out_filename}_MU")
                    if rlayer.isValid():
                        QgsProject.instance().addMapLayer(rlayer)
                    
                    # Generar Frecuencias y Gráficos
                    total_area = sum(freq_dict.values())
                    stats_list = []
                    for c_id in range(1, 9):
                        r_area = freq_dict.get(c_id, 0.0)
                        pct = (r_area / total_area * 100) if total_area > 0 else 0.0
                        r_str, d_str = rangos[c_id]
                        stats_list.append({
                            'Clase': c_id,
                            'Rango MU': r_str,
                            'Descripción': d_str,
                            'Área (ha)': round(r_area, 2),
                            '%': round(pct, 2)
                        })
                        
                    out_csv = os.path.join(out_folder, f"{out_filename}_Frecuencias.csv")
                    with open(out_csv, mode='w', newline='', encoding='utf-8-sig') as fcsv:
                        writer = csv.writer(fcsv)
                        writer.writerow(["Clase", "Rango MU (Mg/ha)", "Descripción", "Área Total (ha)", "%"])
                        for row in stats_list:
                            writer.writerow([row['Clase'], row['Rango MU'], row['Descripción'], row['Área (ha)'], row['%']])
                            
                    try:
                        import matplotlib.pyplot as plt
                        rangos_labels = [r['Rango MU'] for r in stats_list]
                        areas = [r['Área (ha)'] for r in stats_list]
                        
                        if 'seaborn-v0_8-whitegrid' in plt.style.available: plt.style.use('seaborn-v0_8-whitegrid')
                        elif 'seaborn-whitegrid' in plt.style.available: plt.style.use('seaborn-whitegrid')
                        else: plt.style.use('ggplot')
                        
                        fig, ax = plt.subplots(figsize=(10, 6))
                        bars = ax.bar(rangos_labels, areas, color='#31a354', edgecolor='#006837', linewidth=1.2)
                        ax.set_title('Distribución de Biomasa GEDI L4B por Área', fontsize=14, fontweight='bold', pad=20, color='#333333')
                        ax.set_xlabel('Rango de Biomasa MU (Mg/ha)', fontsize=12, fontweight='bold', labelpad=12, color='#333333')
                        ax.set_ylabel('Área Total (Hectáreas)', fontsize=12, fontweight='bold', labelpad=12, color='#333333')
                        
                        max_y = max(areas) if areas else 1
                        for bar in bars:
                            yval = bar.get_height()
                            if yval > 0:
                                ax.text(bar.get_x() + bar.get_width()/2, yval + (max_y*0.015), f'{int(yval)}', ha='center', va='bottom', fontsize=10, fontweight='bold', color='#111111')
                                
                        plt.xticks(rotation=45, ha='right', fontsize=11)
                        plt.yticks(fontsize=11)
                        ax.spines['top'].set_visible(False)
                        ax.spines['right'].set_visible(False)
                        if 'seaborn' not in plt.style.available:
                            ax.spines['left'].set_visible(False)
                            ax.spines['bottom'].set_visible(False)
                        
                        plt.tight_layout()
                        out_png = os.path.join(out_folder, f"{out_filename}_Grafico.png")
                        plt.savefig(out_png, dpi=300, bbox_inches='tight', facecolor='white')
                        plt.close(fig)
                    except Exception as plt_err:
                        feedback.pushInfo(f"Aviso gráfico: {str(plt_err)}")
                        
                    try: os.remove(geojson_file)
                    except: pass
                    
            except Exception as ee_err:
                feedback.reportError(f"Error en descarga directa: {str(ee_err)}", fatalError=False)
                
            return {self.OUT_FOLDER: out_folder}
