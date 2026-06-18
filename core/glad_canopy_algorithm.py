"""Algoritmo GLAD - Altura Dosel — refactor v2.0 sobre GeeRasterAlgorithm.

Toda la mecánica (AOI, init GEE, buffer, export Drive/GCS, descarga local,
fallback por área grande, carga en QGIS) vive en la base. Aquí solo: el
asset, las máscaras, la rampa de color y (si aplica) las estadísticas.
"""

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


class GladCanopyAlgorithm(GeeRasterAlgorithm):
    PRODUCT = 'PRODUCT'
    EPSG = 'EPSG'
    BUFFER_SEG = 30

    def __init__(self):
        super().__init__()
        self.productos_ui = ["GLAD Altura dosel 2000", "GLAD Altura dosel 2020"]
        self.productos_id = ["altura_foresta_2000", "altura_foresta_2020"]

    def name(self): return 'glad_canopy_analysis'
    def displayName(self): return self.tr('GLAD - Altura Dosel 2000 - 2020')
    def group(self): return self.tr('Altura Forestal Global')
    def groupId(self): return 'altura_forestal_global'
    def createInstance(self): return GladCanopyAlgorithm()

    def shortHelpString(self):
            help_text = (
                "<b>Descripción del Proceso:</b><br>"
                "Esta herramienta extrae la altura del dosel empleando Google Earth Engine (GEE). "
                "El algoritmo descarga datos satelitales en formato Raster según tu Área de Interés, "
                "y calcula automáticamente estadísticas (mínima, máxima, promedio, mediana, desviación estándar) "
                "guardándolas en un archivo Excel local.<br><br>"
                "<b>Información y Fuente de Datos:</b><br>"
                "Los datos utilizados provienen del proyecto <a href='https://glad.umd.edu/dataset/GLCLUC2020/'><b>Global Land Cover and Land Use Change (GLCLUC) 2020</b></a>, "
                "desarrollado por el laboratorio GLAD de la Universidad de Maryland (UMD). "
                "Permite evaluar la altura de la copa del bosque en el año 2000 y 2020."
            )
            return self.tr(help_text)

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterMapLayer(
            'INPUT_LAYER', self.tr('Opción 1: Seleccionar Capa Vectorial (Desde el Panel)'),
            [QgsProcessing.TypeVectorPolygon], optional=True))
        self.addParameter(QgsProcessingParameterExtent(
            'INPUT_EXTENT', self.tr('Opción 2: O dibujar un Recuadro'), optional=True))
        self.addParameter(QgsProcessingParameterEnum(
            self.PRODUCT, self.tr('Producto GLAD Altura'), options=self.productos_ui, defaultValue=0))
        self.addParameter(QgsProcessingParameterCrs(
            self.EPSG, self.tr('CRS de Destino (Solo reproyecta si es vector)'),
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
        idx = self.parameterAsEnum(parameters, self.PRODUCT, context)
        producto = self.productos_id[idx]
        if producto == "altura_foresta_2000":
            asset_id = 'projects/glad/GLCLU2020/Forest_height_2000'; label = 'Altura2000'
        else:
            asset_id = 'projects/glad/GLCLU2020/Forest_height_2020'; label = 'Altura2020'
        img = ee.Image(asset_id).select(0).rename(label)
        img = img.updateMask(img.gt(0))
        return RasterJob(img, f"GLAD_{producto}", label, 30, 'GEE_Bosques_GLAD')

    def apply_symbology(self, rlayer, job):
        rlayer.dataProvider().setNoDataValue(1, 0)
        fnc = QgsColorRampShader(); fnc.setColorRampType(QgsColorRampShader.Discrete)
        C = QgsColorRampShader.ColorRampItem
        fnc.setColorRampItemList([
            C(5, QColor('#ffffe5'), '1 - 5 m'), C(10, QColor('#addd8e'), '5 - 10 m'),
            C(20, QColor('#78c679'), '10 - 20 m'), C(30, QColor('#31a354'), '20 - 30 m'),
            C(40, QColor('#006837'), '> 30 m')])
        shader = QgsRasterShader(); shader.setRasterShaderFunction(fnc)
        r = QgsSingleBandPseudoColorRenderer(rlayer.dataProvider(), 1, shader)
        r.setClassificationMin(1); r.setClassificationMax(100)
        rlayer.setRenderer(r)
