import os
import json
import ee
import zipfile
import requests
import geopandas as gpd
import processing
from .glcluc_charts import GlclucCharts
from ..utils.aoi_builder import build_aoi, to_ee_geometry, safe_simplify
from ..utils.gee_init import ensure_gee_initialized
from ..utils.export_router import export_image, ExportMethod
from ..utils.style_utils import save_qml_sidecar
from qgis.core import QgsVectorLayer, QgsFeatureRequest, QgsCategorizedSymbolRenderer, QgsRendererCategory, QgsSymbol, QgsSingleSymbolRenderer, QgsRasterLayer, QgsColorRampShader, QgsRasterShader, QgsSingleBandPseudoColorRenderer
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
    QgsProcessingParameterMapLayer
)
from qgis.PyQt.QtCore import QCoreApplication

class GlclucAlgorithm(QgsProcessingAlgorithm):
    INPUT_LAYER = 'INPUT_LAYER'
    INPUT_EXTENT = 'INPUT_EXTENT'
    PRODUCT = 'PRODUCT'
    EPSG = 'EPSG'
    EXPORT_METHOD = 'EXPORT_METHOD'
    OUT_FOLDER = 'OUT_FOLDER'
    GCS_BUCKET = 'GCS_BUCKET'

    def __init__(self):
        super().__init__()
        self.productos_ui = [
            "Ganancia Forestal del 2000 al 2020",
            "Dinámica Forestal 2000 al 2020"
        ]
        self.productos_id = [
            "Ganancia_foresta_2020",
            "Dinamica_forestal_2000_2020"
        ]
        self.export_methods = [
            "Descarga Directa (Vector + Excel Estadístico)",
            "Exportar a Google Drive",
            "Exportar a Google Cloud Storage (GCS)"
        ]

    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    def createInstance(self):
        return GlclucAlgorithm()

    def name(self):
        return 'glcluc_analysis'

    def displayName(self):
        return self.tr('Cambio global de la cobertura 2000 - 2020')

    def group(self):
        return self.tr('Monitoreo de Bosques')

    def groupId(self):
        return 'monitoreo_bosques'

    def shortHelpString(self):
        help_text = (
            "<b>Descripción del Proceso:</b><br>"
            "Esta herramienta automatiza la extracción de la dinámica de cobertura global y altura del dosel empleando Google Earth Engine (GEE). "
            "El algoritmo extrae, recorta y vectoriza datos satelitales según tu Área de Interés, generando gráficos "
            "estadísticos y capas vectoriales descargables o exportables a la nube.<br><br>"
            "<b>Información y Fuente de Datos:</b><br>"
            "Los datos utilizados provienen del proyecto <a href='https://glad.umd.edu/dataset/GLCLUC2020/'><b>Global Land Cover and Land Use Change (GLCLUC) 2020</b></a>, "
            "desarrollado por el laboratorio GLAD de la Universidad de Maryland (UMD). "
            "Permite evaluar la altura de la copa del bosque en el año 2000 y 2020, así como la ganancia y dinámica forestal en dicho periodo."
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
        self.addParameter(QgsProcessingParameterEnum(self.PRODUCT, self.tr('Producto GLCLUC'), options=self.productos_ui, defaultValue=1))
        self.addParameter(QgsProcessingParameterCrs(self.EPSG, self.tr('CRS de Destino'), defaultValue='EPSG:32718', optional=True))
        self.addParameter(QgsProcessingParameterEnum(self.EXPORT_METHOD, self.tr('Método de Exportación'), options=self.export_methods, defaultValue=0))
        self.addParameter(QgsProcessingParameterFolderDestination(self.OUT_FOLDER, self.tr('Carpeta Local de Destino (Solo Descarga Directa)'), optional=True))
        self.addParameter(QgsProcessingParameterString(self.GCS_BUCKET, self.tr('Nombre del Bucket GCS (Solo para GCS)'), optional=True))

    def processAlgorithm(self, parameters, context, feedback):
        
        # 1. AOI centralizado (utils/aoi_builder)
        aoi = build_aoi(self, parameters, context, feedback)
        geom_union = aoi.geom_4326
        crs_source = aoi.source_crs
        original_aoi_layer = aoi.aoi_layer
            
        
        producto_idx = self.parameterAsEnum(parameters, self.PRODUCT, context)
        producto_nombre = self.productos_id[producto_idx]
        
        crs_dest = self.parameterAsCrs(parameters, self.EPSG, context)
        reproject_utm = crs_dest.isValid()
        
        export_method_idx = self.parameterAsEnum(parameters, self.EXPORT_METHOD, context)
        out_folder = self.parameterAsString(parameters, self.OUT_FOLDER, context)
        gcs_bucket = self.parameterAsString(parameters, self.GCS_BUCKET, context)
        
        out_dir = out_folder if out_folder else ""
        if export_method_idx == 0 and not out_dir:
            raise QgsProcessingException("Debe seleccionar una Carpeta Local de Destino.")
            
        # geom_union ya está 100% en EPSG:4326 (build_aoi se encarga).
        geom_union_simp = safe_simplify(geom_union, 0.0001)
        geo_dict = json.loads(geom_union_simp.asJson())
        
        # 3. Inicializar GEE (wrapper centralizado)
        ensure_gee_initialized(feedback)
                
        # 4. Geometría EE sin truncar GeometryCollection — fix B-01
        geom_ee_exact = to_ee_geometry(geom_union_simp, ee, geo_dict)
            
        feedback.pushInfo("Aplicando Buffer INTERNO de seguridad en GEE de 250m...")
        geom_ee_buffer = geom_ee_exact.buffer(250, 30)
        
        feedback.setProgress(10)
        
        # Productos
        asset_base = 'projects/glad/GLCLU2020/'
        if producto_nombre == "altura_foresta_2000":
            asset_id = asset_base + 'Forest_height_2000'
            label_prop = 'Altura2000'
        elif producto_nombre == "altura_foresta_2020":
            asset_id = asset_base + 'Forest_height_2020'
            label_prop = 'Altura2020'
        elif producto_nombre == "Ganancia_foresta_2020":
            asset_id = asset_base + 'Forest_gain'
            label_prop = 'Ganancia'
        elif producto_nombre == "Dinamica_forestal_2000_2020":
            asset_id = asset_base + 'Forest_type'
            label_prop = 'Dinamica'

        out_filename = f"GLCLUC_{producto_nombre}"
        img_band = ee.Image(asset_id).select(0).rename(label_prop)
        img_for_vector = img_band.updateMask(img_band.gt(0))
        
        buffer_mask = ee.Image().byte().paint(geom_ee_buffer, 1)
        img_for_vector = img_for_vector.updateMask(buffer_mask)
        img_for_vector = img_for_vector.clip(geom_ee_buffer.bounds())
            
        proj = img_for_vector.projection()
        is_raster_export = "altura" in producto_nombre.lower()
        
        if is_raster_export:
            feedback.setProgress(40)
            feedback.pushInfo("Preparando descarga de Raster a 30m en GEE...")
            img_for_export = img_for_vector # Se exporta con el buffer de 250m
            
            if export_method_idx != 0:
                export_image(
                    method=export_method_idx, image=img_for_export,
                    description=out_filename, region=geom_ee_buffer.bounds(),
                    scale=30, crs='EPSG:4326', drive_folder='GEE_Bosques_GLCLUC',
                    gcs_bucket=gcs_bucket, feedback=feedback,
                )
                return {self.OUT_FOLDER: out_dir}
            else:
                feedback.pushInfo("Descargando Raster desde GEE...")
                try:
                    url = img_for_export.getDownloadURL({
                        'name': out_filename,
                        'crs': 'EPSG:4326',
                        'scale': 30,
                        'region': geom_ee_buffer.bounds(),
                        'format': 'GEO_TIFF'
                    })
                    r = requests.get(url, stream=True)
                    r.raise_for_status()
                    
                    tif_file = os.path.join(out_dir, f"{out_filename}.tif")
                    with open(tif_file, 'wb') as f:
                        for chunk in r.iter_content(chunk_size=1024):
                            if chunk: f.write(chunk)
                            
                    if not os.path.exists(tif_file): raise QgsProcessingException("Error al guardar el archivo TIFF.")
                    
                    feedback.pushInfo("Cargando Raster a QGIS...")
                    
                    rlayer = QgsRasterLayer(tif_file, out_filename)
                    if rlayer.isValid():
                        rlayer.dataProvider().setNoDataValue(1, 0)
                        fnc = QgsColorRampShader()
                        fnc.setColorRampType(QgsColorRampShader.Discrete)
                        lst = [
                            QgsColorRampShader.ColorRampItem(5, QColor('#ffffe5'), '1 - 5 m'),
                            QgsColorRampShader.ColorRampItem(10, QColor('#addd8e'), '5 - 10 m'),
                            QgsColorRampShader.ColorRampItem(20, QColor('#78c679'), '10 - 20 m'),
                            QgsColorRampShader.ColorRampItem(30, QColor('#31a354'), '20 - 30 m'),
                            QgsColorRampShader.ColorRampItem(40, QColor('#006837'), '> 30 m')
                        ]
                        fnc.setColorRampItemList(lst)
                        shader = QgsRasterShader()
                        shader.setRasterShaderFunction(fnc)
                        renderer = QgsSingleBandPseudoColorRenderer(rlayer.dataProvider(), 1, shader)
                        renderer.setClassificationMin(1)
                        renderer.setClassificationMax(100)
                        rlayer.setRenderer(renderer)
                        rlayer.triggerRepaint()
                        QgsProject.instance().addMapLayer(rlayer)
                        
                    return {self.OUT_FOLDER: out_dir}
                except Exception:
                    feedback.pushInfo("Área demasiado grande para raster directo. Exportando a Drive...")
                    export_image(
                        method=ExportMethod.DRIVE, image=img_for_export,
                        description=out_filename, region=geom_ee_buffer.bounds(),
                        scale=30, crs='EPSG:4326', drive_folder='GEE_Bosques_GLCLUC',
                        feedback=feedback,
                    )
                    feedback.pushInfo(f"El área es demasiado grande para descarga directa de raster. Exportación a Drive iniciada con el nombre: {out_filename}")
                    feedback.reportError(f"NOTA: Área inmensa. El raster '{out_filename}' fue enviado a Google Drive.", fatalError=False)
                    return {}

        else:
            feedback.setProgress(40)
            feedback.pushInfo("Transformando Raster a Vector a 30m en GEE...")
            
            if export_method_idx != 0:
                img_for_export = img_for_vector
                destino = "Google Drive" if export_method_idx == 1 else "GCS"
                feedback.pushInfo(
                    f"Por el tamaño del área, el formato vectorial supera el "
                    f"límite de Google. Se exportará como RASTER (.tif) a {destino}."
                )
                export_image(
                    method=export_method_idx, image=img_for_export,
                    description=out_filename, region=geom_ee_buffer.bounds(),
                    scale=30, crs='EPSG:4326', drive_folder='GEE_Bosques_GLCLUC',
                    gcs_bucket=gcs_bucket, feedback=feedback,
                )
                return {}
            
            # Descarga Directa (0)
            img_to_reduce = img_for_vector
            reduce_geom = geom_ee_buffer.bounds()

            vector = img_to_reduce.reduceToVectors(
                geometry=reduce_geom,
                crs=proj,
                scale=30,
                geometryType='polygon',
                reducer=ee.Reducer.countEvery(),
                eightConnected=False,
                labelProperty=label_prop,
                maxPixels=1e13,
                tileScale=16
            )
            
            feedback.setProgress(70)
                
            feedback.pushInfo("Descargando vector desde GEE...")
            try:
                url = vector.getDownloadURL(filetype='SHP', filename=out_filename)
                r = requests.get(url, stream=True)
                r.raise_for_status()
            except Exception:
                feedback.pushInfo("Área demasiado grande para vectorizar. Cambiando automáticamente a exportación RASTER hacia Google Drive...")
                img_for_export = img_for_vector
                export_image(
                    method=ExportMethod.DRIVE, image=img_for_export,
                    description=out_filename, region=geom_ee_buffer.bounds(),
                    scale=30, crs='EPSG:4326', drive_folder='GEE_Bosques_GLCLUC',
                    feedback=feedback,
                )
                feedback.reportError(f"NOTA: Área inmensa. El archivo '{out_filename}' fue enviado de forma segura como RASTER (.tif) a Google Drive.", fatalError=False)
                return {}

            zip_path = os.path.join(out_dir, f"{out_filename}.zip")
            with open(zip_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=1024):
                    if chunk: f.write(chunk)

            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(out_dir)

            shp_file = None
            for f in os.listdir(out_dir):
                if f.endswith(".shp") and out_filename in f:
                    shp_file = os.path.join(out_dir, f)
                    break

            if not shp_file: raise QgsProcessingException("No se encontró el Shapefile.")
            
            # 1. Reproyectar a UTM PRIMERO
            feedback.pushInfo(f"Reproyectando a UTM {crs_source.authid()}...")
            out_reproj = os.path.join(out_dir, f"{out_filename}_reproj.gpkg")
            processing.run("native:reprojectlayer", {
                'INPUT': shp_file,
                'TARGET_CRS': crs_source,
                'OUTPUT': out_reproj
            }, context=context, feedback=feedback)
            shp_file = out_reproj

            # 2. Recortar estricto usando el polígono original en UTM
            feedback.pushInfo("Recortando geometría con herramientas nativas (en UTM)...")
            out_clip = os.path.join(out_dir, f"{out_filename}_final.gpkg")
            
            original_check = context.invalidGeometryCheck()
            context.setInvalidGeometryCheck(QgsFeatureRequest.GeometrySkipInvalid)
            
            processing.run("native:createspatialindex", {'INPUT': shp_file}, context=context, feedback=feedback)
            
            try:
                processing.run("native:clip", {
                    'INPUT': shp_file,
                    'OVERLAY': original_aoi_layer,
                    'OUTPUT': out_clip
                }, context=context, feedback=feedback)
            finally:
                context.setInvalidGeometryCheck(original_check)
                
            shp_file = out_clip
                
            # BYPASS gpd.read_file() para evitar pyproj Access Violation y pérdida de CRS
            import pandas as pd
            import geopandas as gpd
            from shapely.wkt import loads
            
            vlayer_clip = QgsVectorLayer(shp_file, "temp", "ogr")
            feats = []
            geoms = []
            iterator = vlayer_clip.getFeatures()
            for feat in iterator:
                feats.append(feat.attributes())
                geom = feat.geometry()
                if not geom.isEmpty():
                    geoms.append(loads(geom.asWkt()))
                else:
                    geoms.append(None)
            iterator.close()
            del iterator
            
            field_names = [f.name() for f in vlayer_clip.fields()]
            df = pd.DataFrame(feats, columns=field_names)
            # Se omite CRS para evitar crashes de pyproj en Windows
            gdf_clipped = gpd.GeoDataFrame(df, geometry=geoms)
            
            del vlayer_clip
            import gc
            gc.collect()
            
            if gdf_clipped.empty:
                feedback.pushInfo("El recorte resultante está vacío. El polígono no intersecta áreas con datos válidos.")
            else:
                # Calcular area (ya está en metros gracias a la reproyección nativa)
                gdf_clipped['area_ha'] = (gdf_clipped.geometry.area / 10000).round(4)
                
                chart_logic = GlclucCharts(out_dir, producto_nombre)
                chart_logic.generate_charts(gdf_clipped, shp_file, label_prop)

            # --- RESTAURAR CRS Y EVITAR OGR LOCK ---
            # Como geopandas guardó el archivo en blanco (sin CRS), usamos la 
            # herramienta nativa para asignarlo a un archivo limpio.
            temp_assigned = shp_file.replace(".gpkg", "_PROYECTADO.gpkg")
            processing.run("native:assignprojection", {
                'INPUT': shp_file,
                'CRS': crs_source,
                'OUTPUT': temp_assigned
            }, context=context, feedback=feedback)
            
            if os.path.exists(shp_file):
                try: os.remove(shp_file)
                except: pass
                
            shp_file = temp_assigned

            # Cargar a QGIS
            feedback.pushInfo("Cargando resultados a QGIS...")
            
            vlayer = QgsVectorLayer(shp_file, out_filename, "ogr")
            if vlayer.isValid():
                if "Dinamica" in producto_nombre:
                    idx = vlayer.fields().indexOf("TIPO")
                    if idx == -1: idx = vlayer.fields().indexOf(label_prop)
                    
                    categories = []
                    colores_dict = {
                        'Bosque estable': '#006400',
                        'Perdida forestal': '#FF0000',
                        'Ganancia forestal': '#00FF00',
                        'Degradacion': '#FF8C00'
                    }
                    for nombre, color_hex in colores_dict.items():
                        sym = QgsSymbol.defaultSymbol(vlayer.geometryType())
                        sym.setColor(QColor(color_hex))
                        sym.symbolLayer(0).setStrokeColor(QColor("transparent"))
                        categories.append(QgsRendererCategory(nombre, sym, nombre))
                    
                    vlayer.setRenderer(QgsCategorizedSymbolRenderer('TIPO', categories))
                else:
                    sym = QgsSymbol.defaultSymbol(vlayer.geometryType())
                    if "Ganancia" in producto_nombre:
                        sym.setColor(QColor('#0000ff'))
                    else:
                        sym.setColor(QColor('#005a00'))
                    vlayer.setRenderer(QgsSingleSymbolRenderer(sym))
                    
                save_qml_sidecar(vlayer, shp_file)
                QgsProject.instance().addMapLayer(vlayer)

            feedback.setProgress(100)
            return {self.OUT_FOLDER: out_dir}
