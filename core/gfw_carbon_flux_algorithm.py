import os
import csv
import ee
import requests
import processing
from qgis.core import (
    QgsProcessing, QgsProcessingAlgorithm, QgsProcessingParameterExtent,
    QgsProcessingParameterEnum, QgsProcessingParameterCrs,
    QgsProcessingParameterFolderDestination, QgsProcessingParameterString,
    QgsProcessingException, QgsProject, QgsProcessingParameterMapLayer,
    QgsRasterLayer
)
from qgis.PyQt.QtCore import QCoreApplication
import numpy as np

from ..utils.aoi_builder import build_aoi, to_ee_geometry
from ..utils.gee_init import ensure_gee_initialized

class GfwCarbonFluxAlgorithm(QgsProcessingAlgorithm):
    INPUT_LAYER = 'INPUT_LAYER'
    INPUT_EXTENT = 'INPUT_EXTENT'
    PRODUCT = 'PRODUCT'
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
        self.productos = [
            "Todos (Emisiones, Remociones y Flujo Neto)",
            "Emisiones brutas de carbono",
            "Remociones de carbono",
            "Flujo neto de carbono"
        ]

    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    def createInstance(self):
        return GfwCarbonFluxAlgorithm()

    def name(self):
        return 'gfw_carbon_flux_analysis'

    def displayName(self):
        return self.tr('GFW - Carbono Modelo de Flujo 30m')

    def group(self):
        return self.tr('Biomasa Forestal Global')

    def groupId(self):
        return 'biomasa_forestal_global'

    def shortHelpString(self):
        help_text = (
            "<b>Global Forest Watch Carbon Flux Model v1.4.2 (30m)</b><br><br>"
            "Modelo basado en la metodología de Nancy Harris et al. 2021 y Gibbs et al. 2025, actualizados con la pérdida "
            "de cobertura arbórea de 2024.<br><br>"
            "<b>Productos Disponibles:</b><br>"
            "<ul>"
            "<li><b>Emisiones brutas de carbono:</b> Emisiones de gases de efecto invernadero asociadas con la deforestación y degradación.</li>"
            "<li><b>Remociones de carbono:</b> Absorción de carbono de la atmósfera por los bosques en crecimiento.</li>"
            "<li><b>Flujo neto de carbono:</b> Balance entre emisiones y remociones (Valores negativos indican sumideros, positivos indican fuentes).</li>"
            "</ul><br>"
            "<b>Información Técnica:</b><br>"
            "Los resultados se calculan con una resolución de 30m y las unidades son generalmente en toneladas de CO2 equivalente por hectárea (Mg CO2e/ha).<br><br>"
            "<b>Fuente de Datos:</b><br>"
            "Proyectos en GEE: <i>projects/sat-io/open-datasets/forest_carbon_fluxes/...</i>"
        )
        return self.tr(help_text)

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterMapLayer(self.INPUT_LAYER, self.tr('Opción 1: Seleccionar Capa Vectorial (Área de Interés)'), [QgsProcessing.TypeVectorPolygon], optional=True))
        self.addParameter(QgsProcessingParameterExtent(self.INPUT_EXTENT, self.tr('Opción 2: O dibujar un Recuadro Extensión'), optional=True))
        self.addParameter(QgsProcessingParameterEnum(self.PRODUCT, self.tr('Producto a Determinar'), options=self.productos, defaultValue=0))
        self.addParameter(QgsProcessingParameterCrs(self.EPSG, self.tr('CRS de Destino (Ej. UTM)'), defaultValue='EPSG:32718', optional=True))
        self.addParameter(QgsProcessingParameterEnum(self.EXPORT_METHOD, self.tr('Método de Exportación'), options=self.export_methods, defaultValue=0))
        self.addParameter(QgsProcessingParameterFolderDestination(self.OUT_FOLDER, self.tr('Carpeta Local de Destino'), optional=False))
        self.addParameter(QgsProcessingParameterString(self.GCS_BUCKET, self.tr('Nombre del Bucket GCS (Solo para GCS)'), optional=True))

    def processAlgorithm(self, parameters, context, feedback):
        prod_idx = self.parameterAsEnum(parameters, self.PRODUCT, context)
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

        to_process = []
        if prod_idx == 0 or prod_idx == 1:
            to_process.append(('Emisiones', 'projects/sat-io/open-datasets/forest_carbon_fluxes/gross_emissions', ['#ffffcc', '#ffeda0', '#fed976', '#feb24c', '#fd8d3c', '#fc4e2a', '#e31a1c', '#b10026']))
        if prod_idx == 0 or prod_idx == 2:
            to_process.append(('Remociones', 'projects/sat-io/open-datasets/forest_carbon_fluxes/gross_removals', ['#f7fcf5', '#e5f5e0', '#c7e9c0', '#a1d99b', '#74c476', '#41ab5d', '#238b45', '#005a32']))
        if prod_idx == 0 or prod_idx == 3:
            to_process.append(('Flujo_Neto', 'projects/sat-io/open-datasets/forest_carbon_fluxes/net_flux', ['#005a32', '#41ab5d', '#a1d99b', '#ffffff', '#fd8d3c', '#e31a1c', '#800026']))

        stats_results = []
        downloaded_tifs = []
        
        for i, (name, asset_id, palette) in enumerate(to_process):
            feedback.setProgress(10 + (i * 20))
            feedback.pushInfo(f"Procesando GFW - {name}...")

            image = ee.Image(asset_id).clip(geom_ee_exact)
            band_name = image.bandNames().get(0)
            
            # Extraer banda
            band = image.select([band_name])
            
            # Algunos datasets en GEE tienen -inf como valor literal de pixel.
            # Forzamos una máscara a cualquier valor anómalo negativo (<-100000) 
            # para que 'unmask' pueda rellenarlos todos uniformemente.
            band_masked = band.updateMask(band.gt(-100000))
            
            img_export = band_masked.unmask(-9999).toFloat()

            out_prefix = f"GFW_Carbon_{name}"
            
            if export_method_idx != 0:
                # Exportación a Drive/GCS centralizada (utils/export_router)
                from ..utils.export_router import export_image
                export_image(
                    method=export_method_idx,
                    image=img_export,
                    description=f"{out_prefix}_30m",
                    region=geom_ee_exact,
                    scale=30,
                    drive_folder='GEE_Bosques_GFW',
                    gcs_bucket=gcs_bucket,
                    file_format='GeoTIFF',
                    feedback=feedback,
                )
                continue

            # Descarga Directa
            try:
                url_img = img_export.getDownloadURL({
                    'scale': 30, 
                    'crs': 'EPSG:4326',
                    'region': geom_ee_exact, 
                    'format': 'GEO_TIFF'
                })
                r_img = requests.get(url_img, stream=True)
                r_img.raise_for_status()
                
                tif_download = os.path.join(out_folder, f"{out_prefix}_raw.tif")
                    
                with open(tif_download, 'wb') as f:
                    for chunk in r_img.iter_content(chunk_size=8192):
                        if chunk: f.write(chunk)
                
                tif_final = os.path.join(out_folder, f"{out_prefix}.tif")
                feedback.pushInfo(f"Asignando NoData (-9999) y proyectando a {target_crs_str}...")
                
                params_reprj = {
                    'INPUT': tif_download,
                    'SOURCE_CRS': 'EPSG:4326',
                    'TARGET_CRS': target_crs_str,
                    'NODATA': -9999,
                    'EXTRA': '-srcnodata -9999',
                    'OUTPUT': tif_final
                }
                processing.run("gdal:warpreproject", params_reprj, context=context, feedback=feedback)
                
                try:
                    os.remove(tif_download)
                except Exception:
                    pass
                    
                rlayer = QgsRasterLayer(tif_final, f"GFW {name.replace('_', ' ')}")
                if not rlayer.isValid():
                    raise QgsProcessingException(f"El raster descargado de {name} no es válido.")
                    
                downloaded_tifs.append((name, tif_final, rlayer, palette))
                
                # Estadísticas
                stats = processing.run("native:rasterlayerstatistics", {'INPUT': tif_final, 'BAND': 1})
                stats_results.append({
                    'Producto': name,
                    'MIN': stats['MIN'],
                    'MAX': stats['MAX'],
                    'MEAN': stats['MEAN'],
                    'STD_DEV': stats['STD_DEV']
                })
                
            except Exception as e:
                feedback.reportError(f"Error descargando {name}: {str(e)}", fatalError=False)

        if export_method_idx != 0:
            return {self.OUT_FOLDER: out_folder}
            
        feedback.setProgress(80)
        
        # Guardar Estadísticas en CSV
        if stats_results:
            csv_path = os.path.join(out_folder, "GFW_Carbon_Flux_Estadisticas.csv")
            with open(csv_path, 'w', newline='', encoding='utf-8') as csvfile:
                writer = csv.writer(csvfile)
                writer.writerow(['Producto', 'Minimo', 'Maximo', 'Promedio', 'Desviacion_Estandar'])
                for s in stats_results:
                    writer.writerow([s['Producto'], s['MIN'], s['MAX'], s['MEAN'], s['STD_DEV']])
        
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
                    data = np.ma.masked_invalid(data)
                    return data.compressed()
                return data.flatten()
            
            num_plots = len(downloaded_tifs)
            if num_plots > 0:
                fig, axes = plt.subplots(1, num_plots, figsize=(6 * num_plots, 5))
                if num_plots == 1:
                    axes = [axes]
                    
                for i, (name, tif_path, rlayer, palette) in enumerate(downloaded_tifs):
                    data = get_raster_data(tif_path)
                    if len(data) > 0:
                        y_hist, x_hist = np.histogram(data, bins=30)
                        x_hist = (x_hist[:-1] + x_hist[1:]) / 2
                        
                        # Usar el color principal según el producto
                        bar_color = palette[-2] if len(palette) > 1 else '#888888'
                        if name == 'Flujo_Neto': bar_color = '#1f78b4'
                        
                        axes[i].bar(x_hist, y_hist, width=(x_hist[1]-x_hist[0])*0.8 if len(x_hist)>1 else 1, color=bar_color, edgecolor='black')
                        axes[i].set_title(f"{name.replace('_', ' ')} (Mg CO2e/ha)")
                        axes[i].set_xlabel('Valor')
                        if i == 0: axes[i].set_ylabel('Frecuencia')
                        
                plt.tight_layout()
                png_path = os.path.join(out_folder, "GFW_Carbon_Histogramas.png")
                plt.savefig(png_path, dpi=300, bbox_inches='tight', facecolor='white')
                plt.close(fig)
        except Exception as e:
            feedback.pushInfo(f"Aviso gráfico: No se pudo generar el gráfico ({str(e)}).")

        feedback.setProgress(95)
        
        # Aplicar Simbología Dinámica y Cargar a QGIS
        from qgis.core import QgsColorRampShader, QgsRasterShader, QgsSingleBandPseudoColorRenderer
        from qgis.PyQt.QtGui import QColor

        def apply_symbology(layer, min_val, max_val, hex_colors):
            if min_val is None or max_val is None or min_val == max_val:
                return # Skip si no hay estadísticas válidas
            
            fnc = QgsColorRampShader()
            fnc.setColorRampType(QgsColorRampShader.Interpolated)
            lst = []
            
            step = (max_val - min_val) / (len(hex_colors) - 1) if len(hex_colors) > 1 else 1
            for j, color in enumerate(hex_colors):
                val = min_val + (j * step)
                lst.append(QgsColorRampShader.ColorRampItem(val, QColor(color), str(round(val, 2))))
            
            fnc.setColorRampItemList(lst)
            shader = QgsRasterShader()
            shader.setRasterShaderFunction(fnc)
            renderer = QgsSingleBandPseudoColorRenderer(layer.dataProvider(), 1, shader)
            renderer.setClassificationMin(min_val)
            renderer.setClassificationMax(max_val)
            layer.setRenderer(renderer)
            layer.triggerRepaint()
        
        for name, tif_path, rlayer, palette in downloaded_tifs:
            # Obtener los límites para la simbología desde las stats
            s = next((item for item in stats_results if item["Producto"] == name), None)
            if s and s['MIN'] is not None and s['MAX'] is not None:
                # Filtrar valores atípicos usando media y desviación (para evitar que extremos arruinen el color)
                # O usar un límite razonable. Asumiremos los valores reales o aproximaciones genéricas
                # Para flujo neto se sugiere centrar en 0
                if name == 'Flujo_Neto':
                    limit = max(abs(s['MIN']), abs(s['MAX']))
                    limit = min(limit, 500) # Capar outliers visuales si existen
                    apply_symbology(rlayer, -limit, limit, palette)
                else:
                    # Emisiones y Remociones
                    apply_symbology(rlayer, max(0, s['MIN']), s['MAX'], palette)
            
            QgsProject.instance().addMapLayer(rlayer)
            
        feedback.setProgress(100)
        return {self.OUT_FOLDER: out_folder}
