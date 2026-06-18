"""Algoritmo Meta 1m - Altura Dosel — refactor v2.0 sobre GeeRasterAlgorithm.

Caso especial: clasifica la altura continua en 9 clases (0-8), calcula
ÁREA por clase (no min/max), descarga como Byte y usa rampa Exact.
Sobrescribe build_export_image, compute_statistics y prepare_for_export.
"""

import os

from qgis.core import (
    QgsProcessing,
    QgsProcessingParameterMapLayer,
    QgsProcessingParameterExtent,
    QgsProcessingParameterEnum,
    QgsProcessingParameterCrs,
    QgsProcessingParameterFolderDestination,
    QgsProcessingParameterString,
    QgsColorRampShader,
    QgsRasterShader,
    QgsSingleBandPseudoColorRenderer,
)
from qgis.PyQt.QtGui import QColor

from .base.gee_raster_algorithm import GeeRasterAlgorithm, RasterJob


class MetaCanopyAlgorithm(GeeRasterAlgorithm):
    PRODUCT = 'PRODUCT'
    EPSG = 'EPSG'
    SCALE = 'SCALE'
    BUFFER_SEG = 10

    CLASS_NAMES = {
        0: "0 - 1m (Sin dosel / Vegetación baja)", 1: "1 - 3m", 2: "3 - 5m",
        3: "5 - 8m", 4: "8 - 10m", 5: "10 - 15m", 6: "15 - 20m",
        7: "20 - 30m", 8: "> 30m",
    }

    def __init__(self):
        super().__init__()
        self.productos_ui = ["Altura global 1m Meta"]
        self.productos_id = ["meta_altura_dosel_1m"]
        self.scales_ui = ["1 m (Nativa, solo áreas pequeñas)", "5 m", "10 m", "30 m"]
        self.scales_val = [1, 5, 10, 30]

    def name(self): return 'meta_canopy_analysis'
    def displayName(self): return self.tr('Meta 1m - Altura Dosel 2018 - 2020')
    def group(self): return self.tr('Altura Forestal Global')
    def groupId(self): return 'altura_forestal_global'
    def createInstance(self): return MetaCanopyAlgorithm()

    def shortHelpString(self):
            help_text = (
                "<b>Descripción del Proceso:</b><br>"
                "Esta herramienta extrae la altura del dosel global a 1m de Meta "
                "empleando Google Earth Engine (GEE). El algoritmo re-clasifica el raster en 8 clases de altura "
                "(1-3m, 3-5m, etc.) para optimizar drásticamente su peso de descarga. "
                "Además, genera automáticamente un archivo Excel con el Área en Hectáreas para cada clase.<br><br>"
                "<b>Información y Fuente de Datos:</b><br>"
                "Los datos provienen del proyecto Meta & World Resources Institute (WRI): <i>Global Canopy Height</i>. "
                "Ofrece un mapa de la altura de la copa del bosque a 1 metro de resolución, basado en modelos de IA.<br><br>"
                "<b>Fuente y Citación:</b><br>"
                "<a href='https://gee-community-catalog.org/projects/meta_trees/'>https://gee-community-catalog.org/projects/meta_trees/</a>"
            )
            return self.tr(help_text)

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterMapLayer(
            'INPUT_LAYER', self.tr('Opción 1: Seleccionar Capa Vectorial (Desde el Panel)'),
            [QgsProcessing.TypeVectorPolygon], optional=True))
        self.addParameter(QgsProcessingParameterExtent(
            'INPUT_EXTENT', self.tr('Opción 2: O dibujar un Recuadro'), optional=True))
        self.addParameter(QgsProcessingParameterEnum(
            self.PRODUCT, self.tr('Producto Meta'), options=self.productos_ui, defaultValue=0))
        self.addParameter(QgsProcessingParameterCrs(
            self.EPSG, self.tr('CRS de Destino'), defaultValue='EPSG:32718', optional=True))
        self.addParameter(QgsProcessingParameterEnum(
            self.SCALE, self.tr('Resolución Espacial de Descarga (Sube esto si se envía a Drive)'),
            options=self.scales_ui, defaultValue=0))
        self.addParameter(QgsProcessingParameterEnum(
            'EXPORT_METHOD', self.tr('Método de Exportación'),
            options=["Descarga Directa (Raster)", "Exportar a Google Drive",
                     "Exportar a Google Cloud Storage (GCS)"], defaultValue=0))
        self.addParameter(QgsProcessingParameterFolderDestination(
            'OUT_FOLDER', self.tr('Carpeta Local de Destino (Obligatoria para Estadísticas Excel)'),
            optional=False))
        self.addParameter(QgsProcessingParameterString(
            'GCS_BUCKET', self.tr('Nombre del Bucket GCS (Solo para GCS)'), optional=True))

    def build_export_image(self, ee, parameters, context, geom_ee_buffer, feedback):
        idx = self.parameterAsEnum(parameters, self.PRODUCT, context)
        producto = self.productos_id[idx]
        scale_idx = self.parameterAsEnum(parameters, self.SCALE, context)
        scale_val = self.scales_val[scale_idx]

        asset_id = 'projects/sat-io/open-datasets/facebook/meta-canopy-height'
        img_band = ee.ImageCollection(asset_id).mosaic().select(0)
        img_classified = (ee.Image(0).byte()
            .where(img_band.gt(1), 1).where(img_band.gt(3), 2)
            .where(img_band.gt(5), 3).where(img_band.gt(8), 4)
            .where(img_band.gt(10), 5).where(img_band.gt(15), 6)
            .where(img_band.gt(20), 7).where(img_band.gt(30), 8)
            .rename('Clase_Altura'))
        return RasterJob(img_classified, f"META_{producto}_Clasificado",
                         'Clase_Altura', scale_val, 'GEE_Bosques_META')

    def prepare_for_export(self, image):
        return image.toByte()

    def compute_statistics(self, ee, image, region, job, out_dir, feedback):
        import pandas as pd
        try:
            area_image = ee.Image.pixelArea().addBands(image)
            stats = area_image.reduceRegion(
                reducer=ee.Reducer.sum().group(groupField=1, groupName='class'),
                geometry=region, scale=job.scale, maxPixels=int(1e13),
            ).getInfo()
            areas_ha = {k: 0.0 for k in self.CLASS_NAMES}
            for g in stats.get('groups', []):
                c = int(g['class'])
                if c in areas_ha:
                    areas_ha[c] = g['sum'] / 10000.0
            df = pd.DataFrame({
                "Valor Raster (Clase)": list(self.CLASS_NAMES.keys()),
                "Rango de Altura": list(self.CLASS_NAMES.values()),
                "Área (Hectáreas)": [areas_ha[k] for k in self.CLASS_NAMES],
            })
            excel_path = os.path.join(out_dir, f"{job.out_filename}_Areas.xlsx")
            df.to_excel(excel_path, index=False)
            feedback.pushInfo(f"Archivo Excel de Áreas generado: {excel_path}")
        except Exception as stat_err:
            feedback.reportError(
                f"Error al calcular áreas en GEE: {stat_err}", fatalError=False)

    def apply_symbology(self, rlayer, job):
        C = QgsColorRampShader.ColorRampItem
        fnc = QgsColorRampShader(); fnc.setColorRampType(QgsColorRampShader.Exact)
        fnc.setColorRampItemList([
            C(0, QColor('#f5f5f5'), '0 - 1m'), C(1, QColor('#ffffe5'), '1 - 3m'),
            C(2, QColor('#f7fcb9'), '3 - 5m'), C(3, QColor('#d9f0a3'), '5 - 8m'),
            C(4, QColor('#addd8e'), '8 - 10m'), C(5, QColor('#78c679'), '10 - 15m'),
            C(6, QColor('#41ab5d'), '15 - 20m'), C(7, QColor('#238443'), '20 - 30m'),
            C(8, QColor('#005a32'), '> 30m')])
        shader = QgsRasterShader(); shader.setRasterShaderFunction(fnc)
        r = QgsSingleBandPseudoColorRenderer(rlayer.dataProvider(), 1, shader)
        r.setClassificationMin(0); r.setClassificationMax(8)
        rlayer.setRenderer(r)
