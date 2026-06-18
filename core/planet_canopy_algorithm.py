"""Algoritmo Planet NICFI - Altura dosel — refactor v2.0 sobre GeeRasterAlgorithm."""

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


class PlanetCanopyAlgorithm(GeeRasterAlgorithm):
    PRODUCT = 'PRODUCT'
    EPSG = 'EPSG'
    BUFFER_SEG = 30

    def __init__(self):
        super().__init__()
        self.productos_ui = ["Planet NICFI Altura dosel 4.7m Amazonía"]
        self.productos_id = ["planet_nicfi_amazon_canopy"]

    def name(self): return 'planet_canopy_analysis'
    def displayName(self): return self.tr('Planet NICFI - Altura dosel Amazonía')
    def group(self): return self.tr('Altura Forestal Global')
    def groupId(self): return 'altura_forestal_global'
    def createInstance(self): return PlanetCanopyAlgorithm()

    def shortHelpString(self):
            help_text = (
                "<b>Descripción del Proceso:</b><br>"
                "Esta herramienta proporciona la altura del dosel arbóreo de alta resolución (~4.78 m) de la selva amazónica, "
                "como una media para el período 2020-2024. Se generó utilizando un modelo de aprendizaje profundo U-Net adaptado "
                "para regresión, entrenado con imágenes satelitales Planet NICFI e informado por modelos de altura del dosel (CHM) "
                "derivados de LiDAR.<br><br>"
                "El modelo predice la altura media de la copa de los árboles estimando con éxito alturas de hasta 40-50 m. "
                "El estudio determinó que la selva amazónica tiene una altura promedio de dosel de aproximadamente 22 m.<br><br>"
                "La herramienta extraerá estos datos, calculará estadísticas automáticamente (mínima, máxima, promedio, mediana, desviación estándar) "
                "y las guardará en Excel, permitiendo descargar el Raster directamente o exportarlo a la nube.<br><br>"
                "<b>Fuente Oficial:</b> <a href='https://gee-community-catalog.org/projects/ctrees_amazon/'>GEE Community Catalog - CTrees Amazon Canopy</a>"
            )
            return self.tr(help_text)

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterMapLayer(
            'INPUT_LAYER', self.tr('Opción 1: Seleccionar Capa Vectorial (Desde el Panel)'),
            [QgsProcessing.TypeVectorPolygon], optional=True))
        self.addParameter(QgsProcessingParameterExtent(
            'INPUT_EXTENT', self.tr('Opción 2: O dibujar un Recuadro'), optional=True))
        self.addParameter(QgsProcessingParameterEnum(
            self.PRODUCT, self.tr('Producto'), options=self.productos_ui, defaultValue=0))
        self.addParameter(QgsProcessingParameterCrs(
            self.EPSG, self.tr('CRS de Destino (Solo reproyecta localmente)'),
            defaultValue='EPSG:32718', optional=True))
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
        asset_id = "projects/sat-io/open-datasets/CTREES/AMAZON-CANOPY-TREE-HT"
        label = 'Altura_m'
        feedback.pushInfo(f"Cargando dataset: {asset_id}")
        img = ee.Image(asset_id).divide(2.5).rename(label)
        return RasterJob(img, "Planet_NICFI_Altura_Amazonia", label, 4.78,
                         'GEE_Bosques_Planet_NICFI')

    def apply_symbology(self, rlayer, job):
        fnc = QgsColorRampShader(); fnc.setColorRampType(QgsColorRampShader.Interpolated)
        C = QgsColorRampShader.ColorRampItem
        fnc.setColorRampItemList([
            C(0, QColor('#000000'), '0 m'), C(4.37, QColor('#1a0033'), '4 m'),
            C(8.75, QColor('#330066'), '8 m'), C(13.12, QColor('#004d66'), '13 m'),
            C(17.5, QColor('#006666'), '17 m'), C(21.87, QColor('#009966'), '22 m'),
            C(26.25, QColor('#33cc66'), '26 m'), C(30.62, QColor('#66ff33'), '30 m'),
            C(35, QColor('#ccff00'), '>= 35 m')])
        shader = QgsRasterShader(); shader.setRasterShaderFunction(fnc)
        rlayer.setRenderer(QgsSingleBandPseudoColorRenderer(rlayer.dataProvider(), 1, shader))
