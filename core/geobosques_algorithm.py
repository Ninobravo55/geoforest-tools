import os
import json
import ee
import zipfile
import requests
import geopandas as gpd
import processing
import uuid  # ← AGREGAR ESTA LÍNEA
from .geobosques_charts import GeobosquesCharts
from ..utils.aoi_builder import build_aoi, to_ee_geometry, safe_simplify
from ..utils.gee_init import ensure_gee_initialized
from ..utils.export_router import export_image, ExportMethod
from ..utils.style_utils import save_qml_sidecar
from qgis.core import (
    QgsFeatureRequest,
    QgsCategorizedSymbolRenderer,
    QgsRendererCategory,
    QgsSymbol,
    QgsSingleSymbolRenderer,
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
    QgsCoordinateReferenceSystem
)
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
    QgsVectorLayer
)
from qgis.PyQt.QtCore import QCoreApplication

class GeobosquesAlgorithm(QgsProcessingAlgorithm):
    INPUT_LAYER = 'INPUT_LAYER'
    INPUT_EXTENT = 'INPUT_EXTENT'
    PRODUCT = 'PRODUCT'
    EPSG = 'EPSG'
    EXPORT_METHOD = 'EXPORT_METHOD'
    OUT_FOLDER = 'OUT_FOLDER'
    GCS_BUCKET = 'GCS_BUCKET'

    def __init__(self):
        super().__init__()
        self.productos = [
            "Bosque y No bosque (Actualizado)",
            "Pérdida de Bosque (Actualizado)",
            "Alerta Temprana 2025",
            "Alerta Temprana 2026"
        ]
        self.export_methods = [
            "Descarga Directa (Vector + Gráficos Locales)",
            "Exportar a Google Drive",
            "Exportar a Google Cloud Storage (GCS)"
        ]

    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    def createInstance(self):
        return GeobosquesAlgorithm()

    def name(self):
        return 'geobosques_analysis'

    def displayName(self):
        return self.tr('Monitoreo GEOBOSQUES Perú')

    def group(self):
        return self.tr('Monitoreo de Bosques')

    def groupId(self):
        return 'monitoreo_bosques'

    def shortHelpString(self):
        help_text = (
            "<b>Descripción del Proceso:</b><br>"
            "Esta herramienta automatiza la extracción de datos forestales utilizando la potencia de cálculo de Google Earth Engine (GEE). "
            "El algoritmo intersecta el Área de Interés (con o sin buffer) contra la base de datos oficial, reduciendo el raster a polígonos vectoriales. "
            "Además, permite la exportación a Google Drive/Cloud o la descarga directa, en cuyo caso genera reportes estadísticos y gráficos automatizados.<br><br>"
            "<b>Datos Fuente:</b><br>"
            "Los datos utilizados provienen del Programa Nacional de Conservación de Bosques: MINAM - GEOBOSQUES.<br>"
            "Página oficial: <a href='https://geobosques.minam.gob.pe/geobosque/view/index.php'>https://geobosques.minam.gob.pe</a>"
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
        
        self.addParameter(
            QgsProcessingParameterExtent(
                self.INPUT_EXTENT,
                self.tr('Opción 2: O dibujar un Recuadro (Extensión del Canvas)'),
                optional=True
            )
        )
        
        self.addParameter(
            QgsProcessingParameterEnum(
                self.PRODUCT,
                self.tr('Producto GEOBOSQUES'),
                options=self.productos,
                defaultValue=0 # Bosque y No bosque 2024
            )
        )
        

        self.addParameter(
            QgsProcessingParameterCrs(
                self.EPSG,
                self.tr('CRS de Destino'),
                defaultValue='EPSG:32718',
                optional=True
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
                self.tr('Carpeta Local de Destino (Solo para Descarga Directa)'),
                optional=True
            )
        )
        
        self.addParameter(
            QgsProcessingParameterString(
                self.GCS_BUCKET,
                self.tr('Nombre del Bucket GCS (Solo para Google Cloud Storage)'),
                optional=True
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        # 1. Construcción del AOI (centralizada — utils/aoi_builder)
        aoi = build_aoi(self, parameters, context, feedback)
        geom_union = aoi.geom_4326
        original_aoi_layer = aoi.aoi_layer
            
        
        producto_idx = self.parameterAsEnum(parameters, self.PRODUCT, context)
        producto_nombre = self.productos[producto_idx]
        
        crs_dest = self.parameterAsCrs(parameters, self.EPSG, context)

        # ✓ VALIDACIÓN NUEVA
        if not crs_dest.isValid():
            feedback.pushWarning(
                f"⚠️ CRS destino especificado es inválido. "
                f"Usando EPSG:32718 (UTM zona 18S) por defecto."
            )
            crs_dest = QgsCoordinateReferenceSystem('EPSG:32718')

        feedback.pushInfo(
            f"CRS de entrada: {aoi.source_crs_authid}\n"
            f"CRS destino: {crs_dest.authid()}"
        )
        
        export_method_idx = self.parameterAsEnum(parameters, self.EXPORT_METHOD, context)
        out_folder = self.parameterAsString(parameters, self.OUT_FOLDER, context)
        gcs_bucket = self.parameterAsString(parameters, self.GCS_BUCKET, context)
        
        out_dir = out_folder if out_folder else ""
        if export_method_idx == 0 and not out_dir:
            raise QgsProcessingException(
                "❌ Debes seleccionar una Carpeta Local de Destino "
                "para usar 'Descarga Directa (Vector + Gráficos Locales)'."
            )
        if export_method_idx == 2 and not gcs_bucket:
            raise QgsProcessingException(
                "❌ Debes ingresar un Nombre de Bucket GCS "
                "para usar Google Cloud Storage."
            )
        

        # geom_union ya está 100% en EPSG:4326 (build_aoi se encarga).
        # Aplicamos safe_simplify (tolera AOIs muy pequeños sin aplastarlos).
        geom_union_simp = safe_simplify(geom_union, 0.0001)
        geo_dict = json.loads(geom_union_simp.asJson())
        
        # 3. Inicializar GEE (wrapper centralizado — utils/gee_init)
        ensure_gee_initialized(feedback)
                
        if feedback.isCanceled(): return {}
        
        # 4. Crear Geometría EE (sin truncar GeometryCollection — fix B-01)
        geom_ee_exact = to_ee_geometry(geom_union_simp, ee, geo_dict)
            
        feedback.pushInfo("Aplicando buffer interno de seguridad en GEE (250m)...")
        geom_ee_buffer = geom_ee_exact.buffer(250, 30)
            
        if feedback.isCanceled(): return {}
        
        # 5. Selección de Producto
        feedback.setProgress(10)
        is_collection = False
        if "Bosque" in producto_nombre and "Pérdida" not in producto_nombre:
            asset_id = "projects/gee-unas/assets/BOSQUE/Bosque_No_Bosque_MINAM"
            label_prop = 'Cobertura'
            is_collection = True
            out_filename = "Bosque_No_Bosque_Actualizado"
        elif "Pérdida" in producto_nombre:
            asset_id = "projects/gee-unas/assets/BOSQUE/Perdida_2001_Actual"
            label_prop = 'Anual'
            is_collection = True
            out_filename = "Perdida_Bosque_Actualizada"
        elif "2025" in producto_nombre:
            asset_id = "projects/gee-unas/assets/BOSQUE/ATD_Anterior"
            label_prop = 'dia'
            out_filename = "Alerta_Temprana_2025"
            is_collection = True
        elif "2026" in producto_nombre:
            asset_id = "projects/gee-unas/assets/BOSQUE/ATD_2026"
            label_prop = 'dia'
            out_filename = "Alerta_Temprana_2026"
            is_collection = True
            
        feedback.pushInfo("Recortando Imagen GEOBOSQUES...")
        if is_collection:
            collection = ee.ImageCollection(asset_id)
            if "2026" in producto_nombre:
                # Para 2026, ordenamos por system:index descendente porque las imágenes subidas
                # manualmente a veces no tienen la propiedad system:time_start, lo que hace
                # que sort('system:time_start') falle y traiga una imagen incorrecta o vacía.
                img = collection.sort('system:index', False).first()
            else:
                img = collection.sort('system:time_start', False).first()
        else:
            img = ee.Image(asset_id)
            
        fuente_2026 = ""
        if "2026" in producto_nombre:
            try:
                feedback.pushInfo("Obteniendo Image ID (fuente) de Earth Engine...")
                # system:index contiene exactamente la última parte del ID (ej. 2026_ATD_01_136_R)
                fuente_2026 = str(img.get('system:index').getInfo())
                feedback.pushInfo(f"Fuente detectada: {fuente_2026}")
            except Exception as e:
                feedback.pushWarning(f"No se pudo obtener la fuente: {e}")
            
        buffer_mask = ee.Image().byte().paint(geom_ee_buffer, 1)
        img = img.updateMask(buffer_mask)
        img = img.clip(geom_ee_buffer.bounds())
            
        target_crs_str = img.projection()
            
        if feedback.isCanceled(): return {}
        feedback.setProgress(30)
        
        # 6. Reducción a Vectores
        feedback.pushInfo("Transformando Raster a Vector en GEE (Puede tardar varios minutos)...")
        if "Pérdida" in producto_nombre:
            img_for_vector = img.updateMask(img.gte(2).And(img.lte(25)))
        else:
            img_for_vector = img.updateMask(img)
            
        if export_method_idx != 0:
            img_for_export = img_for_vector
            destino = "Google Drive" if export_method_idx == 1 else "GCS"
            feedback.pushInfo(
                f"Debido al tamaño, el formato vectorial colapsa la nube. "
                f"Se enviará como RASTER (.tif) a {destino}."
            )
            export_image(
                method=export_method_idx, image=img_for_export,
                description=out_filename, region=geom_ee_buffer.bounds(),
                scale=30, crs='EPSG:4326', drive_folder='GEE_Bosques',
                gcs_bucket=gcs_bucket, feedback=feedback,
            )
            return {}
        
        # Descarga Directa (0)
        img_to_reduce = img_for_vector
        reduce_geom = geom_ee_buffer.bounds()

        vector = img_to_reduce.reduceToVectors(
            geometry=reduce_geom,
            crs=target_crs_str,
            scale=30,
            geometryType='polygon',
            reducer=ee.Reducer.countEvery(),
            eightConnected=False,
            labelProperty=label_prop,
            maxPixels=1e13,
            tileScale=16
        )
        
        if feedback.isCanceled(): return {}
        feedback.setProgress(60)

        # Descarga Directa (0)
        feedback.pushInfo("Solicitando descarga directa a Google Earth Engine...")
        r = None  # fix B-03: evita UnboundLocalError si requests.get falla antes de retornar
        try:
            url = vector.getDownloadURL(filetype='SHP', filename=out_filename)
            r = requests.get(url, stream=True, timeout=300)
            r.raise_for_status()
        except Exception:
            feedback.pushInfo("Área demasiado grande para vectorizar. Cambiando automáticamente a exportación RASTER hacia Google Drive...")
            img_for_export = img_for_vector
            export_image(
                method=ExportMethod.DRIVE, image=img_for_export,
                description=out_filename, region=geom_ee_buffer.bounds(),
                scale=30, crs='EPSG:4326', drive_folder='GEE_Bosques',
                feedback=feedback,
            )
            feedback.reportError(f"NOTA: Área inmensa. El archivo '{out_filename}' fue enviado de forma segura como RASTER (.tif) a Google Drive.", fatalError=False)
            return {}

        # 8. Descargar ZIP y Extraer
        feedback.pushInfo("Descargando archivo ZIP...")
        zip_path = os.path.join(out_dir, f"{out_filename}.zip")
        with open(zip_path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=1024):
                if feedback.isCanceled(): return {}
                if chunk:
                    f.write(chunk)

        feedback.pushInfo("Extrayendo Shapefile...")
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(out_dir)

        # Buscar el SHP extraído
        shp_file = None
        for f in os.listdir(out_dir):
            if f.endswith(".shp") and out_filename in f:
                shp_file = os.path.join(out_dir, f)
                break

        if not shp_file:
            raise QgsProcessingException("No se pudo encontrar el Shapefile descargado.")

        feedback.setProgress(85)
        if feedback.isCanceled(): return {}

        # 9. Post-procesamiento Geopandas y gráficos
        feedback.pushInfo("Recortando geometría y generando reportes gráficos...")
        
        feedback.pushInfo("━━━ FASE 3: Post-procesamiento Local ━━━")

        # PASO 1: Assign CRS (GEE a veces no adjunta .prj)
        feedback.pushInfo("Asignando CRS base (EPSG:4326) al descargado...")
        out_assigned = os.path.join(out_dir, f"{out_filename}_assigned.shp")
        processing.run("native:assignprojection", {
            'INPUT': shp_file,
            'CRS': QgsCoordinateReferenceSystem('EPSG:4326'),
            'OUTPUT': out_assigned
        }, context=context, feedback=feedback)

        # PASO 2: Reproyectar SHP al CRS destino (UNA SOLA VEZ)
        if crs_dest.authid() != 'EPSG:4326':
            feedback.pushInfo(
                f"Reproyectando vector a {crs_dest.authid()}..."
            )
            out_reproj = os.path.join(
                out_dir,
                f"{out_filename}_reproj_{crs_dest.authid().replace(':', '')}.gpkg"
            )
            
            # Eliminar si existe (evita OGR lock)
            if os.path.exists(out_reproj):
                try:
                    os.remove(out_reproj)
                    feedback.pushInfo(f"Archivo existente eliminado: {out_reproj}")
                except Exception as e:
                    feedback.pushWarning(f"No se pudo eliminar {out_reproj}: {e}")
            
            processing.run("native:reprojectlayer", {
                'INPUT': out_assigned,
                'TARGET_CRS': crs_dest,
                'OUTPUT': out_reproj
            }, context=context, feedback=feedback)
        else:
            # Datos ya están en 4326, no reprojectar
            out_reproj = out_assigned
            feedback.pushInfo("SHP ya está en EPSG:4326, saltando reproyección")


        # PASO 3: Reproyectar AOI al MISMO CRS destino
        feedback.pushInfo(
            f"Reproyectando AOI (desde {aoi.source_crs_authid}) "
            f"a {crs_dest.authid()}..."
        )
        res_aoi = processing.run("native:reprojectlayer", {
            'INPUT': aoi.aoi_layer,
            'TARGET_CRS': crs_dest,
            'OUTPUT': 'memory:'
        }, context=context, feedback=feedback)

        out_aoi_reproj = res_aoi['OUTPUT']

        # ✓ VALIDACIÓN CRÍTICA: AOI no debe estar vacío
        if out_aoi_reproj is None or not out_aoi_reproj.isValid():
            raise QgsProcessingException(
                f"❌ Error al reproyectar AOI a {crs_dest.authid()}. "
                f"Posible geometría inválida o CRS incompatible."
            )

        feat_count_aoi = out_aoi_reproj.featureCount()
        if feat_count_aoi == 0:
            raise QgsProcessingException(
                f"❌ AOI resultó VACÍO tras reproyectar a {crs_dest.authid()}. "
                f"Revisa el AOI original o el CRS destino."
            )

        feedback.pushInfo(
            f"✓ AOI válido en {crs_dest.authid()}: "
            f"{feat_count_aoi} feature(s)"
        )


        # PASO 4: Crear índice espacial (mejora performance)
        feedback.pushInfo("Creando índice espacial...")
        processing.run("native:createspatialindex", {
            'INPUT': out_reproj
        }, context=context, feedback=feedback)


        # PASO 5: Recorte con validación (nombre único para evitar OGR lock)
        feedback.pushInfo("Recortando geometría con AOI...")

        out_clip_basename = (
            f"{out_filename}_final_"
            f"{uuid.uuid4().hex[:6]}.gpkg"
        )
        out_clip = os.path.join(out_dir, out_clip_basename)

        try:
            processing.run("native:clip", {
                'INPUT': out_reproj,
                'OVERLAY': out_aoi_reproj,
                'OUTPUT': out_clip
            }, context=context, feedback=feedback)
        except Exception as e:
            raise QgsProcessingException(
                f"❌ Error durante clip: {str(e)}\n"
                f"Verifica que ambas capas estén en {crs_dest.authid()} "
                f"y que la geometría sea válida."
            )


        # PASO 6: Verificar que resultado no esté vacío
        vlayer_check = QgsVectorLayer(out_clip, "temp_check", "ogr")
        if not vlayer_check.isValid():
            raise QgsProcessingException(
                f"❌ No se pudo leer el archivo recortado: {out_clip}"
            )

        feat_count_final = vlayer_check.featureCount()
        if feat_count_final == 0:
            feedback.pushWarning(
                "⚠️ El resultado está VACÍO. El AOI no intersectó "
                "con los datos descargados. Verifica que:\n"
                "  • El AOI esté dentro del área de datos\n"
                "  • Ambas capas estén en el mismo CRS"
            )
        else:
            feedback.pushInfo(
                f"✓ Recorte exitoso: {feat_count_final} polígonos"
            )

        del vlayer_check
        import gc
        gc.collect()

        shp_file = out_clip
            
        # WORKAROUND: Bypass geopandas.read_file() to prevent pyproj access violation in QGIS Windows.
        # We read geometries and attributes natively using PyQGIS and construct the GeoDataFrame manually.
        import pandas as pd
        from shapely.wkt import loads
        import geopandas as gpd
        
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
        
        # Explicitly close iterator to release OGR lock
        iterator.close()
        del iterator
                
        field_names = [f.name() for f in vlayer_clip.fields()]
        df = pd.DataFrame(feats, columns=field_names)
        # Se omite 'crs' deliberadamente. GeoPandas/PyProj colapsa con Access Violation 
        # si se intenta asignar CRS desde un hilo de fondo (thread) de QGIS en Windows.
        gdf = gpd.GeoDataFrame(df, geometry=geoms)
        
        # AGREGAR COLUMNA FUENTE PARA 2026
        if "2026" in producto_nombre and fuente_2026:
            gdf['fuente'] = fuente_2026
        
        # Completely release the layer lock
        del vlayer_clip
        import gc
        gc.collect()
        
        dummy_logic = GeobosquesCharts(out_dir, producto_nombre)

        if "Bosque_No_Bosque" in out_filename:
            dummy_logic.generate_bosque_charts(gdf, shp_file)
        elif "Perdida_Bosque" in out_filename:
            dummy_logic.generate_perdida_charts(gdf, shp_file)
        else:
            dummy_logic.generate_alerta_charts(gdf, shp_file)

        # Restaurar CRS (ya que geopandas sin crs borra la proyección al guardar)
        # Usamos un nombre nuevo para no corromper la caché OGR de QGIS
        temp_assigned = shp_file.replace(".gpkg", "_PROYECTADO.gpkg")
        processing.run("native:assignprojection", {
            'INPUT': shp_file,
            'CRS': crs_dest,
            'OUTPUT': temp_assigned
        }, context=context, feedback=feedback)
        
        # Eliminamos el archivo sin proyección si es posible, y apuntamos al nuevo
        if os.path.exists(shp_file):
            try: os.remove(shp_file)
            except: pass
            
        shp_file = temp_assigned

        feedback.setProgress(95)
        feedback.pushInfo(
            "\n━━━ RESUMEN DEL PROCESO ━━━\n"
            f"Producto: {producto_nombre}\n"
            f"AOI entrada: {aoi.source_crs_authid}\n"
            f"AOI procesado: EPSG:4326\n"
            f"Resultado final: {crs_dest.authid()}\n"
            f"Archivo: {os.path.basename(shp_file)}\n"
        )

        feedback.setProgress(100)
        feedback.pushInfo("¡Proceso completado con éxito! Revisa la carpeta seleccionada.")

        # 10. Cargar en QGIS y aplicar simbología
        feedback.pushInfo("Cargando capa en QGIS y aplicando simbología...")

        vlayer = QgsVectorLayer(shp_file, out_filename, "ogr")
        if vlayer.isValid():
            if "Bosque_No_Bosque" in out_filename:
                categories = []
                colores_dict = {
                    'Agua': '#1565c0',
                    'Bosque al 2024': '#9cbe62',
                    'Deforestacion del 2001 al 2024': '#ff0000',
                    'No Bosque al 2000': '#e6c46a',
                    'Sin informacion': '#b2b2b2'
                }
                for nombre, color_hex in colores_dict.items():
                    symbol = QgsSymbol.defaultSymbol(vlayer.geometryType())
                    symbol.setColor(QColor(color_hex))
                    symbol.symbolLayer(0).setStrokeColor(QColor("transparent"))
                    categories.append(QgsRendererCategory(nombre, symbol, nombre))
                vlayer.setRenderer(QgsCategorizedSymbolRenderer('NAME', categories))
                save_qml_sidecar(vlayer, shp_file)

            elif "Perdida_Bosque" in out_filename:
                symbol = QgsSymbol.defaultSymbol(vlayer.geometryType())
                symbol.setColor(QColor('red'))
                symbol.symbolLayer(0).setStrokeColor(QColor("transparent"))
                vlayer.setRenderer(QgsSingleSymbolRenderer(symbol))
                save_qml_sidecar(vlayer, shp_file)
                
            elif "Alerta_Temprana" in out_filename:
                symbol = QgsSymbol.defaultSymbol(vlayer.geometryType())
                symbol.setColor(QColor('#ff5500'))
                symbol.symbolLayer(0).setStrokeColor(QColor("transparent"))
                vlayer.setRenderer(QgsSingleSymbolRenderer(symbol))
                save_qml_sidecar(vlayer, shp_file)

            QgsProject.instance().addMapLayer(vlayer)

        # Processing outputs dict
        return {self.OUT_FOLDER: out_dir}
