from qgis.PyQt.QtWidgets import QAction, QMenu
from qgis.PyQt.QtCore import QCoreApplication
from qgis.PyQt.QtGui import QIcon
from .utils.qt_compat import dialog_exec
import os

class GeoForestToolsPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.action = None
        self.menu = self.tr('&GeoForest Tools')
        
    def tr(self, message):
        return QCoreApplication.translate('GeoForestTools', message)

    def initGui(self):
        from qgis.core import QgsApplication
        from .core.geobosques_provider import GeoForestProvider
        from .utils.gee_init import background_gee_initialize
        
        self.provider = GeoForestProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)
        
        # Inicialización silenciosa de GEE en segundo plano
        background_gee_initialize()
        
        # ── Iconos ─────────────────────────────────────────────────────
        icon_path  = os.path.join(self.plugin_dir, 'icon.png')
        icons_dir  = os.path.join(self.plugin_dir, 'iconos')
        icon_monitoreo     = QIcon(os.path.join(icons_dir, 'Monitoreo.svg'))
        icon_alerta        = QIcon(os.path.join(icons_dir, 'Alerta.svg'))
        icon_altura        = QIcon(os.path.join(icons_dir, 'Altura_forestal.svg'))
        icon_biomasa       = QIcon(os.path.join(icons_dir, 'Biomasa.svg'))
        icon_biodiversidad = QIcon(os.path.join(icons_dir, 'Biodiversidad.svg'))
        icon_dinamica      = QIcon(os.path.join(icons_dir, 'Dinamica.svg'))
        icon_tools         = QIcon(os.path.join(icons_dir, 'Herramienta.svg'))
        icon_main          = QIcon(icon_path)
        
        # ── Acciones — Monitoreo de Bosques ────────────────────────────
        self.action = QAction(icon_monitoreo, self.tr('Monitoreo GEOBOSQUES Perú'), self.iface.mainWindow())
        self.action.setObjectName('GeoForestToolsAction')
        self.action.triggered.connect(self.run)
        
        self.action_gfw = QAction(icon_monitoreo, self.tr('Análisis Global Forest Watch'), self.iface.mainWindow())
        self.action_gfw.setObjectName('GeoForestToolsGfwAction')
        self.action_gfw.triggered.connect(self.run_gfw)
        
        self.action_glcluc = QAction(icon_monitoreo, self.tr('Cambio global de la cobertura 2000 - 2020'), self.iface.mainWindow())
        self.action_glcluc.setObjectName('GeoForestToolsGlclucAction')
        self.action_glcluc.triggered.connect(self.run_glcluc)
        
        # ── Acciones — Alerta de Perturbación ─────────────────────────
        self.action_early_warning = QAction(icon_alerta, self.tr('Alerta temprana Deforestación GLAD-L/S2/S1'), self.iface.mainWindow())
        self.action_early_warning.setObjectName('GeoForestToolsEarlyWarningAction')
        self.action_early_warning.triggered.connect(self.run_early_warning)
        
        # ── Acciones — Altura Forestal Global ─────────────────────────
        self.action_glad = QAction(icon_altura, self.tr('GLAD - Altura Dosel 2000 - 2020'), self.iface.mainWindow())
        self.action_glad.triggered.connect(self.run_glad)
        
        self.action_meta = QAction(icon_altura, self.tr('Meta 1m - Altura Dosel 2018 - 2020'), self.iface.mainWindow())
        self.action_meta.triggered.connect(self.run_meta)
        
        self.action_eth = QAction(icon_altura, self.tr('ETH Sentinel2 - Altura Dosel 2020'), self.iface.mainWindow())
        self.action_eth.triggered.connect(self.run_eth)
        
        self.action_gedi = QAction(icon_altura, self.tr('GEDI L2A - Altura Forestal'), self.iface.mainWindow())
        self.action_gedi.triggered.connect(self.run_gedi)
        
        self.action_planet = QAction(icon_altura, self.tr('Planet NICFI - Altura Dosel'), self.iface.mainWindow())
        self.action_planet.triggered.connect(self.run_planet)
        
        # ── Acciones — Biomasa Forestal Global ────────────────────────
        self.action_gedi_l2b = QAction(icon_biomasa, self.tr('GEDI L2B - Estructura Vertical y Métrica de Cobertura'), self.iface.mainWindow())
        self.action_gedi_l2b.triggered.connect(self.run_gedi_l2b)
        
        self.action_gedi_l4a = QAction(icon_biomasa, self.tr('GEDI L4A AGBD - Densidad de biomasa aérea'), self.iface.mainWindow())
        self.action_gedi_l4a.triggered.connect(self.run_gedi_l4a)
        
        self.action_gedi_l4b = QAction(icon_biomasa, self.tr('GEDI L4B - Biomasa Aérea Media (AGBD) 1km'), self.iface.mainWindow())
        self.action_gedi_l4b.triggered.connect(self.run_gedi_l4b)
        
        self.action_ctrees = QAction(icon_biomasa, self.tr('CTrees - Biomasa Aérea Global'), self.iface.mainWindow())
        self.action_ctrees.triggered.connect(self.run_ctrees)
        
        self.action_gfw_carbon = QAction(icon_biomasa, self.tr('GFW - Flujo de Carbono Forestal'), self.iface.mainWindow())
        self.action_gfw_carbon.triggered.connect(self.run_gfw_carbon)
        
        self.action_esa_agb = QAction(icon_biomasa, self.tr('ESA CCI - Biomasa Aérea'), self.iface.mainWindow())
        self.action_esa_agb.triggered.connect(self.run_esa_agb)
        
        # ── Acciones — Dinámica de Cobertura ────────────────────────
        self.action_dynamic_world = QAction(icon_dinamica, self.tr('Dynamic World - Cobertura Global NRT'), self.iface.mainWindow())
        self.action_dynamic_world.triggered.connect(self.run_dynamic_world)
        
        self.action_mapbiomas_c3 = QAction(icon_dinamica, self.tr('MapBiomas cobertura Perú C3'), self.iface.mainWindow())
        self.action_mapbiomas_c3.triggered.connect(self.run_mapbiomas_c3)
        
        # ── Acciones — Monitoreo de Incendios ────────────────────────
        self.action_firecci = QAction(icon_alerta, self.tr('ESA FireCCI - Área Quemada'), self.iface.mainWindow())
        self.action_firecci.triggered.connect(self.run_firecci)
        
        self.action_active_fire = QAction(icon_alerta, self.tr('FIRMS - Incendios Activos NRT'), self.iface.mainWindow())
        self.action_active_fire.triggered.connect(self.run_active_fire)
        
        self.action_mapbiomas_fire = QAction(icon_alerta, self.tr('MapBiomas Perú - Monitoreo de Incendios'), self.iface.mainWindow())
        self.action_mapbiomas_fire.triggered.connect(self.run_mapbiomas_fire)
        
        # ── Acciones — Herramientas Complementarias ───────────────────
        self.action_reports = QAction(icon_tools, self.tr('Generador de Reportes y Gráficos (Local)'), self.iface.mainWindow())
        self.action_reports.setObjectName('GeoForestToolsReportsAction')
        self.action_reports.triggered.connect(self.run_reports)
        
        # ── Acciones — Biodiversidad ───────────────────────────────────
        self.action_gbif = QAction(icon_biodiversidad, self.tr('GBIF - Ocurrencias de Especies'), self.iface.mainWindow())
        self.action_gbif.setObjectName('GeoForestToolsGbifAction')
        self.action_gbif.triggered.connect(self.run_gbif)
        
        # ── Acciones — GEE (sin grupo propio) ─────────────────────────
        self.action_auth = QAction(icon_main, self.tr('Autenticación GEE'), self.iface.mainWindow())
        self.action_auth.triggered.connect(self.run_auth)
        
        self.action_deps = QAction(icon_main, self.tr('Instalar Dependencias GEE'), self.iface.mainWindow())
        self.action_deps.triggered.connect(self.run_deps)
        
        # ── Menú principal ─────────────────────────────────────────────
        self.main_menu = QMenu(self.tr('GeoForest Tools'), self.iface.mainWindow())
        self.main_menu.setIcon(icon_main)
        self.iface.pluginMenu().addMenu(self.main_menu)
        
        # Monitoreo de Bosques
        self.submenu_monitoreo = self.main_menu.addMenu(icon_monitoreo, self.tr('Monitoreo de Bosques'))
        self.submenu_monitoreo.addAction(self.action)
        self.submenu_monitoreo.addAction(self.action_gfw)
        self.submenu_monitoreo.addAction(self.action_glcluc)
        
        # Alerta de Perturbación
        self.submenu_alerta_perturbacion = self.main_menu.addMenu(icon_alerta, self.tr('Alerta de Perturbación'))
        self.submenu_alerta_perturbacion.addAction(self.action_early_warning)
        
        # Altura Forestal Global
        self.submenu_altura = self.main_menu.addMenu(icon_altura, self.tr('Altura Forestal Global'))
        self.submenu_altura.addAction(self.action_glad)
        self.submenu_altura.addAction(self.action_meta)
        self.submenu_altura.addAction(self.action_eth)
        self.submenu_altura.addAction(self.action_gedi)
        self.submenu_altura.addAction(self.action_planet)
        
        # Biomasa Forestal Global
        self.submenu_biomasa = self.main_menu.addMenu(icon_biomasa, self.tr('Biomasa Forestal Global'))
        self.submenu_biomasa.addAction(self.action_gedi_l2b)
        self.submenu_biomasa.addAction(self.action_gedi_l4a)
        self.submenu_biomasa.addAction(self.action_gedi_l4b)
        self.submenu_biomasa.addAction(self.action_ctrees)
        self.submenu_biomasa.addAction(self.action_gfw_carbon)
        self.submenu_biomasa.addAction(self.action_esa_agb)
        
        # Dinámica Cobertura
        self.submenu_dinamica = self.main_menu.addMenu(icon_dinamica, self.tr('Dinámica de Cobertura'))
        self.submenu_dinamica.addAction(self.action_dynamic_world)
        self.submenu_dinamica.addAction(self.action_mapbiomas_c3)
        
        # Monitoreo de Incendios
        self.submenu_incendios = self.main_menu.addMenu(icon_alerta, self.tr('Monitoreo de Incendios'))
        self.submenu_incendios.addAction(self.action_firecci)
        self.submenu_incendios.addAction(self.action_active_fire)
        self.submenu_incendios.addAction(self.action_mapbiomas_fire)
        
        # Herramientas Complementarias
        self.submenu_tools = self.main_menu.addMenu(icon_tools, self.tr('Herramientas Complementarias'))
        self.submenu_tools.addAction(self.action_reports)
        
        # Biodiversidad
        self.submenu_biodiversidad = self.main_menu.addMenu(icon_biodiversidad, self.tr('Biodiversidad'))
        self.submenu_biodiversidad.addAction(self.action_gbif)
        
        self.main_menu.addSeparator()
        self.main_menu.addAction(self.action_auth)
        self.main_menu.addAction(self.action_deps)


    def unload(self):
        from qgis.core import QgsApplication
        if hasattr(self, 'provider'):
            QgsApplication.processingRegistry().removeProvider(self.provider)
        if hasattr(self, 'main_menu'):
            self.iface.pluginMenu().removeAction(self.main_menu.menuAction())

    def check_dependencies(self):
        missing = []
        try:
            pass
        except ImportError:
            missing.append("earthengine-api")
        try:
            pass
        except ImportError:
            missing.append("geopandas")
        try:
            pass
        except ImportError:
            missing.append("matplotlib")
        try:
            pass
        except ImportError:
            missing.append("pandas")
        try:
            pass
        except ImportError:
            missing.append("openpyxl")
        try:
            pass
        except ImportError:
            missing.append("shapely")
            
        if missing:
            from .gui.install_deps_dialog import InstallDepsDialog
            dlg = InstallDepsDialog(missing)
            dialog_exec(dlg)
            return False
        return True

    def run_deps(self):
        from qgis.PyQt.QtWidgets import QMessageBox
        # Verificar qué librerías ya están instaladas
        deps_to_check = {
            "earthengine-api": "ee",
            "geopandas": "geopandas",
            "matplotlib": "matplotlib",
            "pandas": "pandas",
            "openpyxl": "openpyxl",
            "shapely": "shapely"
        }
        installed = []
        missing = []
        for pip_name, import_name in deps_to_check.items():
            try:
                __import__(import_name)
                installed.append(pip_name)
            except ImportError:
                missing.append(pip_name)

        if not missing:
            QMessageBox.information(
                self.iface.mainWindow(),
                "GeoForest Tools — Dependencias",
                "✅ <b>Todas las dependencias están instaladas y actualizadas.</b><br><br>"
                "Librerías verificadas:<br>"
                + "".join(f"  • <span style='color:#2E8B57;'>{d}</span><br>" for d in installed)
                + "<br>No es necesario instalar nada."
            )
            return

        from .gui.install_deps_dialog import InstallDepsDialog
        dlg = InstallDepsDialog(missing, installed_deps=installed)
        dialog_exec(dlg)
        
    def run_auth(self):
        from .gui.gee_auth_dialog import GEEAuthDialog
        dlg = GEEAuthDialog(self.iface.mainWindow())
        dialog_exec(dlg)

    def run(self):
        if not self.check_dependencies():
            return
        import processing
        processing.execAlgorithmDialog('geoforest:geobosques_analysis')
        
    def run_gfw(self):
        if not self.check_dependencies():
            return
        import processing
        processing.execAlgorithmDialog('geoforest:gfw_analysis')

    def run_glcluc(self):
        if not self.check_dependencies():
            return
        import processing
        processing.execAlgorithmDialog('geoforest:glcluc_analysis')

    def run_early_warning(self):
        if not self.check_dependencies():
            return
        import processing
        processing.execAlgorithmDialog('geoforest:early_warning_analysis')

    def run_reports(self):
        if not self.check_dependencies():
            return
        import processing
        processing.execAlgorithmDialog('geoforest:report_generator')

    def run_glad(self):
        if not self.check_dependencies(): return
        import processing
        processing.execAlgorithmDialog('geoforest:glad_canopy_analysis')

    def run_meta(self):
        if not self.check_dependencies(): return
        import processing
        processing.execAlgorithmDialog('geoforest:meta_canopy_analysis')

    def run_eth(self):
        if not self.check_dependencies(): return
        import processing
        processing.execAlgorithmDialog('geoforest:eth_canopy_analysis')

    def run_gedi(self):
        if not self.check_dependencies(): return
        import processing
        processing.execAlgorithmDialog('geoforest:gedi_canopy_analysis')

    def run_gedi_l2b(self):
        if not self.check_dependencies(): return
        import processing
        processing.execAlgorithmDialog('geoforest:gedi_l2b_analysis')

    def run_gedi_l4a(self):
        if not self.check_dependencies(): return
        import processing
        processing.execAlgorithmDialog('geoforest:gedi_l4a_analysis')

    def run_gedi_l4b(self):
        if not self.check_dependencies(): return
        import processing
        processing.execAlgorithmDialog('geoforest:gedi_l4b_analysis')

    def run_planet(self):
        if not self.check_dependencies(): return
        import processing
        processing.execAlgorithmDialog('geoforest:planet_canopy_analysis')

    def run_ctrees(self):
        if not self.check_dependencies(): return
        import processing
        processing.execAlgorithmDialog('geoforest:ctrees_agb_analysis')

    def run_gfw_carbon(self):
        if not self.check_dependencies(): return
        import processing
        processing.execAlgorithmDialog('geoforest:gfw_carbon_flux_analysis')

    def run_esa_agb(self):
        if not self.check_dependencies(): return
        import processing
        processing.execAlgorithmDialog('geoforest:esa_agb_analysis')

    def run_dynamic_world(self):
        if not self.check_dependencies(): return
        import processing
        processing.execAlgorithmDialog('geoforest:dynamic_world_analysis')

    def run_mapbiomas_c3(self):
        if not self.check_dependencies(): return
        import processing
        processing.execAlgorithmDialog('geoforest:mapbiomas_c3_analysis')

    def run_firecci(self):
        if not self.check_dependencies(): return
        import processing
        processing.execAlgorithmDialog('geoforest:firecci_analysis')

    def run_active_fire(self):
        if not self.check_dependencies(): return
        import processing
        processing.execAlgorithmDialog('geoforest:active_fire_analysis')

    def run_mapbiomas_fire(self):
        if not self.check_dependencies(): return
        import processing
        processing.execAlgorithmDialog('geoforest:mapbiomas_fire_analysis')

    def run_gbif(self):
        """Descarga de ocurrencias GBIF — no requiere dependencias GEE."""
        import processing
        processing.execAlgorithmDialog('geoforest:gbif_occurrences')
