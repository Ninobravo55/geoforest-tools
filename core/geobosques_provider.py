import os
from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon

class GeoForestProvider(QgsProcessingProvider):

    def __init__(self):
        super().__init__()

    def loadAlgorithms(self, *args, **kwargs):
        from .geobosques_algorithm import GeobosquesAlgorithm
        from .gfw_algorithm import GfwAlgorithm
        from .glcluc_algorithm import GlclucAlgorithm
        from .early_warning_algorithm import EarlyWarningAlgorithm
        from .report_generator_algorithm import ReportGeneratorAlgorithm
        from .glad_canopy_algorithm import GladCanopyAlgorithm
        from .eth_canopy_algorithm import EthCanopyAlgorithm
        from .meta_canopy_algorithm import MetaCanopyAlgorithm
        from .gedi_canopy_algorithm import GediCanopyAlgorithm
        from .gedi_l2b_algorithm import GediL2BAlgorithm
        from .gedi_l4a_algorithm import GediL4AAlgorithm
        from .gedi_l4b_algorithm import GediL4BAlgorithm
        from .planet_canopy_algorithm import PlanetCanopyAlgorithm
        from .ctrees_agb_algorithm import CtreesAgbAlgorithm
        from .gfw_carbon_flux_algorithm import GfwCarbonFluxAlgorithm
        from .esa_agb_algorithm import EsaAgbAlgorithm
        from .gbif_occurrences_algorithm import GbifOccurrencesAlgorithm
        from .dynamic_world_algorithm import DynamicWorldAlgorithm
        from .firecci_algorithm import FireCCIAlgorithm
        from .active_fire_algorithm import ActiveFireAlgorithm
        from .mapbiomas_c3_algorithm import MapbiomasC3Algorithm
        from .mapbiomas_fire_algorithm import MapbiomasFireAlgorithm
        self.addAlgorithm(GeobosquesAlgorithm())
        self.addAlgorithm(GfwAlgorithm())
        self.addAlgorithm(GlclucAlgorithm())
        self.addAlgorithm(EarlyWarningAlgorithm())
        self.addAlgorithm(GladCanopyAlgorithm())
        self.addAlgorithm(EthCanopyAlgorithm())
        self.addAlgorithm(MetaCanopyAlgorithm())
        self.addAlgorithm(GediCanopyAlgorithm())
        self.addAlgorithm(GediL2BAlgorithm())
        self.addAlgorithm(GediL4AAlgorithm())
        self.addAlgorithm(GediL4BAlgorithm())
        self.addAlgorithm(PlanetCanopyAlgorithm())
        self.addAlgorithm(ReportGeneratorAlgorithm())
        self.addAlgorithm(CtreesAgbAlgorithm())
        self.addAlgorithm(GfwCarbonFluxAlgorithm())
        self.addAlgorithm(EsaAgbAlgorithm())
        self.addAlgorithm(GbifOccurrencesAlgorithm())
        self.addAlgorithm(DynamicWorldAlgorithm())
        self.addAlgorithm(FireCCIAlgorithm())
        self.addAlgorithm(ActiveFireAlgorithm())
        self.addAlgorithm(MapbiomasC3Algorithm())
        self.addAlgorithm(MapbiomasFireAlgorithm())

    def id(self):
        return 'geoforest'

    def name(self):
        return self.tr('GeoForest Tools')

    def icon(self):
        # Asegurarse de que el icono exista en la raíz del plugin
        icon_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'icon.png')
        if os.path.exists(icon_path):
            return QIcon(icon_path)
        return QgsProcessingProvider.icon(self)
