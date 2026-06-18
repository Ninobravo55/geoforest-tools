import os
import geopandas as gpd
import processing
import shutil
import pandas as pd
from qgis.core import QgsFeatureRequest, QgsProject
from .geobosques_charts import GeobosquesCharts
from .gfw_charts import GfwCharts
from .glcluc_charts import GlclucCharts
from ..utils.style_utils import save_qml_sidecar

from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFolderDestination,
    QgsProcessingException,
    QgsVectorLayer,
    QgsCategorizedSymbolRenderer,
    QgsRendererCategory,
    QgsSymbol,
    QgsSingleSymbolRenderer,
    QgsProcessingParameterCrs,
    QgsProcessingParameterMapLayer,
    QgsMapLayer,
    QgsRuleBasedRenderer
)
from qgis.PyQt.QtCore import QCoreApplication
from qgis.PyQt.QtGui import QColor

class ReportGeneratorAlgorithm(QgsProcessingAlgorithm):
    INPUT_VECTOR = 'INPUT_VECTOR'
    INPUT_AREA = 'INPUT_AREA'
    TARGET_CRS = 'TARGET_CRS'
    PRODUCT_TYPE = 'PRODUCT_TYPE'
    OUT_FOLDER = 'OUT_FOLDER'

    def __init__(self):
        super().__init__()
        self.productos = [
            "GEOBOSQUES - Bosque y No bosque",
            "GEOBOSQUES - Pérdida de Bosque Actual",
            "GEOBOSQUES - Alerta Temprana 2025",
            "GEOBOSQUES - Alerta Temprana 2026",
            "GFW - Bosque del 2000 y 2025",
            "GFW - Pérdida de Bosque 2001 - 2025",
            "GFW - Ganancia de Bosque 2001 - 2012",
            "GLAD - Altura dosel 2000",
            "GLAD - Altura dosel 2020",
            "ETH - Altura dosel 2020",
            "ETH - Desviación estandar",
            "Meta 1m - Altura global",
            "GLCLUC - Ganancia de altura del bosque 2000-2020",
            "GLCLUC - Dinámica forestal 2000-2020"
        ]

    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    def createInstance(self):
        return ReportGeneratorAlgorithm()

    def name(self):
        return 'report_generator'

    def displayName(self):
        return self.tr('Generador de Reportes y Gráficos (Local)')

    def group(self):
        return self.tr('Herramientas Complementarias')

    def groupId(self):
        return 'herramientas_complementarias'

    def shortHelpString(self):
        help_text = (
            "<b>Descripción del Proceso:</b><br>"
            "Esta herramienta genera reportes estadísticos, tablas Excel (.xlsx) y gráficos (.jpg) "
            "a partir de un archivo Shapefile que hayas descargado previamente desde Google Drive "
            "o Google Cloud Storage tras usar las herramientas principales del plugin.<br><br>"
            "<b>Instrucciones:</b><br>"
            "1. Carga el archivo .shp descargado.<br>"
            "2. Selecciona exactamente el mismo producto que usaste en la nube.<br>"
            "3. Elige la carpeta donde se guardarán los resultados.<br>"
        )
        return self.tr(help_text)

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterMapLayer(
                self.INPUT_VECTOR,
                self.tr('Capa Descargada desde la Nube (Seleccionar del Panel)'),
                [QgsProcessing.TypeVectorPolygon, QgsProcessing.TypeRaster]
            )
        )
        
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.INPUT_AREA,
                self.tr('Área de Interés (Polígono de Recorte)'),
                [QgsProcessing.TypeVectorPolygon],
                optional=True
            )
        )
        
        self.addParameter(
            QgsProcessingParameterEnum(
                self.PRODUCT_TYPE,
                self.tr('Producto Analizado'),
                options=self.productos,
                defaultValue=0
            )
        )
        
        self.addParameter(
            QgsProcessingParameterCrs(
                self.TARGET_CRS,
                self.tr('CRS de Destino para el Vector (Reproyección)'),
                defaultValue='EPSG:32718'
            )
        )
        
        self.addParameter(
            QgsProcessingParameterFolderDestination(
                self.OUT_FOLDER,
                self.tr('Carpeta de Salida para Reportes')
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        input_layer = self.parameterAsLayer(parameters, self.INPUT_VECTOR, context)
        area_layer = self.parameterAsSource(parameters, self.INPUT_AREA, context)
        target_crs = self.parameterAsCrs(parameters, self.TARGET_CRS, context)
        out_folder = self.parameterAsString(parameters, self.OUT_FOLDER, context)
        prod_idx = self.parameterAsEnum(parameters, self.PRODUCT_TYPE, context)
        
        if not input_layer:
            raise QgsProcessingException("Debe seleccionar una capa de entrada válida desde el panel de capas.")
            
        if not out_folder:
            raise QgsProcessingException("Debe seleccionar una carpeta de salida.")
            
        feedback.pushInfo(f"Capa de entrada: {input_layer.name()}")
        
        if input_layer.type() == QgsMapLayer.RasterLayer:
            feedback.setProgressText("Paso 1/5: Detectado archivo Raster. Iniciando poligonización local (Esto puede tomar varios minutos)...")
            feedback.setProgress(10)
            out_shp = os.path.join(out_folder, "raster_vectorizado_temp.gpkg")
            processing.run("gdal:polygonize", {
                'INPUT': input_layer,
                'BAND': 1,
                'FIELD': 'DN',
                'EIGHT_CONNECTEDNESS': False,
                'EXTRA': '',
                'OUTPUT': out_shp
            }, context=context, feedback=feedback)
            
            shp_file = out_shp
            feedback.setProgressText("Poligonización completada con éxito.")
            feedback.setProgress(30)
        else:
            # Si ya es vector
            shp_file = input_layer.dataProvider().dataSourceUri().split('|')[0]
            
        # 1. Reproyectar NATIVAMENTE en QGIS para evitar que Geopandas (pyproj) crashee
        if target_crs and target_crs.isValid():
            layer_to_reproject = shp_file
            if not isinstance(layer_to_reproject, str) or not os.path.exists(layer_to_reproject):
                # Caso extremo si source() no es una ruta válida
                layer_to_reproject = input_layer
                
            crs_authid = input_layer.crs().authid() if not shp_file.endswith('gpkg') else QgsVectorLayer(shp_file, "temp", "ogr").crs().authid()
            
            if crs_authid != target_crs.authid():
                feedback.setProgressText(f"Paso 2/5: Reproyectando vector a {target_crs.authid()}...")
                feedback.setProgress(40)
                out_reproj = os.path.join(out_folder, "raster_vectorizado_reproj.gpkg")
                processing.run("native:reprojectlayer", {
                    'INPUT': layer_to_reproject,
                    'TARGET_CRS': target_crs,
                    'OUTPUT': out_reproj
                }, context=context, feedback=feedback)
                shp_file = out_reproj
                
        # 1.5 Recortar vector con el polígono de área de interés
        if area_layer is not None:
            feedback.setProgressText("Paso 3/5: Recortando con el Área de Interés (Clip)...")
            feedback.setProgress(50)
            out_clip = os.path.join(out_folder, "vector_clip.gpkg")
            
            # Reproyectar el polígono de recorte al mismo CRS de destino para que el clip sea perfecto
            area_reproj = processing.run("native:reprojectlayer", {
                'INPUT': parameters[self.INPUT_AREA],
                'TARGET_CRS': target_crs if (target_crs and target_crs.isValid()) else 'EPSG:4326',
                'OUTPUT': 'memory:'
            }, context=context, feedback=feedback)['OUTPUT']
            
            # Evitar que polígonos corruptos minúsculos detengan el proceso de recorte
            original_check = context.invalidGeometryCheck()
            context.setInvalidGeometryCheck(QgsFeatureRequest.GeometrySkipInvalid)
            
            try:
                processing.run("native:clip", {
                    'INPUT': shp_file,
                    'OVERLAY': area_reproj,
                    'OUTPUT': out_clip
                }, context=context, feedback=feedback)
            finally:
                context.setInvalidGeometryCheck(original_check)
            
            shp_file = out_clip

        # 2. Hack para evitar Access Violation si el archivo es SHP
        prj_path = shp_file.replace('.shp', '.prj') if shp_file.lower().endswith('.shp') else ""
        bak_path = prj_path + '.bak' if prj_path else ""
        if prj_path and os.path.exists(prj_path):
            shutil.copy(prj_path, bak_path)
            os.remove(prj_path) # Ocultar CRS a geopandas
            
        try:
            feedback.setProgressText("Paso 4/5: Leyendo datos y filtrando polígonos inválidos (Geopandas)...")
            feedback.setProgress(70)
            gdf = gpd.read_file(shp_file)
        except Exception as e:
            raise QgsProcessingException(f"Error al leer el archivo con Geopandas: {str(e)}")
            
        if gdf.empty:
            raise QgsProcessingException("El archivo vectorial está vacío (probablemente el área seleccionada no contiene bosque).")
                
        # Reconstruir columnas si viene de TIF
        if 'DN' in gdf.columns and 'NAME' not in gdf.columns and 'TIPO' not in gdf.columns:
            feedback.setProgressText("Paso 5/5: Generando reportes, tablas Excel y gráficos HTML...")
            feedback.setProgress(85)
            
            # Asegurar que DN sea entero para que el mapeo funcione correctamente
            gdf['DN'] = pd.to_numeric(gdf['DN'], errors='coerce').fillna(0).astype(int)
            
            # Filtrar valor cero (fondos nulos exportados por GEE)
            gdf = gdf[gdf['DN'] > 0].copy()
            
            # Como ocultamos el CRS, geopandas no crasheará, pero las coordenadas ya están en metros!
            gdf['Area_ha'] = gdf.geometry.area / 10000.0
                
            if prod_idx == 0:
                mapping = {1: 'Agua', 2: 'Bosque al 2024', 3: 'Deforestacion del 2001 al 2024', 4: 'No Bosque al 2000'}
                gdf['NAME'] = gdf['DN'].map(mapping).fillna('Sin informacion')
            elif prod_idx == 1:
                gdf['Anio'] = gdf['DN'] + 1999
                gdf['Clase'] = gdf['Area_ha'].apply(lambda a: '<1 ha' if a < 1 else ('1-5 ha' if a < 5 else ('5-50 ha' if a < 50 else ('50-500 ha' if a < 500 else '>500 ha'))))
            elif prod_idx in [2, 3]:
                year_alerta = 2025 if prod_idx == 2 else 2026
                fechas = pd.to_datetime(f'{year_alerta}-01-01') + pd.to_timedelta(gdf['DN'] - 1, unit='D')
                gdf['Mes_num'] = fechas.dt.month
                gdf['Anio'] = fechas.dt.year
                gdf['Fecha'] = fechas.dt.strftime('%Y-%m-%d')
            elif prod_idx == 4:
                def map_gfw(dn):
                    if dn == 2000: return 'Bosque al 2025'
                    elif dn > 2000: return f'Pérdida bosque {int(dn)}'
                    else: return 'Sin informacion'
                gdf['Cobertura'] = gdf['DN'].apply(map_gfw)
            elif prod_idx == 5:
                gdf['Anio'] = gdf['DN'] + 2000
                gdf['Clase'] = gdf['Area_ha'].apply(lambda a: '<1 ha' if a < 1 else ('1-5 ha' if a < 5 else ('5-50 ha' if a < 50 else ('50-500 ha' if a < 500 else '>500 ha'))))
            elif prod_idx == 6:
                gdf['Anio_Gan'] = gdf['DN']
                gdf['Clase'] = gdf['Area_ha'].apply(lambda a: '<1 ha' if a < 1 else ('1-5 ha' if a < 5 else ('5-50 ha' if a < 50 else ('50-500 ha' if a < 500 else '>500 ha'))))
            elif prod_idx in [7, 8]:
                gdf['Altura'] = gdf['DN']
            elif prod_idx == 9:
                gdf['Clase'] = gdf['DN']
            elif prod_idx == 10:
                mapping = {1: 'Bosque estable', 2: 'Perdida forestal', 3: 'Ganancia forestal', 4: 'Degradacion'}
                gdf['TIPO'] = gdf['DN'].map(mapping).fillna('Desconocido')
                
        # Normalizar tildes si los datos vienen como vector directamente
        if 'Cobertura' in gdf.columns:
            gdf['Cobertura'] = gdf['Cobertura'].str.replace('Perdida bosque', 'Pérdida bosque')
                
        # Guardar el vector procesado
        base_name = os.path.basename(shp_file).replace('.shp', '').replace('.tif', '').replace('.gpkg', '')
        new_shp = os.path.join(out_folder, f"{base_name}_procesado.gpkg")
        gdf.to_file(new_shp, driver='GPKG', encoding='utf-8')
        
        # 4. Restaurar el CRS original si se usó el hack
        if bak_path and os.path.exists(bak_path):
            if os.path.exists(prj_path):
                os.remove(prj_path)
            shutil.move(bak_path, prj_path) # Restaurar original
            
        shp_file = new_shp
        vlayer = QgsVectorLayer(shp_file, base_name, "ogr")
        QgsProject.instance().addMapLayer(vlayer)
            
        feedback.pushInfo("Generando reportes gráficos y estadísticos...")
        
        # Enrutamiento según el producto seleccionado
        if prod_idx <= 3:
            if prod_idx == 0: prod_name_gb = "Bosque y No bosque"
            elif prod_idx == 1: prod_name_gb = "Pérdida"
            elif prod_idx == 2: prod_name_gb = "Alerta Temprana 2025"
            else: prod_name_gb = "Alerta Temprana 2026"
            
            logic = GeobosquesCharts(out_folder, prod_name_gb)
            
            if prod_idx == 0: logic.generate_bosque_charts(gdf, None)
            elif prod_idx == 1: logic.generate_perdida_charts(gdf, None)
            else: logic.generate_alerta_charts(gdf, None)
            
        elif prod_idx <= 6:
            if prod_idx == 4: prod_name_gfw = "Bosque del 2000 y"
            elif prod_idx == 5: prod_name_gfw = "Pérdida"
            else: prod_name_gfw = "Ganancia"
            
            logic = GfwCharts(out_folder, prod_name_gfw)
            
            if prod_idx == 4: logic.generate_bosque_charts(gdf, None)
            elif prod_idx == 5: logic.generate_perdida_charts(gdf, None)
            else: logic.generate_ganancia_charts(gdf, None)
            
        else:
            if prod_idx == 7: prod_name_gl = "altura_foresta_2000"
            elif prod_idx == 8: prod_name_gl = "altura_foresta_2020"
            elif prod_idx == 9: prod_name_gl = "Ganancia_foresta_2020"
            else: prod_name_gl = "Dinamica_forestal_2000_2020"
            
            logic = GlclucCharts(out_folder, prod_name_gl)
            if prod_idx in [7, 8]: logic.generate_altura_charts(gdf)
            elif prod_idx == 9: logic.generate_ganancia_charts(gdf)
            elif prod_idx == 10: logic.generate_dinamica_charts(gdf)
            
        feedback.pushInfo("Reportes y Excel generados exitosamente.")
        
        # Estilizar
        feedback.pushInfo("Aplicando simbología QGIS...")
        if vlayer and vlayer.isValid():
            try:
                # GeoBosques
                if prod_idx == 0:
                    categories = []
                    colores_dict = {'Agua': '#1565c0', 'Bosque al 2024': '#9cbe62', 'Deforestacion del 2001 al 2024': '#ff0000', 'No Bosque al 2000': '#e6c46a', 'Sin informacion': '#b2b2b2'}
                    for nombre, color_hex in colores_dict.items():
                        symbol = QgsSymbol.defaultSymbol(vlayer.geometryType())
                        symbol.setColor(QColor(color_hex))
                        symbol.symbolLayer(0).setStrokeColor(QColor("transparent"))
                        categories.append(QgsRendererCategory(nombre, symbol, nombre))
                    vlayer.setRenderer(QgsCategorizedSymbolRenderer('NAME', categories))
                elif prod_idx == 1:
                    symbol = QgsSymbol.defaultSymbol(vlayer.geometryType())
                    symbol.setColor(QColor('red'))
                    symbol.symbolLayer(0).setStrokeColor(QColor("transparent"))
                    vlayer.setRenderer(QgsSingleSymbolRenderer(symbol))
                elif prod_idx in [2, 3]:
                    symbol = QgsSymbol.defaultSymbol(vlayer.geometryType())
                    symbol.setColor(QColor('#ff5500'))
                    symbol.symbolLayer(0).setStrokeColor(QColor("transparent"))
                    vlayer.setRenderer(QgsSingleSymbolRenderer(symbol))
                
                # GFW
                elif prod_idx == 4:
                    root_rule = QgsRuleBasedRenderer.Rule(None)
                    renderer = QgsRuleBasedRenderer(root_rule)
                    
                    symbol_bosque = QgsSymbol.defaultSymbol(vlayer.geometryType())
                    symbol_bosque.setColor(QColor(51, 160, 44))
                    symbol_bosque.symbolLayer(0).setStrokeColor(QColor("transparent"))
                    rule_bosque = QgsRuleBasedRenderer.Rule(symbol_bosque)
                    rule_bosque.setFilterExpression('"Cobertura" = \'Bosque al 2025\'')
                    rule_bosque.setLabel('Bosque al 2025')
                    renderer.rootRule().appendChild(rule_bosque)
                    
                    # Interpolar desde 2001 hasta 2025
                    for y in range(2001, 2026):
                        t = (y - 2001) / max(1, 2025 - 2001)
                        g = int(165 - (165 * t))
                        symbol_loss = QgsSymbol.defaultSymbol(vlayer.geometryType())
                        symbol_loss.setColor(QColor(255, g, 0))
                        symbol_loss.symbolLayer(0).setStrokeColor(QColor("transparent"))
                        
                        rule_loss = QgsRuleBasedRenderer.Rule(symbol_loss)
                        rule_loss.setFilterExpression(f'"Cobertura" = \'Pérdida bosque {y}\'')
                        rule_loss.setLabel(f'Pérdida {y}')
                        renderer.rootRule().appendChild(rule_loss)
                    
                    vlayer.setRenderer(renderer)
                elif prod_idx == 5:
                    symbol = QgsSymbol.defaultSymbol(vlayer.geometryType())
                    symbol.setColor(QColor('red'))
                    symbol.symbolLayer(0).setStrokeColor(QColor("transparent"))
                    vlayer.setRenderer(QgsSingleSymbolRenderer(symbol))
                elif prod_idx == 6:
                    symbol = QgsSymbol.defaultSymbol(vlayer.geometryType())
                    symbol.setColor(QColor('blue'))
                    symbol.symbolLayer(0).setStrokeColor(QColor("transparent"))
                    vlayer.setRenderer(QgsSingleSymbolRenderer(symbol))
                
                # GLCLUC
                elif prod_idx in [7, 8]:
                    symbol = QgsSymbol.defaultSymbol(vlayer.geometryType())
                    symbol.setColor(QColor('#005a00'))
                    vlayer.setRenderer(QgsSingleSymbolRenderer(symbol))
                elif prod_idx == 9:
                    symbol = QgsSymbol.defaultSymbol(vlayer.geometryType())
                    symbol.setColor(QColor('#0000ff'))
                    vlayer.setRenderer(QgsSingleSymbolRenderer(symbol))
                elif prod_idx == 10:
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
                    
                save_qml_sidecar(vlayer, shp_file)
                vlayer.triggerRepaint()
            except Exception as style_e:
                feedback.pushInfo(f"No se pudo aplicar la simbología: {str(style_e)}")
            
        feedback.pushInfo("¡Proceso Finalizado!")
        return {}
