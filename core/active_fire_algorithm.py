import os
import requests
import pandas as pd
import geopandas as gpd
import matplotlib
matplotlib.use("Agg")  # backend headless, thread-safe en worker de Processing
import matplotlib.pyplot as plt

from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterExtent,
    QgsProcessingParameterEnum,
    QgsProcessingParameterCrs,
    QgsProcessingParameterFolderDestination,
    QgsProcessingParameterMapLayer,
    QgsProcessingException,
    QgsVectorLayer,
    QgsProject,
    QgsGeometry,
    QgsFeature
)
import processing
from qgis.PyQt.QtCore import QCoreApplication

class ActiveFireAlgorithm(QgsProcessingAlgorithm):
    INPUT_LAYER = 'INPUT_LAYER'
    INPUT_EXTENT = 'INPUT_EXTENT'
    REGION = 'REGION'
    SENSOR = 'SENSOR'
    PERIODO = 'PERIODO'
    EPSG = 'EPSG'
    OUT_FOLDER = 'OUT_FOLDER'

    def __init__(self):
        super().__init__()
        self.regiones = [
            "Mundo", "Canadá", "Alaska", "USA y Hawaii", "Centroamérica", 
            "Sudamérica", "Europa", "Norte y Centro de África", "África Austral", 
            "Rusia y Asia", "Asia del Sur", "Sudeste Asiático", "Australia y Nueva Zelanda"
        ]
        self.regiones_keys = [
            "Global", "Canada", "Alaska", "USA_contiguous_and_Hawaii", "Central_America",
            "South_America", "Europe", "Northern_and_Central_Africa", "Southern_Africa",
            "Russia_Asia", "South_Asia", "SouthEast_Asia", "Australia_NewZealand"
        ]
        
        self.sensores = [
            "MODIS C6.1 1km", 
            "VIIRS S-NPP 375m", 
            "VIIRS NOAA-20 375m", 
            "VIIRS NOAA-21 375m"
        ]
        self.sensores_keys = [
            "MODIS_C6_1", 
            "SUOMI_VIIRS_C2", 
            "J1_VIIRS_C2", 
            "J2_VIIRS_C2"
        ]
        self.sensores_folders = [
            "modis-c6.1", 
            "suomi-npp-viirs-c2", 
            "noaa-20-viirs-c2", 
            "noaa-21-viirs-c2"
        ]

        self.periodos = ["24 Horas", "48 Horas", "7 Días"]
        self.periodos_keys = ["24h", "48h", "7d"]

    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    def createInstance(self):
        return ActiveFireAlgorithm()

    def name(self):
        return 'active_fire_analysis'

    def displayName(self):
        return self.tr('Puntos activos Incendio (FIRMS)')

    def group(self):
        return self.tr('Monitoreo de incendio')

    def groupId(self):
        return 'monitoreo_incendio'

    def shortHelpString(self):
        help_text = (
            "<b>Descripción del Proceso:</b><br>"
            "El Sistema de Gestión de Recursos e Información sobre Incendios (FIRMS) distribuye datos de incendios "
            "activos casi en tiempo real (NRT) del espectrorradiómetro de imágenes de resolución moderada (MODIS) a "
            "bordo de los satélites Aqua y Terra, y del conjunto de radiómetros de imágenes infrarrojas visibles (VIIRS) "
            "a bordo del S-NPP, NOAA 20 y NOAA 21 (formalmente conocidos como JPSS-1 y JPSS-2).<br><br>"
            "satelital, pero para EE. UU. y Canadá las detecciones activas de incendios están disponibles en "
            "tiempo real.<br><br>"
            "Esta herramienta descarga puntos de incendios activos desde FIRMS (NASA) "
            "según la región, sensor y periodo seleccionado.<br>"
            "Genera dos capas vectoriales: una global/regional y otra recortada al área de interés.<br>"
            "Además, determina la fecha y la temperatura en °C, y crea un gráfico de barras y un CSV de resumen.<br><br>"
            "<b>Fuente:</b> <a href='https://firms.modaps.eosdis.nasa.gov/active_fire/'>https://firms.modaps.eosdis.nasa.gov/active_fire/</a>"
        )
        return self.tr(help_text)

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterMapLayer(
                self.INPUT_LAYER,
                self.tr('Área de Interés: Capa Vectorial'),
                [QgsProcessing.TypeVectorPolygon],
                optional=True
            )
        )
        self.addParameter(QgsProcessingParameterExtent(self.INPUT_EXTENT, self.tr('Área de Interés: Dibujar Recuadro'), optional=True))
        
        self.addParameter(QgsProcessingParameterEnum(self.REGION, self.tr('Seleccionar Región'), options=self.regiones, defaultValue=5)) # Default Sudamérica
        self.addParameter(QgsProcessingParameterEnum(self.SENSOR, self.tr('Seleccionar Sensor'), options=self.sensores, defaultValue=1))
        self.addParameter(QgsProcessingParameterEnum(self.PERIODO, self.tr('Filtro de periodo'), options=self.periodos, defaultValue=2)) # Default 7 Días
        self.addParameter(QgsProcessingParameterCrs(self.EPSG, self.tr('CRS de Destino (Para el recorte)'), defaultValue='EPSG:32718', optional=True))
        self.addParameter(QgsProcessingParameterFolderDestination(self.OUT_FOLDER, self.tr('Carpeta de Destino')))

    def processAlgorithm(self, parameters, context, feedback):
        input_layer = self.parameterAsLayer(parameters, self.INPUT_LAYER, context)
        extent = self.parameterAsExtent(parameters, self.INPUT_EXTENT, context)
        
        region_idx = self.parameterAsEnum(parameters, self.REGION, context)
        sensor_idx = self.parameterAsEnum(parameters, self.SENSOR, context)
        periodo_idx = self.parameterAsEnum(parameters, self.PERIODO, context)
        crs_dest = self.parameterAsCrs(parameters, self.EPSG, context)
        target_crs_str = crs_dest.authid() if crs_dest.isValid() else 'EPSG:4326'
        out_folder = self.parameterAsString(parameters, self.OUT_FOLDER, context)

        if input_layer is None and (extent.isNull() or extent.isEmpty()):
            raise QgsProcessingException("Debes usar una Capa Vectorial o Dibujar un Recuadro para el recorte.")

        region_key = self.regiones_keys[region_idx]
        sensor_key = self.sensores_keys[sensor_idx]
        sensor_folder = self.sensores_folders[sensor_idx]
        periodo_key = self.periodos_keys[periodo_idx]
        ext = ".gpkg"

        # 1. Construir URL y descargar CSV
        url = f"https://firms.modaps.eosdis.nasa.gov/data/active_fire/{sensor_folder}/csv/{sensor_key}_{region_key}_{periodo_key}.csv"
        feedback.pushInfo(f"Descargando datos de FIRMS: {url}")
        
        try:
            response = requests.get(url)
            response.raise_for_status()
        except Exception as e:
            raise QgsProcessingException(f"Error al descargar datos de FIRMS: {str(e)}")

        csv_path = os.path.join(out_folder, "temp_firms_data.csv")
        with open(csv_path, 'wb') as f:
            f.write(response.content)

        # 2. Leer con Pandas y crear GeoDataFrame
        feedback.pushInfo("Convirtiendo datos a vectorial...")
        df = pd.read_csv(csv_path)
        if df.empty:
            raise QgsProcessingException("No se encontró incendios activos en estas fechas para la región solicitada.")

        df['Fecha'] = pd.to_datetime(df['acq_date'], errors='coerce').dt.date
        df = df.dropna(subset=['Fecha'])
        df['Fecha_str'] = df['Fecha'].astype(str)
        
        if 'brightness' in df.columns:
            df['Temp_C'] = df['brightness'] - 273.15
        elif 'bright_ti4' in df.columns:
            df['Temp_C'] = df['bright_ti4'] - 273.15
            df.rename(columns={'bright_ti4': 'brightness'}, inplace=True)
        else:
            df['Temp_C'] = 0.0

        gdf = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df['longitude'], df['latitude']), crs="EPSG:4326")

        cols_region = ['latitude', 'longitude', 'brightness', 'confidence', 'Temp_C', 'Fecha_str', 'geometry']
        cols_region = [c for c in cols_region if c in gdf.columns]
        gdf_region_save = gdf[cols_region]

        # 3. Guardar vectorial de la región completa
        base_name_region = f"Incendios_{self.regiones[region_idx].replace(' ', '_')}_{self.periodos[periodo_idx].replace(' ', '_')}"
        region_out_path = os.path.join(out_folder, f"{base_name_region}{ext}")
        
        # Eliminar si existe para evitar problemas de append en gpkg
        if os.path.exists(region_out_path):
            try:
                os.remove(region_out_path)
            except:
                pass
                
        gdf_region_save.to_file(region_out_path, driver="GPKG")
        feedback.pushInfo(f"Capa regional guardada en: {region_out_path}")

        # 4. Obtener límite para recorte (QGIS)
        feedback.pushInfo("Realizando recorte con área de interés...")
        if input_layer is not None:
            params = {'INPUT': input_layer, 'TARGET_CRS': 'EPSG:4326', 'OUTPUT': 'memory:'}
            limite_layer = processing.run("native:reprojectlayer", params, context=context, feedback=feedback)['OUTPUT']
        else:
            crs_source = self.parameterAsExtentCrs(parameters, self.INPUT_EXTENT, context)
            geom = QgsGeometry.fromRect(extent)
            vl = QgsVectorLayer("Polygon?crs=" + crs_source.authid(), "temp", "memory")
            f = QgsFeature()
            f.setGeometry(geom)
            vl.dataProvider().addFeature(f)
            params = {'INPUT': vl, 'TARGET_CRS': 'EPSG:4326', 'OUTPUT': 'memory:'}
            limite_layer = processing.run("native:reprojectlayer", params, context=context, feedback=feedback)['OUTPUT']

        # Escribir capa de recorte temporalmente para gpd.clip o usar native:clip
        temp_pts = os.path.join(out_folder, "temp_pts.gpkg")
        gdf.to_file(temp_pts, driver="GPKG")
        
        clip_params = {
            'INPUT': temp_pts,
            'OVERLAY': limite_layer,
            'OUTPUT': 'memory:'
        }
        clipped_layer = processing.run("native:clip", clip_params, context=context, feedback=feedback)['OUTPUT']
        
        # Cargar capa recortada a GeoPandas
        features = list(clipped_layer.getFeatures())
        if not features:
            raise QgsProcessingException("No se encontró incendios activos en estas fechas en su área de interés.")

        # Convertir de QgsVectorLayer a GeoDataFrame
        clipped_path_temp = os.path.join(out_folder, "temp_clipped.gpkg")
        processing.run("native:savefeatures", {'INPUT': clipped_layer, 'OUTPUT': clipped_path_temp}, context=context)
        gdf_clip = gpd.read_file(clipped_path_temp)

        # Los cálculos de Fecha y Temp_C ya se hicieron en el paso 2

        # 6. Guardar recorte
        base_name_clip = f"Incendios_Recorte_{self.periodos[periodo_idx].replace(' ', '_')}"
        clip_out_path = os.path.join(out_folder, f"{base_name_clip}{ext}")
        
        if os.path.exists(clip_out_path):
            try:
                os.remove(clip_out_path)
            except:
                pass
                
        # Proyectar y calcular coordenadas X, Y (Este, Norte)
        gdf_save = gdf_clip.copy()
        gdf_save = gdf_save.to_crs(target_crs_str)
        gdf_save['Este'] = gdf_save.geometry.x
        gdf_save['Norte'] = gdf_save.geometry.y
        
        cols_clip = ['latitude', 'longitude', 'brightness', 'confidence', 'Temp_C', 'Fecha_str', 'Este', 'Norte', 'geometry']
        cols_clip = [c for c in cols_clip if c in gdf_save.columns]
        gdf_save = gdf_save[cols_clip]
        
        gdf_save.to_file(clip_out_path, driver="GPKG")
        feedback.pushInfo(f"Capa recortada guardada en: {clip_out_path}")

        # 7. Generar tabla resumen y gráfico
        feedback.pushInfo("Generando tabla resumen y gráfico...")
        resumen = gdf_clip.groupby('Fecha_str').size().reset_index(name='Num_Incendios')
        
        csv_resumen_path = os.path.join(out_folder, f"{base_name_clip}_resumen.csv")
        resumen.to_csv(csv_resumen_path, index=False)

        if not resumen.empty:
            fig, ax = plt.subplots(figsize=(10, 6))
            bars = ax.bar(resumen['Fecha_str'], resumen['Num_Incendios'], color='firebrick')
            
            ax.set_xlabel('Fecha')
            ax.set_ylabel('Número de Incendios')
            ax.set_title('Número de Incendios Detectados por Día')
            
            for bar in bars:
                yval = bar.get_height()
                ax.text(bar.get_x() + bar.get_width()/2.0, yval, int(yval), ha='center', va='bottom')
                
            plt.xticks(rotation=45)
            plt.tight_layout()
            
            png_path = os.path.join(out_folder, f"{base_name_clip}_grafico.png")
            plt.savefig(png_path, dpi=300)
            plt.close(fig)

        # Cargar capas a QGIS
        def apply_fire_symbology(vlayer):
            from qgis.core import QgsMarkerSymbol, QgsSingleSymbolRenderer
            sym = QgsMarkerSymbol.createSimple({
                'name': 'square', 
                'color': '#a9041a', 
                'size': '1.0', 
                'outline_width': '0.2', 
                'outline_color': '#ffffff'
            })
            renderer = QgsSingleSymbolRenderer(sym)
            vlayer.setRenderer(renderer)
            vlayer.triggerRepaint()

        vlayer_region = QgsVectorLayer(region_out_path, base_name_region, "ogr")
        if vlayer_region.isValid():
            apply_fire_symbology(vlayer_region)
            QgsProject.instance().addMapLayer(vlayer_region)
            
        vlayer_clip = QgsVectorLayer(clip_out_path, base_name_clip, "ogr")
        if vlayer_clip.isValid():
            apply_fire_symbology(vlayer_clip)
            QgsProject.instance().addMapLayer(vlayer_clip)

        # Limpiar temporales
        try:
            os.remove(csv_path)
            os.remove(temp_pts)
            os.remove(clipped_path_temp)
        except:
            pass

        return {self.OUT_FOLDER: out_folder}
