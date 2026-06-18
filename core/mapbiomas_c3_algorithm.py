import os
import json
import ee
import requests
import processing
import pandas as pd
from qgis.core import (
    QgsProcessing, QgsProcessingAlgorithm, QgsProcessingParameterExtent,
    QgsProcessingParameterEnum, QgsProcessingParameterCrs,
    QgsProcessingParameterFolderDestination, QgsProcessingException,
    QgsProject, QgsProcessingParameterMapLayer, QgsVectorLayer,
    QgsCategorizedSymbolRenderer, QgsSymbol, QgsRendererCategory
)
from qgis.PyQt.QtCore import QCoreApplication
from qgis.PyQt.QtGui import QColor

from ..utils.aoi_builder import build_aoi, to_ee_geometry, safe_simplify
from ..utils.gee_init import ensure_gee_initialized

class MapbiomasC3Algorithm(QgsProcessingAlgorithm):
    INPUT_LAYER = 'INPUT_LAYER'
    INPUT_EXTENT = 'INPUT_EXTENT'
    PRODUCT = 'PRODUCT'
    YEAR = 'YEAR'
    TRANSITION = 'TRANSITION'
    EPSG = 'EPSG'
    OUT_FOLDER = 'OUT_FOLDER'

    def __init__(self):
        super().__init__()
        self.products = [
            "Cobertura y uso suelo 1985 - 2024",
            "Transiciones de cambio cobertura"
        ]
        self.years = [str(y) for y in range(1985, 2025)]
        self.transitions = [
            'Transiciones del 1985 al 2024',
            'Transiciones del 1990 al 2024',
            'Transiciones del 2000 al 2024',
            'Transiciones del 2008 al 2024',
            'Transiciones del 2010 al 2024',
            'Transiciones del 2023 al 2024'
        ]
        self.mapbiomas_info = {
            3: {'name': 'Bosque', 'color': '#1f8d49'},
            4: {'name': 'Bosque seco', 'color': '#7dc975'},
            5: {'name': 'Manglar', 'color': '#04381d'},
            6: {'name': 'Bosque inundable', 'color': '#026975'},
            11: {'name': 'Zona pantanosa o pastizal inundable', 'color': '#519799'},
            12: {'name': 'Pastizal / Herbazal', 'color': '#d6bc74'},
            13: {'name': 'Otra formación no boscosa', 'color': '#d89f5c'},
            15: {'name': 'Pasto', 'color': '#edde8e'},
            18: {'name': 'Agricultura', 'color': '#e974ed'},
            21: {'name': 'Mosaico agropecuario', 'color': '#ffefc3'},
            23: {'name': 'Playa', 'color': '#ffa07a'},
            24: {'name': 'Infraestructura urbana', 'color': '#d4271e'},
            25: {'name': 'Otra área sin vegetación', 'color': '#db4d4f'},
            27: {'name': 'No observado', 'color': '#ffffff'},
            29: {'name': 'Afloramiento rocoso', 'color': '#ffaa5f'},
            30: {'name': 'Minería', 'color': '#9c0027'},
            31: {'name': 'Acuicultura', 'color': '#091077'},
            32: {'name': 'Salina costera', 'color': '#fc8114'},
            33: {'name': 'Río, lago u océano', 'color': '#2532e4'},
            34: {'name': 'Glaciar', 'color': '#93dfe6'},
            35: {'name': 'Palma aceitera', 'color': '#9065d0'},
            40: {'name': 'Arroz', 'color': '#c71585'},
            61: {'name': 'Salar', 'color': '#f5d5d5'},
            66: {'name': 'Matorral', 'color': '#a89358'},
            68: {'name': 'Otra área natural sin vegetación', 'color': '#E97A7A'},
            70: {'name': 'Loma costera', 'color': '#be9e00'},
            72: {'name': 'Otros cultivos', 'color': '#910046'}
        }

    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    def createInstance(self):
        return MapbiomasC3Algorithm()

    def name(self):
        return 'mapbiomas_c3_analysis'

    def displayName(self):
        return self.tr('MapBiomas cobertura Perú C3')

    def group(self):
        return self.tr('Dinámica de cobertura')

    def groupId(self):
        return 'dinamica_cobertura'

    def shortHelpString(self):
        help_text = (
            "<b>Descripción del Proceso:</b><br>"
            "MapBiomas Perú es una iniciativa científica y colaborativa que genera mapas anuales de cobertura "
            "y uso del suelo de todo el territorio peruano, utilizando imágenes satelitales Landsat, inteligencia "
            "artificial y Google Earth Engine. Actualmente la Colección 3 abarca el período 1985-2024.<br><br>"
            "Esta herramienta permite descargar, reproyectar y vectorizar datos de MapBiomas Perú Colección 3 "
            "(Cobertura y Transiciones).<br><br>"
            "<b>Metodología:</b> <a href='https://peru.mapbiomas.org/atbd-entienda-cada-etapa/'>https://peru.mapbiomas.org/atbd-entienda-cada-etapa/</a><br>"
            "<b>Fuente:</b> <a href='https://peru.mapbiomas.org/colecciones-de-mapbiomas-peru/'>https://peru.mapbiomas.org/colecciones-de-mapbiomas-peru/</a>"
        )
        return self.tr(help_text)

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterMapLayer(
                self.INPUT_LAYER,
                self.tr('Área de interés (Capa Vectorial)'),
                [QgsProcessing.TypeVectorPolygon],
                optional=True
            )
        )
        self.addParameter(QgsProcessingParameterExtent(self.INPUT_EXTENT, self.tr('O dibujar un Recuadro'), optional=True))
        
        self.addParameter(QgsProcessingParameterEnum(self.PRODUCT, self.tr('Seleccionar Producto'), options=self.products, defaultValue=0))
        self.addParameter(QgsProcessingParameterEnum(self.YEAR, self.tr('Año de cobertura (Si eligió Cobertura)'), options=self.years, defaultValue=len(self.years)-1))
        self.addParameter(QgsProcessingParameterEnum(self.TRANSITION, self.tr('Periodo (Si eligió Transiciones)'), options=self.transitions, defaultValue=0))
        
        self.addParameter(QgsProcessingParameterCrs(self.EPSG, self.tr('CRS de Destino'), defaultValue='EPSG:32718'))
        self.addParameter(QgsProcessingParameterFolderDestination(self.OUT_FOLDER, self.tr('Carpeta de Destino Local'), optional=False))

    def processAlgorithm(self, parameters, context, feedback):
        product_idx = self.parameterAsEnum(parameters, self.PRODUCT, context)
        year_idx = self.parameterAsEnum(parameters, self.YEAR, context)
        trans_idx = self.parameterAsEnum(parameters, self.TRANSITION, context)
        crs_dest = self.parameterAsCrs(parameters, self.EPSG, context)
        out_folder = self.parameterAsString(parameters, self.OUT_FOLDER, context)
        
        if not out_folder:
            raise QgsProcessingException("Debe seleccionar una Carpeta de Destino.")
            
        target_crs_str = crs_dest.authid() if crs_dest.isValid() else 'EPSG:4326'

        # AOI centralizado (utils/aoi_builder)
        aoi = build_aoi(self, parameters, context, feedback)
        geom_union = aoi.geom_4326

        geom_union_simp = safe_simplify(geom_union, 0.0001)
        geo_dict = json.loads(geom_union_simp.asJson())

        ensure_gee_initialized(feedback)

        # Geometría EE sin truncar GeometryCollection — fix B-01
        geom_ee = to_ee_geometry(geom_union_simp, ee, geo_dict)

        if product_idx == 0:
            year_str = self.years[year_idx]
            asset_path = 'projects/mapbiomas-public/assets/peru/collection3/mapbiomas_peru_collection3_integration_v1'
            band_name = f'classification_{year_str}'
            out_filename = f"MapBiomas_Cobertura_{year_str}"
        else:
            trans_str = self.transitions[trans_idx]
            years_part = trans_str.replace('Transiciones del ', '').replace(' al ', '_')
            asset_path = 'projects/mapbiomas-public/assets/peru/collection3/mapbiomas_peru_collection3_transitions_v1'
            band_name = f'transitions_{years_part}'
            out_filename = f"MapBiomas_Transiciones_{years_part}"

        feedback.pushInfo(f"Descargando {out_filename} desde GEE...")
        
        try:
            img = ee.Image(asset_path).select(band_name).clip(geom_ee)
            url = img.getDownloadURL({
                'name': out_filename,
                'crs': 'EPSG:4326',
                'scale': 30,
                'region': geom_ee,
                'format': 'GEO_TIFF'
            })
            r = requests.get(url, stream=True)
            r.raise_for_status()
            
            tif_file = os.path.join(out_folder, f"{out_filename}.tif")
            with open(tif_file, 'wb') as f:
                for chunk in r.iter_content(chunk_size=1024):
                    if chunk: f.write(chunk)
                    
            if not os.path.exists(tif_file): raise Exception("Error al guardar TIFF.")
        except Exception as e:
            raise QgsProcessingException(f"Error descargando de GEE: {str(e)}\nIntenta con un área más pequeña.")

        feedback.setProgress(30)
        feedback.pushInfo("Reproyectando Raster descargado...")
        tif_proj = os.path.join(out_folder, f"{out_filename}_proj.tif")
        processing.run("gdal:warpreproject", {
            'INPUT': tif_file,
            'SOURCE_CRS': 'EPSG:4326',
            'TARGET_CRS': target_crs_str,
            'NODATA': 0,
            'OUTPUT': tif_proj
        }, context=context, feedback=feedback)

        feedback.setProgress(50)
        feedback.pushInfo("Poligonizando Raster (Conversión a Vector)...")
        vec_file = os.path.join(out_folder, f"{out_filename}.gpkg")
        processing.run("gdal:polygonize", {
            'INPUT': tif_proj,
            'BAND': 1,
            'FIELD': 'codigo',
            'EIGHT_CONNECTEDNESS': False,
            'OUTPUT': vec_file
        }, context=context, feedback=feedback)

        feedback.setProgress(70)
        feedback.pushInfo("Agregando campos y calculando áreas...")
        
        vlayer = QgsVectorLayer(vec_file, out_filename, "ogr")
        
        params_calc = {
            'INPUT': vec_file,
            'FIELD_NAME': 'area_ha',
            'FIELD_TYPE': 0, # Float
            'FIELD_LENGTH': 10,
            'FIELD_PRECISION': 4,
            'FORMULA': '$area / 10000',
            'OUTPUT': 'memory:'
        }
        res_calc = processing.run("native:fieldcalculator", params_calc, context=context, feedback=feedback)['OUTPUT']
        
        # Agregamos campo Cobertura
        params_calc2 = {
            'INPUT': res_calc,
            'FIELD_NAME': 'Cobertura',
            'FIELD_TYPE': 2, # String
            'FIELD_LENGTH': 100,
            'FIELD_PRECISION': 0,
            'FORMULA': "''",
            'OUTPUT': 'memory:'
        }
        final_layer = processing.run("native:fieldcalculator", params_calc2, context=context, feedback=feedback)['OUTPUT']
        
        feedback.pushInfo("Decodificando códigos MapBiomas...")
        final_layer.startEditing()
        idx_cod = final_layer.fields().lookupField('codigo')
        idx_cob = final_layer.fields().lookupField('Cobertura')
        
        for f in final_layer.getFeatures():
            cod = int(f.attributes()[idx_cod])
            if product_idx == 0:
                name = self.mapbiomas_info.get(cod, {'name': f"Clase {cod}"})['name']
            else:
                initial_class = cod // 100
                final_class = cod % 100
                name_init = self.mapbiomas_info.get(initial_class, {'name': str(initial_class)})['name']
                name_fin = self.mapbiomas_info.get(final_class, {'name': str(final_class)})['name']
                if initial_class == final_class:
                    name = f"Estable: {name_init}"
                else:
                    name = f"{name_init} -> {name_fin}"
            final_layer.changeAttributeValue(f.id(), idx_cob, name)
            
        final_layer.commitChanges()
        
        # Save final layer
        final_shp = os.path.join(out_folder, f"{out_filename}_Final.shp")
        params_save = {
            'INPUT': final_layer,
            'OUTPUT': final_shp
        }
        processing.run("native:savefeatures", params_save, context=context, feedback=feedback)
        
        vlayer_final = QgsVectorLayer(final_shp, f"{out_filename} (Vector)", "ogr")
        
        # Crear diccionario inverso de colores
        name_to_color = {}
        for info in self.mapbiomas_info.values():
            name_to_color[info['name']] = info['color']
            name_to_color[f"Estable: {info['name']}"] = info['color']
            
        # Aplicar simbología categorizada
        idx_cob = vlayer_final.fields().lookupField('Cobertura')
        unique_values = vlayer_final.uniqueValues(idx_cob)
        categories = []
        for val in unique_values:
            val_str = str(val)
            color_hex = '#cccccc' # Default if not found
            
            if val_str in name_to_color:
                color_hex = name_to_color[val_str]
            elif "->" in val_str:
                fin_name = val_str.split("->")[-1].strip()
                if fin_name in name_to_color:
                    color_hex = name_to_color[fin_name]
                    
            symbol = QgsSymbol.defaultSymbol(vlayer_final.geometryType())
            color = QColor(color_hex)
            symbol.setColor(color)
            if symbol.symbolLayerCount() > 0:
                symbol.symbolLayer(0).setStrokeColor(color)
                
            category = QgsRendererCategory(val, symbol, val_str)
            categories.append(category)
            
        renderer = QgsCategorizedSymbolRenderer('Cobertura', categories)
        vlayer_final.setRenderer(renderer)
        vlayer_final.triggerRepaint()
        
        # Load to canvas
        QgsProject.instance().addMapLayer(vlayer_final)

        feedback.pushInfo("Generando estadísticas...")
        stats_dict = {}
        for f in final_layer.getFeatures():
            cob = f['Cobertura']
            area = f['area_ha']
            stats_dict[cob] = stats_dict.get(cob, 0) + area
            
        col_name = "Transición" if product_idx == 1 else "Clase de Cobertura"
        df = pd.DataFrame(list(stats_dict.items()), columns=[col_name, "Área (Hectáreas)"])
        df = df.sort_values(by="Área (Hectáreas)", ascending=False)
        csv_path = os.path.join(out_folder, f"{out_filename}_Estadisticas.csv")
        df.to_csv(csv_path, index=False, encoding='utf-8-sig')
        feedback.pushInfo(f"Estadísticas exportadas a: {csv_path}")

        return {self.OUT_FOLDER: out_folder}
