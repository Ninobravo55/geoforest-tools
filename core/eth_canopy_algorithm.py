"""Algoritmo ETH - Altura Dosel 2020 — refactor v2.0 sobre GeeRasterAlgorithm."""

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


class EthCanopyAlgorithm(GeeRasterAlgorithm):
    PRODUCT = 'PRODUCT'
    EPSG = 'EPSG'
    BUFFER_SEG = 10

    def __init__(self):
        super().__init__()
        self.productos_ui = ["ETH Altura dosel 2020", "ETH Desviación estandar"]
        self.productos_id = ["eth_altura_dosel_2020", "eth_desviacion_estandar_2020"]

    def name(self): return 'eth_canopy_analysis'
    def displayName(self): return self.tr('ETH Sentinel2 - Altura Dosel 2020')
    def group(self): return self.tr('Altura Forestal Global')
    def groupId(self): return 'altura_forestal_global'
    def createInstance(self): return EthCanopyAlgorithm()

    def shortHelpString(self):
            help_text = (
                "<b>Descripción del Proceso:</b><br>"
                "Esta herramienta extrae la altura del dosel global a 10m de resolución (basada en Sentinel-2) "
                "empleando Google Earth Engine (GEE). El algoritmo descarga datos en formato Raster "
                "según tu Área de Interés, calcula estadísticas (mínima, máxima, promedio, mediana, desviación estándar) "
                "guardándolas en un archivo Excel local, y aplica una paleta de colores continua automáticamente.<br><br>"
                "<b>Información y Fuente de Datos:</b><br>"
                "Los datos provienen del proyecto de la Universidad ETH Zürich: <i>Global Canopy Height 2020</i>. "
                "Ofrece un mapa continuo de la altura de la copa del bosque y la desviación estándar de la predicción.<br><br>"
                "<b>Fuente y Citación:</b><br>"
                "<a href='https://gee-community-catalog.org/projects/canopy/#citation'>https://gee-community-catalog.org/projects/canopy/#citation</a>"
            )
            return self.tr(help_text)

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterMapLayer(
            'INPUT_LAYER', self.tr('Opción 1: Seleccionar Capa Vectorial (Desde el Panel)'),
            [QgsProcessing.TypeVectorPolygon], optional=True))
        self.addParameter(QgsProcessingParameterExtent(
            'INPUT_EXTENT', self.tr('Opción 2: O dibujar un Recuadro'), optional=True))
        self.addParameter(QgsProcessingParameterEnum(
            self.PRODUCT, self.tr('Producto ETH'), options=self.productos_ui, defaultValue=0))
        self.addParameter(QgsProcessingParameterCrs(
            self.EPSG, self.tr('CRS de Destino (Solo reproyecta vector)'),
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
        if producto == "eth_altura_dosel_2020":
            asset_id = 'users/nlang/ETH_GlobalCanopyHeight_2020_10m_v1'
            label = 'AlturaETH'; self._cmin, self._cmax = 0, 50
            self._palette = ['#010005','#150b37','#3b0964','#61136e','#85216b','#a92e5e',
                             '#cc4248','#e75e2e','#f78410','#fcae12','#f5db4c','#fcffa4']
        else:
            asset_id = 'users/nlang/ETH_GlobalCanopyHeightSD_2020_10m_v1'
            label = 'StdDevETH'; self._cmin, self._cmax = 0, 15
            self._palette = ['#0d0406','#241628','#36274d','#403a76','#3d5296','#366da0',
                             '#3488a6','#36a2ab','#44bcad','#6dd3ad','#aee3c0','#def5e5']
        img = ee.Image(asset_id).select(0).rename(label)
        return RasterJob(img, f"ETH_{producto}", label, 10, 'GEE_Bosques_ETH')

    def apply_symbology(self, rlayer, job):
        fnc = QgsColorRampShader(); fnc.setColorRampType(QgsColorRampShader.Interpolated)
        n = len(self._palette); lst = []
        for i, hex_col in enumerate(self._palette):
            val = self._cmin + (self._cmax - self._cmin) * (i / (n - 1))
            lbl = f"{val:.1f} m" if self._cmax == 50 else f"{val:.2f}"
            lst.append(QgsColorRampShader.ColorRampItem(val, QColor(hex_col), lbl))
        fnc.setColorRampItemList(lst)
        shader = QgsRasterShader(); shader.setRasterShaderFunction(fnc)
        r = QgsSingleBandPseudoColorRenderer(rlayer.dataProvider(), 1, shader)
        r.setClassificationMin(self._cmin); r.setClassificationMax(self._cmax)
        rlayer.setRenderer(r)
