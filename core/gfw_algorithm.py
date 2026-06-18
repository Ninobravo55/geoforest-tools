import os
import json
import ee
import zipfile
import requests
import geopandas as gpd
import processing
import datetime
from shapely.wkt import loads as load_wkt
from .gfw_charts import GfwCharts
from ..utils.aoi_builder import build_aoi, to_ee_geometry
from ..utils.gee_init import ensure_gee_initialized
from ..utils.export_router import export_table, ExportMethod
from ..utils.style_utils import save_qml_sidecar
from qgis.core import QgsSymbol, QgsSingleSymbolRenderer, QgsRuleBasedRenderer
from qgis.PyQt.QtGui import QColor

from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
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

class GfwAlgorithm(QgsProcessingAlgorithm):
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
            "Bosque del 2000 y 2025",
            "Pérdida de Bosque 2001 - 2025",
            "Ganancia de Bosque 2001 - 2012"
        ]
        self.export_methods = [
            "Descarga Directa (Vector + Gráficos Locales)",
            "Exportar a Google Drive",
            "Exportar a Google Cloud Storage (GCS)"
        ]

    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    def createInstance(self):
        return GfwAlgorithm()

    def name(self):
        return 'gfw_analysis'

    def displayName(self):
        return self.tr('Análisis Global Forest Watch')

    def group(self):
        return self.tr('Monitoreo de Bosques')

    def groupId(self):
        return 'monitoreo_bosques'

    def shortHelpString(self):
        help_text = (
            "<b>Descripción del Proceso:</b><br>"
            "Esta herramienta automatiza la extracción de la dinámica forestal empleando Google Earth Engine (GEE). "
            "El algoritmo extrae, recorta y vectoriza datos satelitales según tu Área de Interés, generando gráficos "
            "estadísticos y capas vectoriales descargables o exportables a la nube.<br><br>"
            "<b>Información y Fuente de Datos:</b><br>"
            "Los datos utilizados provienen de <a href='https://www.globalforestwatch.org/'><b>Global Forest Watch (GFW)</b></a>, "
            "específicamente de la base de datos <i>Global Forest Change</i> desarrollada por UMD/Hansen. Permite evaluar "
            "la cobertura boscosa del año 2000, la pérdida anual de bosques hasta la actualidad y la ganancia forestal (2000-2012)."
        )
        return self.tr(help_text)

    def initAlgorithm(self, config=None):
        from qgis.core import QgsProcessingParameterExtent
        
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
                self.tr('Producto Global Forest Watch'),
                options=self.productos,
                defaultValue=0 # Bosque 2000
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
        # 1. AOI centralizado (utils/aoi_builder)
        aoi = build_aoi(self, parameters, context, feedback)
        geom_union = aoi.geom_4326
            
        exact_geom_wkt = geom_union.asWkt()
        original_shapely_geom = load_wkt(exact_geom_wkt)
        
        producto_idx = self.parameterAsEnum(parameters, self.PRODUCT, context)
        producto_nombre = self.productos[producto_idx]
        
        crs_dest = self.parameterAsCrs(parameters, self.EPSG, context)
        reproject_utm = crs_dest.isValid()
        epsg_code = crs_dest.authid().replace("EPSG:", "") if reproject_utm else None
        
        export_method_idx = self.parameterAsEnum(parameters, self.EXPORT_METHOD, context)
        out_folder = self.parameterAsString(parameters, self.OUT_FOLDER, context)
        gcs_bucket = self.parameterAsString(parameters, self.GCS_BUCKET, context)
        
        out_dir = out_folder if out_folder else ""
        if export_method_idx == 0 and not out_dir:
            raise QgsProcessingException("Debe seleccionar una Carpeta Local de Destino para la Descarga Directa.")
        if export_method_idx == 2 and not gcs_bucket:
            raise QgsProcessingException("Debe ingresar un Nombre de Bucket para usar Google Cloud Storage.")
            
        geojson_str = geom_union.asJson()
        geo_dict = json.loads(geojson_str)
        
        # 3. Inicializar GEE (wrapper centralizado)
        ensure_gee_initialized(feedback)
                
        if feedback.isCanceled(): return {}
        
        # 4. Geometría EE sin truncar GeometryCollection — fix B-01
        geom_ee_exact = to_ee_geometry(geom_union, ee, geo_dict)
            
        feedback.pushInfo("Aplicando buffer interno de seguridad en GEE (250m)...")
        geom_ee_buffer = geom_ee_exact.buffer(250)
            
        if feedback.isCanceled(): return {}
        
        # 5. Selección de Producto GFW y Auto-Descubrimiento del Asset
        feedback.setProgress(10)
        
        current_year = datetime.datetime.now().year
        
        gfc_img = None
        asset_id = ""
        
        # Buscar hacia atrás desde el próximo año hasta el 2023
        for y in range(current_year + 1, 2022, -1):
            v = y - 2012
            test_id = f"UMD/hansen/global_forest_change_{y}_v1_{v}"
            try:
                # Comprobar si el asset existe
                ee.data.getAsset(test_id)
                asset_id = test_id
                gfc_img = ee.Image(asset_id).clip(geom_ee_buffer)
                feedback.pushInfo(f"¡Asset actualizado encontrado!: {asset_id}")
                break
            except Exception:
                continue
                
        if not gfc_img:
            feedback.pushInfo("Usando Asset por defecto de respaldo (2023)...")
            asset_id = "UMD/hansen/global_forest_change_2023_v1_11"
            gfc_img = ee.Image(asset_id).clip(geom_ee_buffer)
            
        # Actualizar los nombres de los archivos para que reflejen el año encontrado
        year_found = asset_id.split('_')[-3] # ej: 2025
        
        if "Bosque del 2000 y" in producto_nombre:
            label_prop = 'Cobertura'
            out_filename = "Bosque_2000_2025_GFW"
            # Extraer treecover > 10%
            treecover = gfc_img.select(['treecover2000']).gte(10)
            lossyear = gfc_img.select(['lossyear'])
            # Valor 2000 para bosque que no se perdió (bosque 2025)
            # Valor 2000 + lossyear para bosque que sí se perdió
            img_for_vector = ee.Image(2000).where(lossyear.gt(0), lossyear.add(2000)).updateMask(treecover).rename('Cobertura')
            
        elif "Pérdida" in producto_nombre:
            label_prop = 'Anual'
            out_filename = f"GFW_Perdida_2001_{year_found}"
            img_band = gfc_img.select(['lossyear'])
            img_for_vector = img_band.updateMask(img_band.gt(0)).rename('Anual')
            
        elif "Ganancia" in producto_nombre:
            label_prop = 'Ganancia'
            out_filename = "GFW_Ganancia_2001_2012"
            img_band = gfc_img.select(['gain'])
            img_for_vector = img_band.updateMask(img_band.gt(0)).rename('Ganancia')
            
        target_crs_str = img_for_vector.projection()
            
        if feedback.isCanceled(): return {}
        feedback.setProgress(30)
        
        # 6. Reducción a Vectores
        feedback.pushInfo("Transformando Raster a Vector en GEE (Puede tardar varios minutos)...")
        vector = img_for_vector.reduceToVectors(
            geometry=geom_ee_buffer,
            crs=target_crs_str,
            scale=30,
            geometryType='polygon',
            reducer=ee.Reducer.countEvery(),
            eightConnected=False,
            labelProperty=label_prop,
            maxPixels=1e13,
            tileScale=16
        )
        
        if "Bosque del 2000 y" in producto_nombre:
            def format_cobertura(feature):
                val = ee.Number(feature.get('Cobertura'))
                label = ee.Algorithms.If(
                    val.eq(2000), 
                    "Bosque al 2025", 
                    ee.String("Perdida bosque ").cat(val.format('%d'))
                )
                return feature.set('Cobertura', label)
            vector = vector.map(format_cobertura)
        

        
        if feedback.isCanceled(): return {}
        feedback.setProgress(60)
        
        # 7. Ejecutar Exportación
        if export_method_idx != 0:  # Google Drive o GCS
            destino = "Google Drive" if export_method_idx == 1 else "GCS"
            feedback.pushInfo(f"Iniciando exportación a {destino}...")
            export_table(
                method=export_method_idx, collection=vector,
                description=out_filename, file_format='SHP',
                drive_folder='GEE_Bosques_GFW', gcs_bucket=gcs_bucket,
                feedback=feedback,
            )
            return {}

        else: # Descarga Directa (0)
            feedback.pushInfo("Solicitando descarga directa a Google Earth Engine...")
            r = None  # fix B-04: evita NameError en el except si la red falla antes
            try:
                url = vector.getDownloadURL(filetype='SHP', filename=out_filename)
                r = requests.get(url, stream=True, timeout=300)
                r.raise_for_status()
            except Exception as ee_err:
                error_msg = str(ee_err)
                error_detail = ""
                if r is not None and hasattr(r, 'text'):
                    try:
                        error_detail = f" | Detalle GEE: {r.text}"
                    except Exception:
                        pass
                
                full_error = f"{error_msg}{error_detail}"
                feedback.pushInfo(f"Error de GEE en descarga directa: {full_error}")
                
                if "empty collection" in full_error.lower():
                    raise QgsProcessingException("El proceso finalizó porque NO se encontraron píxeles válidos (ej. deforestación) en el área seleccionada. La zona está limpia o sin cambios para este producto.")
                
                feedback.pushInfo("Área demasiado grande o compleja para descarga directa de vectores. Cambiando automáticamente a exportación hacia Google Drive...")
                vector_clipped = vector
                export_table(
                    method=ExportMethod.DRIVE, collection=vector_clipped,
                    description=out_filename, file_format='SHP',
                    drive_folder='GEE_Bosques_GFW', feedback=feedback,
                )
                feedback.pushInfo(f"El área es demasiado grande para descarga directa. Exportación iniciada a Google Drive (carpeta 'GEE_Bosques_GFW') con el nombre: {out_filename}")
                feedback.reportError(f"NOTA: Área inmensa. El vector '{out_filename}' fue enviado a Google Drive.", fatalError=False)
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

            # Reproyectar a UTM localmente
            if reproject_utm and epsg_code:
                feedback.pushInfo(f"Reproyectando vector descargado a {crs_dest.authid()} localmente...")
                out_reproj = os.path.join(out_dir, f"{out_filename}_reproj.gpkg")
                processing.run("native:reprojectlayer", {
                    'INPUT': shp_file,
                    'TARGET_CRS': crs_dest,
                    'OUTPUT': out_reproj
                }, context=context, feedback=feedback)
                shp_file = out_reproj

            # 9. Post-procesamiento Geopandas y gráficos
            feedback.pushInfo("Recortando geometría y generando reportes gráficos...")
            gdf = gpd.read_file(shp_file)
            
            # Recorte estricto local
            aoi_gdf = gpd.GeoDataFrame(index=[0], crs='EPSG:4326', geometry=[original_shapely_geom])
            aoi_gdf = aoi_gdf.to_crs(gdf.crs)
            
            # Sanear geometrías para evitar TopologyException
            gdf.geometry = gdf.geometry.make_valid()
            aoi_gdf.geometry = aoi_gdf.geometry.make_valid()
            
            gdf = gpd.clip(gdf, aoi_gdf)
            
            # Corrección de tildes para el shapefile exportado
            if "Cobertura" in gdf.columns and "Bosque_2000_2025" in out_filename:
                gdf['Cobertura'] = gdf['Cobertura'].str.replace('Perdida bosque', 'Pérdida bosque')
            
            # Guardar cambios en GeoPackage
            gpkg_file = os.path.join(out_dir, f"{out_filename}_final.gpkg")
            if not gdf.empty:
                gdf.to_file(gpkg_file, driver='GPKG', encoding='utf-8')
            shp_file = gpkg_file

            chart_logic = GfwCharts(out_dir, producto_nombre)

            if "Bosque" in producto_nombre and "Pérdida" not in producto_nombre and "Ganancia" not in producto_nombre:
                chart_logic.generate_bosque_charts(gdf, shp_file)
            elif "Pérdida" in producto_nombre:
                chart_logic.generate_perdida_charts(gdf, shp_file)
            elif "Ganancia" in producto_nombre:
                chart_logic.generate_ganancia_charts(gdf, shp_file)

            feedback.setProgress(100)
            feedback.pushInfo("¡Proceso completado con éxito! Revisa la carpeta seleccionada.")

            # 10. Cargar en QGIS y aplicar simbología
            feedback.pushInfo("Cargando capa en QGIS y aplicando simbología...")

            vlayer = QgsVectorLayer(shp_file, out_filename, "ogr")
            if vlayer.isValid():
                if "Bosque_2000_2025" in out_filename:
                    
                    # Root rule vacía para agrupar
                    root_rule = QgsRuleBasedRenderer.Rule(None)
                    renderer = QgsRuleBasedRenderer(root_rule)
                    
                    # 1. Bosque al 2025 (Verde)
                    symbol_bosque = QgsSymbol.defaultSymbol(vlayer.geometryType())
                    symbol_bosque.setColor(QColor(51, 160, 44))
                    symbol_bosque.symbolLayer(0).setStrokeColor(QColor(35, 35, 35))
                    symbol_bosque.symbolLayer(0).setStrokeWidth(0.26)
                    
                    rule_bosque = QgsRuleBasedRenderer.Rule(symbol_bosque)
                    rule_bosque.setFilterExpression('"Cobertura" = \'Bosque al 2025\'')
                    rule_bosque.setLabel('Bosque al 2025')
                    renderer.rootRule().appendChild(rule_bosque)
                    
                    # 2. Pérdidas por año (Naranja a Rojo)
                    start_year = 2001
                    end_year = int(year_found) if str(year_found).isdigit() else 2025
                    total_years = max(1, end_year - start_year)
                    
                    for y in range(start_year, end_year + 1):
                        # Interpolar de naranja (255, 165, 0) a rojo (255, 0, 0)
                        t = (y - start_year) / total_years
                        g = int(165 - (165 * t))
                        
                        symbol_loss = QgsSymbol.defaultSymbol(vlayer.geometryType())
                        symbol_loss.setColor(QColor(255, g, 0))
                        symbol_loss.symbolLayer(0).setStrokeColor(QColor(35, 35, 35))
                        symbol_loss.symbolLayer(0).setStrokeWidth(0.26)
                        
                        rule_loss = QgsRuleBasedRenderer.Rule(symbol_loss)
                        rule_loss.setFilterExpression(f'"Cobertura" = \'Pérdida bosque {y}\'')
                        rule_loss.setLabel(f'Pérdida {y}')
                        renderer.rootRule().appendChild(rule_loss)
                    
                    vlayer.setRenderer(renderer)
                    save_qml_sidecar(vlayer, shp_file)

                elif "Perdida" in out_filename:
                    symbol = QgsSymbol.defaultSymbol(vlayer.geometryType())
                    symbol.setColor(QColor('red'))
                    symbol.symbolLayer(0).setStrokeColor(QColor("transparent"))
                    vlayer.setRenderer(QgsSingleSymbolRenderer(symbol))
                    save_qml_sidecar(vlayer, shp_file)
                    
                elif "Ganancia" in out_filename:
                    symbol = QgsSymbol.defaultSymbol(vlayer.geometryType())
                    symbol.setColor(QColor('blue'))
                    symbol.symbolLayer(0).setStrokeColor(QColor("transparent"))
                    vlayer.setRenderer(QgsSingleSymbolRenderer(symbol))
                    save_qml_sidecar(vlayer, shp_file)

                QgsProject.instance().addMapLayer(vlayer)

            # Processing outputs dict
            return {self.OUT_FOLDER: out_dir}
