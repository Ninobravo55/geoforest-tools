import os
import json
import ee
import requests
import processing
import pandas as pd
import matplotlib
matplotlib.use("Agg")  # backend headless, thread-safe en worker de Processing
import matplotlib.pyplot as plt
from ..utils.aoi_builder import build_aoi, to_ee_geometry, safe_simplify
from ..utils.gee_init import ensure_gee_initialized
from qgis.core import (
    QgsProcessing, QgsProcessingAlgorithm, QgsProcessingParameterExtent,
    QgsProcessingParameterEnum, QgsProcessingParameterCrs,
    QgsProcessingParameterFolderDestination, QgsProcessingException,
    QgsProject, QgsProcessingParameterMapLayer, QgsProcessingParameterString,
    QgsVectorLayer,
    QgsCategorizedSymbolRenderer, QgsSymbol, QgsRendererCategory
)
from qgis.PyQt.QtCore import QCoreApplication
from qgis.PyQt.QtGui import QColor


class MapbiomasFireAlgorithm(QgsProcessingAlgorithm):
    INPUT_LAYER = 'INPUT_LAYER'
    INPUT_EXTENT = 'INPUT_EXTENT'
    PRODUCT = 'PRODUCT'
    YEAR = 'YEAR'
    FREQUENCY_BAND = 'FREQUENCY_BAND'
    EPSG = 'EPSG'
    OUT_FOLDER = 'OUT_FOLDER'

    def __init__(self):
        super().__init__()
        self.products = [
            "Área quemada anual 2013 - 2024",
            "Área quemada mensual 2013 - 2024",
            "Frecuencia de incendios 2013 - 2024",
            "Último año de incendio 2014 - 2025"
        ]

        self.frequency_bands = [
            'fire_frequency_2013_2013',
            'fire_frequency_2013_2014',
            'fire_frequency_2013_2015',
            'fire_frequency_2013_2016',
            'fire_frequency_2013_2017',
            'fire_frequency_2013_2018',
            'fire_frequency_2013_2019',
            'fire_frequency_2013_2020',
            'fire_frequency_2013_2021',
            'fire_frequency_2013_2022',
            'fire_frequency_2013_2023',
            'fire_frequency_2013_2024',
            'fire_frequency_2014_2024',
            'fire_frequency_2015_2024',
            'fire_frequency_2016_2024',
            'fire_frequency_2017_2024',
            'fire_frequency_2018_2024',
            'fire_frequency_2019_2024',
            'fire_frequency_2020_2024',
            'fire_frequency_2021_2024',
            'fire_frequency_2022_2024',
            'fire_frequency_2023_2024',
            'fire_frequency_2024_2024'
        ]

        self.meses = {
            1: 'Enero', 2: 'Febrero', 3: 'Marzo', 4: 'Abril',
            5: 'Mayo', 6: 'Junio', 7: 'Julio', 8: 'Agosto',
            9: 'Septiembre', 10: 'Octubre', 11: 'Noviembre', 12: 'Diciembre'
        }

        self.frecuencia_labels = {
            0: 'Nunca', 1: '1 vez', 2: '2 veces', 3: '3 veces',
            4: '4 veces', 5: '5 veces', 6: '6 veces', 7: '7 veces',
            8: '8 veces', 9: '9 veces', 10: '10 veces'
        }

        # Assets GEE MapBiomas Perú Fuego Colección 1
        self.assets = {
            0: 'projects/mapbiomas-public/assets/peru/fire/collection1/mapbiomas_peru_fire_collection1_annual_burned_v1',
            1: 'projects/mapbiomas-public/assets/peru/fire/collection1/mapbiomas_peru_fire_collection1_monthly_burned_v1',
            2: 'projects/mapbiomas-public/assets/peru/fire/collection1/mapbiomas_peru_fire_collection1_frequency_burned_v1',
            3: 'projects/mapbiomas-public/assets/peru/fire/collection1/mapbiomas_peru_fire_collection1_year_last_fire_v1'
        }

    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    def createInstance(self):
        return MapbiomasFireAlgorithm()

    def name(self):
        return 'mapbiomas_fire_analysis'

    def displayName(self):
        return self.tr('Monitoreo de Incendios - MapBiomas Perú')

    def group(self):
        return self.tr('Monitoreo de incendio')

    def groupId(self):
        return 'monitoreo_incendio'

    def shortHelpString(self):
        help_text = (
            "<b>Descripción del Proceso:</b><br>"
            "MapBiomas Perú Fuego Colección 1 proporciona datos de monitoreo de incendios "
            "para todo el territorio peruano desde 2013 hasta 2024, utilizando imágenes satelitales "
            "Landsat, inteligencia artificial y Google Earth Engine.<br><br>"
            "<b>Productos disponibles:</b><br>"
            "• <b>Área quemada anual:</b> Identifica las áreas quemadas por año (valor 1 = área quemada).<br>"
            "• <b>Área quemada mensual:</b> Identifica el mes de ocurrencia del incendio (valores 1-12 = Enero a Diciembre).<br>"
            "• <b>Frecuencia de incendios:</b> Número de veces que se registró fuego en el periodo seleccionado (0-10 veces).<br>"
            "• <b>Último año de incendio:</b> Indica el año del último incendio registrado en cada píxel.<br><br>"
            "La herramienta descarga la imagen desde GEE, la reproyecta al CRS seleccionado, vectoriza y genera "
            "estadísticas con gráficos y resumen en CSV.<br><br>"
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
        self.addParameter(QgsProcessingParameterExtent(
            self.INPUT_EXTENT, self.tr('O dibujar un Recuadro'), optional=True
        ))

        self.addParameter(QgsProcessingParameterEnum(
            self.PRODUCT, self.tr('Seleccionar Producto'),
            options=self.products, defaultValue=0
        ))
        self.addParameter(QgsProcessingParameterString(
            self.YEAR,
            self.tr('Año a analizar (Para Área quemada anual, mensual y Último año de incendio)'),
            defaultValue='2024'
        ))
        self.addParameter(QgsProcessingParameterEnum(
            self.FREQUENCY_BAND,
            self.tr('Banda de Frecuencia (Solo para Frecuencia de incendios)'),
            options=self.frequency_bands, defaultValue=11
        ))

        self.addParameter(QgsProcessingParameterCrs(
            self.EPSG, self.tr('CRS de Destino'), defaultValue='EPSG:32718'
        ))
        self.addParameter(QgsProcessingParameterFolderDestination(
            self.OUT_FOLDER, self.tr('Carpeta de Destino'), optional=False
        ))

    def processAlgorithm(self, parameters, context, feedback):
        product_idx = self.parameterAsEnum(parameters, self.PRODUCT, context)
        year_str = self.parameterAsString(parameters, self.YEAR, context).strip()
        freq_idx = self.parameterAsEnum(parameters, self.FREQUENCY_BAND, context)
        crs_dest = self.parameterAsCrs(parameters, self.EPSG, context)
        out_folder = self.parameterAsString(parameters, self.OUT_FOLDER, context)

        if not out_folder:
            raise QgsProcessingException("Debe seleccionar una Carpeta de Destino.")

        target_crs_str = crs_dest.authid() if crs_dest.isValid() else 'EPSG:4326'

        # ── 1. AOI centralizado (utils/aoi_builder) ───────────────────
        aoi = build_aoi(self, parameters, context, feedback)
        geom_union = aoi.geom_4326

        geom_union_simp = safe_simplify(geom_union, 0.0001)
        geo_dict = json.loads(geom_union_simp.asJson())

        # ── 2. Inicializar GEE (wrapper centralizado) ─────────────────
        ensure_gee_initialized(feedback)

        # Geometría EE sin truncar GeometryCollection — fix B-01
        geom_ee = to_ee_geometry(geom_union_simp, ee, geo_dict)

        # ── 3. Seleccionar asset y banda según producto ───────────────
        asset_path = self.assets[product_idx]

        if product_idx == 0:  # Área quemada anual
            band_name = f'burned_area_{year_str}'
            out_filename = f"MapBiomas_Quema_Anual_{year_str}"
        elif product_idx == 1:  # Área quemada mensual
            band_name = f'burned_monthly_{year_str}'
            out_filename = f"MapBiomas_Quema_Mensual_{year_str}"
        elif product_idx == 2:  # Frecuencia de incendios
            band_name = self.frequency_bands[freq_idx]
            out_filename = f"MapBiomas_Frecuencia_{band_name}"
        else:  # Último año de incendio
            band_name = f'classification_{year_str}'
            out_filename = f"MapBiomas_UltimoIncendio_{year_str}"

        feedback.pushInfo(f"Producto: {self.products[product_idx]}")
        feedback.pushInfo(f"Asset: {asset_path}")
        feedback.pushInfo(f"Banda: {band_name}")

        # ── 4. Descargar imagen desde GEE ─────────────────────────────
        feedback.pushInfo("Descargando imagen desde Google Earth Engine...")
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
                    if chunk:
                        f.write(chunk)

            if not os.path.exists(tif_file):
                raise Exception("Error al guardar TIFF.")
        except Exception as e:
            raise QgsProcessingException(
                f"Error descargando de GEE: {str(e)}\nIntenta con un área más pequeña."
            )

        feedback.setProgress(25)

        # ── 5. Reproyectar raster ─────────────────────────────────────
        feedback.pushInfo(f"Reproyectando raster a {target_crs_str}...")
        tif_proj = os.path.join(out_folder, f"{out_filename}_proj.tif")
        processing.run("gdal:warpreproject", {
            'INPUT': tif_file,
            'SOURCE_CRS': 'EPSG:4326',
            'TARGET_CRS': target_crs_str,
            'NODATA': 0,
            'OUTPUT': tif_proj
        }, context=context, feedback=feedback)

        feedback.setProgress(40)

        # ── 6. Poligonizar raster ─────────────────────────────────────
        feedback.pushInfo("Convirtiendo raster a polígonos...")
        vec_file = os.path.join(out_folder, f"{out_filename}_temp.gpkg")
        processing.run("gdal:polygonize", {
            'INPUT': tif_proj,
            'BAND': 1,
            'FIELD': 'valor',
            'EIGHT_CONNECTEDNESS': False,
            'OUTPUT': vec_file
        }, context=context, feedback=feedback)

        feedback.setProgress(55)

        # ── 7. Filtrar valores sin datos (valor = 0) ──────────────────
        feedback.pushInfo("Filtrando píxeles sin datos...")
        filtered = processing.run("native:extractbyexpression", {
            'INPUT': vec_file,
            'EXPRESSION': '"valor" > 0',
            'OUTPUT': 'memory:'
        }, context=context, feedback=feedback)['OUTPUT']

        feat_count = filtered.featureCount()
        if feat_count == 0:
            raise QgsProcessingException(
                "No se encontraron datos de incendios en el área y producto seleccionados."
            )
        feedback.pushInfo(f"Polígonos con datos válidos: {feat_count}")

        # ── 8. Calcular área en hectáreas ─────────────────────────────
        feedback.pushInfo("Calculando áreas y agregando campos descriptivos...")
        res_area = processing.run("native:fieldcalculator", {
            'INPUT': filtered,
            'FIELD_NAME': 'area_ha',
            'FIELD_TYPE': 0,  # Float
            'FIELD_LENGTH': 10,
            'FIELD_PRECISION': 4,
            'FORMULA': '$area / 10000',
            'OUTPUT': 'memory:'
        }, context=context, feedback=feedback)['OUTPUT']

        # ── 9. Agregar campo Descripcion ──────────────────────────────
        final_layer = processing.run("native:fieldcalculator", {
            'INPUT': res_area,
            'FIELD_NAME': 'Descripcion',
            'FIELD_TYPE': 2,  # String
            'FIELD_LENGTH': 100,
            'FIELD_PRECISION': 0,
            'FORMULA': "''",
            'OUTPUT': 'memory:'
        }, context=context, feedback=feedback)['OUTPUT']

        # ── 10. Decodificar valores según producto ────────────────────
        feedback.pushInfo("Decodificando valores del producto...")
        final_layer.startEditing()
        idx_val = final_layer.fields().lookupField('valor')
        idx_desc = final_layer.fields().lookupField('Descripcion')

        for feat in final_layer.getFeatures():
            val = int(feat.attributes()[idx_val])

            if product_idx == 0:  # Área quemada anual
                desc = 'Área quemada' if val == 1 else f'Valor {val}'
            elif product_idx == 1:  # Área quemada mensual
                desc = self.meses.get(val, f'Valor {val}')
            elif product_idx == 2:  # Frecuencia de incendios
                desc = self.frecuencia_labels.get(val, f'{val} veces')
            else:  # Último año de incendio
                desc = f'Último incendio: {val}'

            final_layer.changeAttributeValue(feat.id(), idx_desc, desc)

        final_layer.commitChanges()

        feedback.setProgress(70)

        # ── 11. Guardar resultado final en GeoPackage ──────────────────
        final_gpkg = os.path.join(out_folder, f"{out_filename}_Final.gpkg")
        if os.path.exists(final_gpkg):
            try:
                os.remove(final_gpkg)
            except Exception:
                pass
        processing.run("native:savefeatures", {
            'INPUT': final_layer, 'OUTPUT': final_gpkg
        }, context=context, feedback=feedback)
        feedback.pushInfo(f"Vector guardado en: {final_gpkg}")

        # ── 12. Aplicar simbología categorizada ───────────────────────
        feedback.pushInfo("Aplicando simbología...")
        vlayer_final = QgsVectorLayer(final_gpkg, out_filename, "ogr")

        if vlayer_final.isValid():
            idx_desc_f = vlayer_final.fields().lookupField('Descripcion')
            unique_vals = sorted(list(vlayer_final.uniqueValues(idx_desc_f)), key=str)
            categories = []

            if product_idx == 0:  # Anual — rojo para quemada
                color_map = {'Área quemada': '#e31a1c'}
                for val in unique_vals:
                    symbol = QgsSymbol.defaultSymbol(vlayer_final.geometryType())
                    color = QColor(color_map.get(str(val), '#cccccc'))
                    symbol.setColor(color)
                    symbol.setOpacity(0.85)
                    if symbol.symbolLayerCount() > 0:
                        symbol.symbolLayer(0).setStrokeColor(QColor('#333333'))
                    categories.append(QgsRendererCategory(val, symbol, str(val)))

            elif product_idx == 1:  # Mensual — paleta gradual
                month_colors = {
                    'Enero': '#ffffcc', 'Febrero': '#ffeda0', 'Marzo': '#fed976',
                    'Abril': '#feb24c', 'Mayo': '#fd8d3c', 'Junio': '#fc4e2a',
                    'Julio': '#e31a1c', 'Agosto': '#bd0026', 'Septiembre': '#800026',
                    'Octubre': '#4d0018', 'Noviembre': '#330011', 'Diciembre': '#000000'
                }
                for val in unique_vals:
                    symbol = QgsSymbol.defaultSymbol(vlayer_final.geometryType())
                    color = QColor(month_colors.get(str(val), '#cccccc'))
                    symbol.setColor(color)
                    symbol.setOpacity(0.85)
                    if symbol.symbolLayerCount() > 0:
                        symbol.symbolLayer(0).setStrokeColor(color)
                    categories.append(QgsRendererCategory(val, symbol, str(val)))

            elif product_idx == 2:  # Frecuencia — paleta calor
                freq_colors = [
                    '#ffffb2', '#fecc5c', '#fd8d3c', '#f03b20', '#bd0026',
                    '#a50026', '#800026', '#660019', '#4d0013', '#33000d'
                ]
                for val in unique_vals:
                    symbol = QgsSymbol.defaultSymbol(vlayer_final.geometryType())
                    # Extraer el índice numérico de la descripción
                    color_idx = 0
                    for k, v in self.frecuencia_labels.items():
                        if v == str(val):
                            color_idx = max(k - 1, 0)
                            break
                    color = QColor(freq_colors[min(color_idx, len(freq_colors) - 1)])
                    symbol.setColor(color)
                    symbol.setOpacity(0.85)
                    if symbol.symbolLayerCount() > 0:
                        symbol.symbolLayer(0).setStrokeColor(color)
                    categories.append(QgsRendererCategory(val, symbol, str(val)))

            else:  # Último año — paleta viridis
                viridis = [
                    '#440154', '#482878', '#3e4989', '#31688e', '#26828e',
                    '#1f9e89', '#35b779', '#6ece58', '#b5de2b', '#fde725'
                ]
                for i, val in enumerate(unique_vals):
                    symbol = QgsSymbol.defaultSymbol(vlayer_final.geometryType())
                    color = QColor(viridis[i % len(viridis)])
                    symbol.setColor(color)
                    symbol.setOpacity(0.85)
                    if symbol.symbolLayerCount() > 0:
                        symbol.symbolLayer(0).setStrokeColor(color)
                    categories.append(QgsRendererCategory(val, symbol, str(val)))

            renderer = QgsCategorizedSymbolRenderer('Descripcion', categories)
            vlayer_final.setRenderer(renderer)
            vlayer_final.triggerRepaint()
            QgsProject.instance().addMapLayer(vlayer_final)

        feedback.setProgress(85)

        # ── 13. Generar estadísticas y gráficos ───────────────────────
        feedback.pushInfo("Generando estadísticas y gráficos...")

        stats = {}
        for feat in final_layer.getFeatures():
            desc = feat['Descripcion']
            area = feat['area_ha']
            stats[desc] = stats.get(desc, 0) + area

        # Nombre de la columna según producto
        if product_idx == 0:
            col_name = "Estado"
        elif product_idx == 1:
            col_name = "Mes"
        elif product_idx == 2:
            col_name = "Frecuencia"
        else:
            col_name = "Último Año de Incendio"

        df = pd.DataFrame(list(stats.items()), columns=[col_name, "Área (ha)"])
        df['Área (ha)'] = df['Área (ha)'].round(4)
        df = df.sort_values(by="Área (ha)", ascending=False)

        csv_path = os.path.join(out_folder, f"{out_filename}_Estadisticas.csv")
        df.to_csv(csv_path, index=False, encoding='utf-8-sig')
        feedback.pushInfo(f"Estadísticas exportadas: {csv_path}")

        # ── Gráfico de barras ──
        fig, ax = plt.subplots(figsize=(12, 6))

        if product_idx == 0:  # Anual
            df_chart = df.copy()
            colors_chart = ['#e31a1c' if x == 'Área quemada' else '#aaaaaa'
                            for x in df_chart[col_name]]
            bars = ax.bar(df_chart[col_name], df_chart['Área (ha)'], color=colors_chart,
                          edgecolor='#333333')
            ax.set_title(f'Área Quemada Anual {year_str} — MapBiomas Perú', fontsize=14, fontweight='bold')

        elif product_idx == 1:  # Mensual
            month_order = list(self.meses.values())
            df_chart = df[df[col_name].isin(month_order)].copy()
            df_chart[col_name] = pd.Categorical(
                df_chart[col_name], categories=month_order, ordered=True
            )
            df_chart = df_chart.sort_values(col_name)
            month_colors_list = [
                '#ffffcc', '#ffeda0', '#fed976', '#feb24c', '#fd8d3c', '#fc4e2a',
                '#e31a1c', '#bd0026', '#800026', '#4d0018', '#330011', '#000000'
            ]
            colors_chart = []
            for m in df_chart[col_name]:
                idx = month_order.index(m) if m in month_order else 0
                colors_chart.append(month_colors_list[idx])
            bars = ax.bar(df_chart[col_name], df_chart['Área (ha)'], color=colors_chart,
                          edgecolor='#333333')
            ax.set_title(f'Área Quemada por Mes {year_str} — MapBiomas Perú', fontsize=14, fontweight='bold')
            plt.xticks(rotation=45, ha='right')

        elif product_idx == 2:  # Frecuencia
            freq_order = [self.frecuencia_labels[k] for k in sorted(self.frecuencia_labels.keys()) if k > 0]
            df_chart = df[df[col_name].isin(freq_order)].copy()
            df_chart[col_name] = pd.Categorical(
                df_chart[col_name], categories=freq_order, ordered=True
            )
            df_chart = df_chart.sort_values(col_name)
            freq_colors_chart = [
                '#ffffb2', '#fecc5c', '#fd8d3c', '#f03b20', '#bd0026',
                '#a50026', '#800026', '#660019', '#4d0013', '#33000d'
            ]
            colors_chart = [freq_colors_chart[i % len(freq_colors_chart)]
                            for i in range(len(df_chart))]
            bars = ax.bar(df_chart[col_name], df_chart['Área (ha)'], color=colors_chart,
                          edgecolor='#333333')
            ax.set_title(f'Frecuencia de Incendios — MapBiomas Perú\n({band_name})',
                         fontsize=14, fontweight='bold')
            plt.xticks(rotation=45, ha='right')

        else:  # Último año
            df_chart = df.copy()
            df_chart = df_chart.sort_values(col_name)
            viridis_c = [
                '#440154', '#482878', '#3e4989', '#31688e', '#26828e',
                '#1f9e89', '#35b779', '#6ece58', '#b5de2b', '#fde725'
            ]
            colors_chart = [viridis_c[i % len(viridis_c)] for i in range(len(df_chart))]
            bars = ax.bar(df_chart[col_name], df_chart['Área (ha)'], color=colors_chart,
                          edgecolor='#333333')
            ax.set_title(f'Último Año de Incendio — MapBiomas Perú ({year_str})',
                         fontsize=14, fontweight='bold')
            plt.xticks(rotation=45, ha='right')

        ax.set_xlabel(col_name, fontsize=11)
        ax.set_ylabel('Área (ha)', fontsize=11)

        for bar in bars:
            yval = bar.get_height()
            if yval > 0:
                ax.text(bar.get_x() + bar.get_width() / 2.0, yval,
                        f'{yval:,.2f}', ha='center', va='bottom', fontsize=8)

        plt.tight_layout()
        png_path = os.path.join(out_folder, f"{out_filename}_Grafico.png")
        plt.savefig(png_path, dpi=300)
        plt.close(fig)
        feedback.pushInfo(f"Gráfico guardado: {png_path}")

        # Limpiar temporales
        try:
            if os.path.exists(vec_file):
                os.remove(vec_file)
        except Exception:
            pass

        feedback.setProgress(100)
        feedback.pushInfo("Proceso completado exitosamente.")

        return {self.OUT_FOLDER: out_folder}
